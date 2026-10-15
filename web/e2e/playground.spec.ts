import { test, expect } from '@playwright/test';
import { reconstructEvidence } from '../src/playground/evidence';
import { cognitiveCycle, cycleStops, firstWorkingCycle } from '../src/playground/cycle';
import { visitorOptions } from '../src/playground/queueEvidence';
import { architectureStops } from '../src/playground/ArchitectureWorld';

test.describe.configure({ mode: 'serial' });
const captureDirectory = process.env.PREACT_E2E_ARTIFACT_DIR || '../.cache/osc-browser-artifacts';

test('an unexecuted manifest entry is never counted as learned experience', async ({ request }) => {
  const saved = await (await request.get('/api/playground/sessions/queue-stable_high-learned-11')).json();
  const experience = saved.events.find((e: any) => e.event.kind === 'learning_experience');
  const prefix = structuredClone(saved.events.slice(0, experience.index));
  prefix[prefix.length - 1].event.data.runs.push('unexecuted-round');
  expect(reconstructEvidence(prefix).learned).toBe(1);
  for (const event of prefix) if (event.event.kind === 'outcome') event.receipt_verified = false;
  expect(reconstructEvidence(prefix).learned).toBe(0);
});

test('one-round projections keep observed facts, model use and receipt feedback separate', async ({ request }) => {
  const saved = await (await request.get('/api/playground/sessions/queue-stable_high-learned-11')).json();
  const end = firstWorkingCycle(saved.events);
  const prefix = saved.events.slice(0, end);
  const cycle = cognitiveCycle(prefix);
  expect(cycle.belief.observed).toEqual(cycle.trace.state);
  expect(cycle.model.pHigh).toBe(cycle.model.prediction.raw.p_high);
  expect(cycle.model.version).toBe(cycle.model.prediction.raw.base_model_version);
  expect(cycle.model.prediction.successor).toBeNull();
  expect(cycle.model.prediction.future_states).toEqual([]);
  expect(cycle.feedback.length).toBeGreaterThan(0);
  for (const receipt of cycle.feedback) expect(cycle.trace.memory_references).toContain(receipt.reference);
  expect(cycle.selected.fingerprint).toBe(cycle.selection!.event.data.action_fingerprint);
  expect(cycle.outcome!.event.data.observation.state.payload.tick).toBe(cycle.observed!.event.data.state.payload.tick + 1);
  const unconfirmed = structuredClone(prefix);
  for (const item of unconfirmed) item.receipt_verified = false;
  expect(cognitiveCycle(unconfirmed).feedback).toEqual([]);
  expect(cognitiveCycle(unconfirmed).outcome).toBeUndefined();
  const next = saved.events.find((e: any) => e.event.kind === 'observed' && e.index > end);
  const fresh = cognitiveCycle(saved.events.slice(0, next.index));
  expect(fresh.run).not.toBe(cycle.run);
  expect(fresh.trace).toBeUndefined();
  expect(fresh.selected).toBeUndefined();
  expect(fresh.outcome).toBeUndefined();
  expect(fresh.model.pHigh).toBeNull();
  const unknown = structuredClone(prefix);
  const comparison = unknown.find((e: any) => e.reference === cycle.deliberation!.reference);
  for (const candidate of comparison.event.data.simulation.candidates) {
    candidate.paths = [];
    for (const record of candidate.records) {
      record.prediction.raw.status = 'unknown';
      record.prediction.vectors = {};
    }
  }
  // Retained parameter statistics on an unknown response are not a usable forecast.
  expect(cognitiveCycle(unknown).model.pHigh).toBeNull();
});

