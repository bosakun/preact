import { test, expect } from '@playwright/test';
import { reconstructEvidence } from '../src/playground/evidence';

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
  await page.goto('/#playground');
  await expect(page.getByRole('heading', { name: 'Queue World', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Live', exact: true }).click();
  await page.getByRole('combobox', { name: 'シナリオ', exact: true }).selectOption('stable_high');
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止', { timeout: 45000 });
  await expect(page.getByTestId('mode-label')).toContainText('LIVE');
  await expect(page.getByRole('img', { name: /実測World Tick 1 / })).toBeVisible();
  await page.getByRole('button', { name: 'Next Step', exact: true }).click();
  await expect(page.getByRole('img', { name: /実測World Tick 2 / })).toBeVisible();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止');
  await page.getByRole('button', { name: 'Auto Play', exact: true }).click();
  await expect.poll(async () => Number((await page.locator('.pg-stats>div').first().locator('b').innerText()))).toBeGreaterThanOrEqual(4);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(page.locator('.pg-session-line')).toContainText('一時停止');
  const tick = await page.locator('.pg-stats>div').first().locator('b').innerText();
  await page.waitForTimeout(1100);
  expect(await page.locator('.pg-stats>div').first().locator('b').innerText()).toBe(tick);
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

test('audited Replay ties module highlights, numeric futures and Actual to original events', async ({ page }) => {
  const rows = (await (await page.request.get('/api/playground/catalog')).json()).sessions;
  const record = rows.find((r: any) => r.provenance.audit_status === 'passed' && r.scenario === 'stable_high');
  expect(record).toBeTruthy();
  const original = await (await page.request.get(`/api/playground/sessions/${record.id}`)).json();
  const writes: string[] = [];
  page.on('request', r => { if (['POST', 'DELETE', 'PUT', 'PATCH'].includes(r.method())) writes.push(r.url()); });
  await page.goto('/#playground');
  await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY');
  const trace = original.events.find((e: any) => e.event.kind === 'deliberation' && e.event.data.simulation.candidates.length === 3);
  const outcome = original.events.find((e: any) => e.event.run_id === trace.event.run_id && e.event.kind === 'outcome');
  const slider = page.getByRole('slider', { name: '記録の再生位置' });
  await expect(slider).toHaveAttribute('max', String(original.events.length));
  await slider.fill(String(trace.index));
  await expect(page.locator('.pg-future')).toHaveCount(4);
  await expect(page.getByTestId('module-prediction')).toHaveClass(/active/);
  await expect(page.getByTestId('module-simulation')).toHaveClass(/active/);
  await expect(page.getByTestId('module-memory')).not.toHaveClass(/active/);
  await expect(page.locator('.pg-future em')).toHaveCount(0);
  await slider.fill(String(outcome.index));
  const expected = reconstructEvidence(original.events.slice(0, outcome.index));
  await expect(page.getByRole('img', { name: `実測World Tick ${expected.state!.payload.tick} 待機${expected.state!.payload.queue} 完了${expected.state!.payload.delivered}` })).toBeVisible();
  await expect(page.locator('.pg-future.chosen em')).toHaveCount(1);
  await page.locator('.pg-future.chosen').click();
  await expect(page.locator('.pg-plot')).toContainText('H1 Actual');
  await page.getByRole('checkbox', { name: '技術者向け詳細' }).check();
  await page.locator('.pg-inspector details').filter({ hasText: 'Action / Prediction / Gate / Actual' }).locator('summary').click();
  await expect(page.locator('.pg-inspector')).toContainText(outcome.event.data.observation.receipt);
  await page.screenshot({ path: `${captureDirectory}/playground-replay.png`, fullPage: true });
  await slider.fill(String(outcome.index - 1));
  await expect(page.locator('.pg-future em')).toHaveCount(0);
  await page.getByRole('button', { name: '先頭', exact: true }).click();
  await expect(page.locator('.pg-future')).toHaveCount(0);
  await page.getByRole('button', { name: 'Next Step', exact: true }).click();
  await expect(slider).toHaveValue('1');
  await page.getByRole('button', { name: 'Auto Play', exact: true }).click();
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(1);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
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
  await expect(page.locator('.pg-recordings>button').filter({ hasText: 'AUDITED REPLAY' })).toHaveCount(6);
  await page.getByRole('button', { name: '候補比較へ', exact: true }).click();
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  await expect.poll(async () => Number(await page.getByRole('slider', { name: '記録の再生位置' }).inputValue())).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.route('**/api/playground/sessions/**', route => route.abort());
  await expect(page.getByRole('alert')).toContainText('接続を再試行', { timeout: 5000 });
  await page.unroute('**/api/playground/sessions/**');
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
});
