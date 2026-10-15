import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

test('kiosk blocks other APIs, works offline and preserves receipts across process restart', async ({ page }) => {
  test.setTimeout(120000);
  const root = path.resolve('..');
  await mkdir(path.join(root, '.cache'), { recursive: true });
  const directory = await mkdtemp(path.join(root, '.cache/kiosk-e2e-'));
  const port = process.env.PREACT_KIOSK_E2E_PORT || '8019';
  const base = `http://127.0.0.1:${port}`;
  let server: ChildProcess | null = null, logs = '';
  async function startServer() {
    server = spawn(path.join(root, '.venv/bin/python'), ['-m', 'uvicorn', 'preact.service.app:app', '--host', '127.0.0.1', '--port', port, '--no-access-log'], {
      cwd: root, env: { ...process.env, PREACT_MODE: 'local', PREACT_KIOSK: '1', PREACT_DATABASE_URL: `sqlite:///${directory}/ledger.db`, PREACT_ARTIFACTS_DIR: `${directory}/artifacts`, PREACT_PLAYGROUND_ROOT: `${directory}/sessions`, PREACT_PLAYGROUND_RECORDINGS: path.join(root, 'assets/playground/recordings') }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    server.stdout?.on('data', chunk => { logs += chunk; });
    server.stderr?.on('data', chunk => { logs += chunk; });
    await expect.poll(async () => {
      try { return (await fetch(`${base}/api/health`)).status; } catch { return 0; }
    }, { timeout: 30000, message: 'The isolated kiosk API must start' }).toBe(200);
  }
  async function stopServer() {
    if (!server || server.exitCode != null) return;
    const current = server;
    const exited = new Promise<void>(resolve => current.once('exit', () => resolve()));
    current.kill('SIGTERM');
    await exited;
    server = null;
  }
  const errors: string[] = [], external: string[] = [], genericRequests: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.url().startsWith(`${base}/api/runs`)) genericRequests.push(r.url()); });
  await page.route('**/*', route => {
    if (route.request().url().startsWith(base)) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  try {
    await startServer();
    const health = await (await page.request.get(`${base}/api/health`)).json();
    expect(health.kiosk).toBe(true);
    expect(health.playground_replays).toBe(6);
    expect(health.playground_replay_errors).toEqual([]);
    expect((await page.request.post(`${base}/api/runs`, { data: { domain: 'software' } })).status()).toBe(404);
    expect((await page.request.get(`${base}/api/artifacts/${'a'.repeat(64)}`)).status()).toBe(404);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto(base);
    await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY');
    await expect(page.getByRole('button', { name: 'Run experiment' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Future Tree', exact: true })).toHaveCount(0);
    await page.evaluate(() => { location.hash = ''; });
    await expect(page).toHaveURL(`${base}/#playground`);
    await page.getByRole('button', { name: 'Live', exact: true }).click();
    await page.getByRole('button', { name: /^(Start|実験を始める)$/, exact: true }).click();
    await expect(page.locator('.pg-session-line')).toContainText('一時停止', { timeout: 45000 });
    await expect(page.getByRole('img', { name: /実測World Tick 1 / })).toBeVisible();
    const active = (await (await page.request.get(`${base}/api/playground/catalog`)).json()).sessions.find((s: any) => s.mode === 'live');
    const before = await (await page.request.get(`${base}/api/playground/sessions/${active.id}`)).json();
    const receipt = before.events.find((e: any) => e.event.kind === 'outcome').event.data.observation.receipt;
    await page.reload();
    await page.locator('.pg-recordings>button').filter({ hasText: 'LIVE SESSION' }).click();
    await expect(page.getByRole('img', { name: /実測World Tick 1 / })).toBeVisible();
    await stopServer();
    await expect(page.getByRole('alert')).toContainText('接続を再試行', { timeout: 5000 });
    await startServer();
    await expect(page.getByTestId('mode-label')).toContainText('RECORDED REPLAY', { timeout: 15000 });
    await expect(page.locator('.pg-session-line')).toContainText('中断済み');
    const recovered = await (await page.request.get(`${base}/api/playground/sessions/${active.id}`)).json();
    expect(recovered.events.filter((e: any) => e.event.kind === 'outcome').map((e: any) => e.event.data.observation.receipt)).toEqual([receipt]);
    expect((await page.request.post(`${base}/api/playground/sessions/${active.id}/control`, { data: { command: 'step', revision: 0 } })).status()).toBe(409);
    await page.getByRole('button', { name: /^(New Session|新しい実験)$/, exact: true }).click();
    await page.getByRole('button', { name: /^(Start|実験を始める)$/, exact: true }).click();
    await expect(page.locator('.pg-session-line')).toContainText('一時停止', { timeout: 45000 });
    await expect(page.getByRole('img', { name: /実測World Tick 1 / })).toBeVisible();
    const next = (await (await page.request.get(`${base}/api/playground/catalog`)).json()).sessions.find((s: any) => s.mode === 'live');
    expect(next.id).not.toBe(active.id);
    expect(genericRequests).toEqual([]);
    expect(external).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await stopServer();
    await writeFile(path.join(directory, 'server.log'), logs);
  }
});