test('all audited scenarios connect real beliefs, forecasts, choices and next-round memory', async ({ request }) => {
  const catalog = await (await request.get('/api/playground/catalog')).json();
  const summaries: Record<string, any[]> = {};
  for (const row of catalog.sessions.filter((r: any) => r.provenance.audit_status === 'passed')) {
    const saved = await (await request.get(`/api/playground/sessions/${row.id}`)).json();
    summaries[row.id] = [];
    for (const stop of cycleStops(saved.events)) {
      const cycle = cognitiveCycle(saved.events.slice(0, stop.index));
      expect(cycle.trace.state.id).toBe(cycle.observed!.event.data.state.id);
      expect(cycle.selected.action.state_id).toBe(cycle.trace.state.id);
      expect(cycle.intent!.event.data.receipt).toBe(cycle.outcome!.event.data.observation.receipt);
      expect(cycle.update!.event.data.belief.observed.payload).toEqual(cycle.outcome!.event.data.observation.state.payload);
      for (const receipt of cycle.feedback) {
        expect(receipt.event.run_id).not.toBe(cycle.run);
        expect(receipt.index).toBeLessThan(cycle.observed!.index);
      }
      if (cycle.model.prediction) {
        expect(cycle.model.prediction.state_id).toBe(cycle.trace.state.id);
        summaries[row.id].push({ tick: cycle.trace.state.payload.tick, p: cycle.model.pHigh, action: cycle.selected.action.payload.amount, model: cycle.model.version });
        for (const option of visitorOptions(cycle)) {
          expect(option.completed).toBe(option.prediction.vectors.delivered.at(-1) - cycle.trace.state.payload.delivered);
          expect(option.nextCompleted).toBe(option.prediction.vectors.delivered[0] - cycle.trace.state.payload.delivered);
          expect(option.waiting).toBe(option.prediction.vectors.queue.at(-1));
        }
      }
    }
  }
  const high = summaries['queue-stable_high-learned-11'];
  const low = summaries['queue-stable_low-learned-11'];
  expect(high[0].p).toBe(1);
  expect(low[0].p).toBe(0);
  // Different constructor scenarios really lead to different model-based actions.
  expect(high.map(c => c.action)).not.toEqual(low.map(c => c.action));
});

test('the system diagram connects observed World, internal model, real branches and execution', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', r => { if (r.method() !== 'GET') writes.push(r.url()); });
  const saved = await (await page.request.get('/api/playground/sessions/queue-stable_high-learned-11')).json();
  const end = firstWorkingCycle(saved.events);
  const cycle = cognitiveCycle(saved.events.slice(0, end));
  await page.goto('/#playground');
  await expect(page.getByRole('heading', { name: '世界を捉え、内部で未来を試し、選んで動かす。' })).toBeVisible();
  const world = page.getByRole('region', { name: '外の世界', exact: true });
  const inside = page.getByRole('region', { name: 'PreActの内部', exact: true });
  await expect(world).toBeVisible();
  await expect(world.locator('.aw-transfer i')).toHaveCount(6); // three actual arrivals and completions
  await expect(inside).toBeVisible();
  await expect(page.locator('.pg-inspection')).not.toBeVisible();
  await expect(inside.locator('.aw-module-title')).toHaveCount(6);
  await expect(page.getByTestId('aw-internal-state')).toHaveAttribute('data-state-id', cycle.observed!.event.data.state.id);
  await expect(page.getByTestId('aw-future')).toHaveCount(3);
  await expect(inside).toContainText('数値シミュレータ QueueTemporalEngine');
  await expect(inside).toContainText('ネイティブの候補生成');
  await expect(inside).toContainText('次tickの処理量：予想 3 → 実際 3');
  await expect(page.getByTestId('aw-flow-action')).toHaveAttribute('data-reference', cycle.intent!.reference);
  await expect(page.getByTestId('aw-flow-experience')).toHaveAttribute('data-reference', cycle.outcome!.reference);
  await expect(page.getByTestId('aw-flow-memory-cognition')).toHaveAttribute('data-reference', cycle.deliberation!.reference);
  await inside.locator('.aw-future-branches > button:not(.chosen)').first().click();
  await expect(page.locator('.aw-explanation')).toContainText('Simulation');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await expect(page.getByRole('slider', { name: '記録の再生位置' })).toHaveValue(String(end));
  await expect(page.locator('.pg-inspection')).toBeVisible();
  await expect(page.getByRole('region', { name: '予測と実際の対応' }).getByRole('cell', { name: '未確定 / 未選択', exact: true })).toHaveCount(2);
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).uncheck();
  const next = architectureStops(saved.events).find(e => e.index > end)!;
  await page.getByRole('button', { name: '次の処理を見る', exact: true }).click();
  await expect(page.locator('.pg-timeline input')).toHaveValue(String(next.index));
  await expect(page.getByTestId('aw-internal-state')).toHaveAttribute('data-state-id', next.event.data.state.id);
  await expect(page.getByTestId('aw-future')).toHaveCount(0);
  await expect(world.locator('.aw-transfer i')).toHaveCount(0); // no new outcome, no animated actual work
  await expect(inside).toContainText('この時点にモデル利用を示す予測記録はありません。');
  await expect(page.getByTestId('aw-flow-action')).toHaveAttribute('data-reference', '');
  await expect(page.getByTestId('aw-flow-experience')).toHaveAttribute('data-reference', '');
  await page.getByRole('button', { name: '自動で進める', exact: true }).click();
  const deliberation = architectureStops(saved.events).find(e => e.index > next.index)!;
  await expect(page.locator('.pg-timeline input')).toHaveValue(String(deliberation.index));
  await page.getByRole('button', { name: '一時停止', exact: true }).click();
  await expect(page.getByTestId('aw-future')).toHaveCount(3);
  await expect(inside.locator('.chosen')).toHaveCount(0);
  await expect(page.getByTestId('aw-flow-experience')).toHaveAttribute('data-reference', '');
  expect(writes).toEqual([]);
});

test.afterEach(async ({ request }) => {
  const rows = (await (await request.get('/api/playground/catalog')).json()).sessions;
  for (const row of rows.filter((r: any) => r.mode === 'live')) {
    await request.post(`/api/playground/sessions/${row.id}/control`, { data: { command: 'stop', revision: row.revision } });
    await expect.poll(async () => (await (await request.get(`/api/playground/sessions/${row.id}`)).json()).session.mode, { timeout: 45000 }).toBe('recorded');
  }
});

test('Live controls admit actual single rounds, pause, autoplay and new sessions', async ({ page }) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  let releaseCatalog!: () => void;
  const catalogGate = new Promise<void>(resolve => { releaseCatalog = resolve; });
  await page.route('**/api/playground/catalog', async route => { await catalogGate; await route.continue(); });
  await page.goto('/#playground');
  await expect(page.getByRole('heading', { name: '外の世界', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  releaseCatalog();
  await expect(page.getByTestId('mode-label')).toContainText('LIVE · 開始待ち');
  await page.getByRole('combobox', { name: 'シナリオ', exact: true }).selectOption('stable_high');
  await page.getByRole('button', { name: /^(Start|実験を始める)$/, exact: true }).click();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止', { timeout: 45000 });
  await expect(page.getByTestId('mode-label')).toContainText('LIVE');
  await expect(page.getByRole('img', { name: /実測World Tick 1 / })).toBeVisible();
  await page.getByRole('button', { name: /^(Next Step|次の判断を見る)$/, exact: true }).click();
  await expect(page.getByRole('img', { name: /実測World Tick 2 / })).toBeVisible();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止');
  await page.getByRole('button', { name: /^(Auto Play|自動で進める)$/, exact: true }).click();
  await expect.poll(async () => Number((await page.getByTestId('world-time').innerText()).match(/\d+/)![0])).toBeGreaterThanOrEqual(4);
  await page.getByRole('button', { name: /^(Pause|一時停止)$/, exact: true }).click();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止');
  const tick = await page.getByTestId('world-time').innerText();
  await page.waitForTimeout(1100);
  expect(await page.getByTestId('world-time').innerText()).toBe(tick);
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await page.getByTestId('module-memory').click();
  await expect(page.locator('.pg-inspector')).toContainText('LearningCoordinator');
  await expect(page.locator('.pg-inspector pre').first()).toContainText('learning_checkpoint');
  await page.screenshot({ path: `${captureDirectory}/playground-live.png`, fullPage: true });
  await page.getByRole('button', { name: 'Reset / 停止', exact: true }).click();
  await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY');
  await expect(page.locator('.pg-session-line')).toContainText('停止済み');
  await page.getByRole('button', { name: 'New Session', exact: true }).click();
  await expect(page.getByTestId('mode-label')).toContainText('LIVE · 開始待ち');
  expect(errors).toEqual([]);
});

test('technical view traces one integrated cycle and its real feedback into the next', async ({ page }) => {
  const saved = await (await page.request.get('/api/playground/sessions/queue-stable_high-learned-11')).json();
  const end = firstWorkingCycle(saved.events);
  const cycle = cognitiveCycle(saved.events.slice(0, end));
  await page.goto('/#playground');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  const nav = page.getByRole('region', { name: '一手の認知サイクル' });
  const slider = page.getByRole('slider', { name: '記録の再生位置' });
  await expect(slider).toHaveValue(String(end));
  await expect(page.getByTestId('world-cycle')).toContainText('実処理 3 単位');
  await nav.getByRole('button', { name: '観測', exact: true }).click();
  await expect(slider).toHaveValue(String(cycle.observed!.index));
  await expect(page.getByTestId('internal-model')).toContainText('数値予測なし');
  await expect(page.getByTestId('world-cycle')).toContainText('Actual未確定');
  await expect(page.getByTestId('flow-observe-predict')).toHaveAttribute('data-reference', '');
  await nav.getByRole('button', { name: '予測・比較', exact: true }).click();
  await expect(page.getByTestId('internal-model')).toContainText('P(service = 3) = 1');
  await expect(page.getByTestId('flow-observe-predict')).toHaveAttribute('data-reference', cycle.deliberation!.reference);
  await expect(page.getByTestId('module-decision')).toContainText('選択待ち');
  await nav.getByRole('button', { name: '選択', exact: true }).click();
  await expect(page.getByTestId('module-decision')).toContainText('3 単位を投入');
  await expect(page.getByTestId('world-cycle')).toContainText('Actual未確定');
  await nav.getByRole('button', { name: '実行結果', exact: true }).click();
  await expect(page.getByTestId('world-cycle')).toContainText('実処理 3 単位');
  await expect(page.getByRole('region', { name: '予測と実際の対応' }).getByRole('cell', { name: '3', exact: true })).toBeVisible();
  await page.locator('.pg-future:not(.chosen)').filter({ hasText: '候補Action' }).first().click();
  await expect(page.getByRole('region', { name: '予測と実際の対応' }).getByRole('cell', { name: '未確定 / 未選択', exact: true })).toHaveCount(2);
  await nav.getByRole('button', { name: '経験へ', exact: true }).click();
  await expect(page.getByTestId('flow-execute-remember')).toHaveAttribute('data-reference', cycle.update!.reference);
  await nav.getByRole('button', { name: '次の認知サイクル', exact: true }).click();
  const nextIndex = cycleStops(saved.events).find(e => e.index > end)!.index;
  const next = cognitiveCycle(saved.events.slice(0, nextIndex));
  expect(next.feedback.map(e => e.reference)).toContain(cycle.outcome!.reference);
  await expect(page.getByTestId('feedback-link')).toContainText(`確定Receipt ${next.feedback.length} 件`);
  await page.getByTestId('module-perception').click();
  await expect(page.locator('.pg-module-detail')).toContainText(`確定経験 ${next.feedback.length} 件を参照`);
  await page.getByRole('combobox', { name: 'シナリオ', exact: true }).selectOption('stable_low');
  await expect(page.getByTestId('internal-model')).toContainText('P(service = 3) = 0');
  await expect(page.locator('.pg-session-line')).toContainText('安定・低能力');
  await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY');
});

test('audited Replay ties module highlights, numeric futures and Actual to original events', async ({ page }) => {
  const rows = (await (await page.request.get('/api/playground/catalog')).json()).sessions;
  const record = rows.find((r: any) => r.provenance.audit_status === 'passed' && r.scenario === 'stable_high');
  expect(record).toBeTruthy();
  const original = await (await page.request.get(`/api/playground/sessions/${record.id}`)).json();
  const writes: string[] = [];
  page.on('request', r => { if (['POST', 'DELETE', 'PUT', 'PATCH'].includes(r.method())) writes.push(r.url()); });
  await page.goto('/#playground');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY');
  const trace = original.events.find((e: any) => e.event.kind === 'deliberation' && e.event.data.simulation.candidates.length === 3);
  const outcome = original.events.find((e: any) => e.event.run_id === trace.event.run_id && e.event.kind === 'outcome');
  const slider = page.getByRole('slider', { name: '記録の再生位置' });
  await expect(slider).toHaveAttribute('max', String(original.events.length));
  await slider.fill(String(trace.index));
  await expect(page.locator('.pg-future')).toHaveCount(4);
  await expect(page.getByTestId('module-prediction')).toHaveClass(/active/);
  await expect(page.getByTestId('module-simulation')).toHaveClass(/active/);
  // Deliberation records actual receipt-backed memory retrieval, not a new model update.
  expect(trace.event.data.memory_references.length).toBeGreaterThan(0);
  await expect(page.getByTestId('module-memory')).toHaveClass(/active/);
  await expect(page.locator('.pg-future em')).toHaveCount(0);
  await slider.fill(String(outcome.index));
  const expected = reconstructEvidence(original.events.slice(0, outcome.index));
  await expect(page.getByRole('img', { name: `実測World Tick ${expected.state!.payload.tick} 待機${expected.state!.payload.queue} 完了${expected.state!.payload.delivered}` })).toBeVisible();
  await expect(page.locator('.pg-future.chosen em')).toHaveCount(1);
  await page.locator('.pg-future.chosen').click();
  await expect(page.locator('.pg-inspector .pg-plot')).toContainText('H1 Actual');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await page.locator('.pg-inspector details').filter({ hasText: 'Action / Prediction / Gate / Actual' }).locator('summary').click();
  await expect(page.locator('.pg-inspector')).toContainText(outcome.event.data.observation.receipt);
  await page.screenshot({ path: `${captureDirectory}/playground-replay.png`, fullPage: true });
  await slider.fill(String(outcome.index - 1));
  await expect(page.locator('.pg-future em')).toHaveCount(0);
  await page.getByRole('button', { name: '先頭', exact: true }).click();
  await expect(page.locator('.pg-future')).toHaveCount(0);
  await page.getByRole('button', { name: /^(Next Step|次の判断を見る)$/, exact: true }).click();
  await expect(slider).toHaveValue('1');
  await page.getByRole('button', { name: /^(Auto Play|自動で進める)$/, exact: true }).click();
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(1);
  await page.getByRole('button', { name: /^(Pause|一時停止)$/, exact: true }).click();
  await page.getByRole('combobox', { name: '記録roundへ移動' }).selectOption(String(trace.index - 1));
  expect(writes).toEqual([]);
});

test('Replay shows real promotion and the adverse Low-to-High comparison', async ({ page }) => {
  const rows = (await (await page.request.get('/api/playground/catalog')).json()).sessions;
  const row = rows.find((r: any) => r.provenance.audit_status === 'passed' && r.scenario === 'low_to_high' && r.provenance.strategy === 'learned');
  expect(row).toBeTruthy();
  const saved = await (await page.request.get(`/api/playground/sessions/${row.id}`)).json();
  const promotion = saved.events.find((e: any) => e.event.kind === 'learning_explicit_promotion');
  expect(promotion).toBeTruthy();
  await page.goto('/#playground');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await page.locator('.pg-recordings>button').filter({ hasText: row.label }).click();
  await expect(page.locator('.pg-adverse')).toContainText('32.9');
  const slider = page.getByRole('slider', { name: '記録の再生位置' });
  await expect(slider).toHaveAttribute('max', String(saved.events.length));
  await slider.fill(String(promotion.index - 1));
  const before = reconstructEvidence(saved.events.slice(0, promotion.index - 1)).checkpoint;
  await expect(page.locator('.pg-learning code').first()).toHaveText(before.model_version.slice(0, 16));
  await slider.fill(String(promotion.index));
  const model = promotion.event.data.backend.epoch.model_version;
  expect(model).not.toBe(before.model_version);
  await expect(page.locator('.pg-learning code').first()).toHaveText(model.slice(0, 16));
  // A promotion does not retroactively change the model used by the prior decision.
  const used = cognitiveCycle(saved.events.slice(0, promotion.index));
  expect(used.model.version).toBe(before.model_version);
  expect(used.model.version).not.toBe(model);
  await page.getByTestId('module-memory').click();
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await expect(page.locator('.pg-inspector pre').first()).toContainText('learning_explicit_promotion');
});

test('localhost Replay works with external networking blocked and reduced motion', async ({ page, baseURL }) => {
  const errors: string[] = [], external: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/*', route => {
    if (route.request().url().startsWith(baseURL!)) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/#playground');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await expect(page.locator('.pg-recordings>button').filter({ hasText: 'AUDITED REPLAY' })).toHaveCount(6);
  await page.getByRole('button', { name: '候補比較へ', exact: true }).click();
  await page.getByRole('button', { name: /^(Start|実験を始める)$/, exact: true }).click();
  await expect.poll(async () => Number(await page.getByRole('slider', { name: '記録の再生位置' }).inputValue())).toBeGreaterThan(0);
  await page.getByRole('button', { name: /^(Pause|一時停止)$/, exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.route('**/api/playground/sessions/**', route => route.abort());
  await expect(page.getByRole('alert')).toContainText('接続を再試行', { timeout: 5000 });
  await page.unroute('**/api/playground/sessions/**');
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
});
