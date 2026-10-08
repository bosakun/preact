import { test, expect } from '@playwright/test';

const captureDirectory = process.env.PREACT_E2E_ARTIFACT_DIR || '../reports';

test('both worlds render actual shared-core trees and outcome evidence', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /See what could happen/ })).toBeVisible();
  await page.getByRole('button', { name: 'Run experiment' }).click();
  await expect(page.locator('.run-status')).toHaveText('complete', { timeout: 20000 });
  await expect(page.locator('.future-card.rejected').first()).toBeVisible();
  await expect(page.locator('.future-card.executed').first()).toBeVisible();
  await expect.poll(async () => page.locator('.react-flow__node').evaluateAll(nodes =>
    nodes.every(node => getComputedStyle(node).visibility === 'visible'))).toBe(true);
  await expect(page.locator('.error-row').first()).toBeVisible();
  await expect(page.locator('.gate-reasons')).toContainText('Decision Gate');
  const total = await page.locator('.future-card').count();
  await page.getByRole('button', { name: 'Expand future branch' }).click();
  await expect.poll(() => page.locator('.future-card').count()).toBeGreaterThan(total);
  await page.getByRole('button', { name: 'Collapse future branch' }).click();
  await expect(page.locator('.future-card')).toHaveCount(total);
  await page.screenshot({ path: `${captureDirectory}/software-tree.png`, fullPage: true });
  await page.getByRole('button', { name: 'Physical World' }).click();
  await page.getByRole('button', { name: 'Run experiment' }).click();
  await expect(page.locator('.run-status')).toHaveText('complete', { timeout: 20000 });
  await expect(page.getByRole('img', { name: 'Measured cube position, obstacle and trajectory' })).toBeVisible();
  await page.screenshot({ path: `${captureDirectory}/physical-tree.png`, fullPage: true });
  await page.getByRole('slider', { name: 'Event replay' }).fill('1');
  await expect(page.locator('.future-card')).toHaveCount(0);
  await page.getByRole('button', { name: 'Latest' }).click();
  await expect(page.locator('.future-card.executed').first()).toBeVisible();
  await page.locator('.history button').first().click();
  await expect(page.locator('.replay')).toContainText('RECORDED REPLAY');
});

test('direct baseline records an unsafe outcome without fabricated forecasts', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('checkbox', { name: 'Direct Agent baseline' }).check();
  await page.getByRole('button', { name: 'Run experiment' }).click();
  await expect(page.locator('.run-status')).toHaveText('failed_task', { timeout: 20000 });
  await expect(page.locator('.run-summary')).toContainText('Observed');
  await expect(page.locator('.error-row')).toHaveCount(0);
});

test('release workflow uses the same tree for real migration, configuration and build', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Software task' }).selectOption('release');
  await page.getByRole('button', { name: 'Run experiment' }).click();
  await expect(page.locator('.run-status')).toHaveText('complete', { timeout: 20000 });
  await expect(page.getByRole('heading', { name: 'Repair, migrate, configure and verify a release.' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Decision round' }).selectOption('1');
  await expect(page.locator('.future-card.rejected').filter({hasText: 'Recreate orders'})).toBeVisible();
  await expect(page.locator('.future-card.rejected').filter({hasText: 'Add a nullable discount'})).toBeVisible();
  await expect(page.locator('.checks')).toContainText('data_preservation');
  await expect(page.locator('.checks')).toContainText('schema_integrity');
  await page.screenshot({ path: `${captureDirectory}/repository-workflow.png`, fullPage: true });
  await page.getByRole('combobox', { name: 'Decision round' }).selectOption('3');
  await expect(page.locator('.future-card.executed')).toContainText('Compile and verify the release');
});

test('opening physical history synchronizes world controls and shows measured state', async ({ page }) => {
  const response = await page.request.post('/api/runs', {data:{domain:'physical', mode:'preact',seed:2,task:'default'}});
  expect(response.status()).toBe(202);
  const {id} = await response.json();
  await expect.poll(async () => (await (await page.request.get(`/api/runs/${id}`)).json()).status,
    {timeout:20000}).toBe('complete');
  await page.goto('/');
  await page.locator('.history button').filter({hasText:id.slice(0,8)}).click();
  await expect(page.getByRole('button',{name:'Physical World'})).toHaveClass('active');
  await expect(page.getByRole('combobox',{name:'Software task'})).toHaveCount(0);
  await expect(page.locator('.future-card.observed')).toContainText('Cube at (');
  await page.getByRole('combobox',{name:'Decision round'}).selectOption('1');
  await expect(page.locator('.future-card.observed')).toContainText('object held');
  await expect(page.locator('.future-card.observed')).not.toContainText('start position');
});

test('unavailable API shows disconnected state without a cloud claim or page error', async ({page}) => {
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/health',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Durable storage is unavailable'})}));
  await page.route('**/api/runs',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Durable storage is unavailable'})}));
  await page.goto('/');
  await expect(page.locator('.connection')).toContainText('Disconnected');
  await expect(page.locator('.connection')).not.toContainText('Cloud runtime');
  await expect(page.getByRole('alert')).toBeVisible();
  expect(errors).toEqual([]);
});

test('physical first actions remain readable while later futures are expandable', async ({page}) => {
  const response = await page.request.post('/api/runs', {data:{domain:'physical',mode:'preact',seed:6,task:'default'}});
  expect(response.status()).toBe(202);
  const {id} = await response.json();
  await expect.poll(async () => (await (await page.request.get(`/api/runs/${id}`)).json()).status,
    {timeout:20000}).toBe('complete');
  await page.goto('/');
  await page.locator('.history button').filter({hasText:id.slice(0,8)}).click();
  await expect(page.locator('.node-branches').first()).toContainText('Later futures');
  await expect.poll(() => page.locator('.future-card.executed').first().evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThanOrEqual(130);
  const initial = await page.locator('.future-card').count();
  await page.getByRole('button',{name:'Expand future branch'}).click();
  await expect.poll(() => page.locator('.future-card').count()).toBeGreaterThan(initial);
  await expect(page.locator('.replay')).toContainText('RECORDED REPLAY');
  const events = await (await page.request.get(`/api/runs/${id}/events`)).json();
  const outcome = events.find((event:any) => event.kind === 'outcome');
  const before = events.find((event:any) => event.kind === 'trust_snapshot');
  const after = events.find((event:any) => event.kind === 'trust_updated' && event.seq > outcome.seq);
  const trust = page.locator('details').filter({has:page.locator('summary').filter({hasText:'Calibrated engine trust'})});
  await trust.locator('summary').click();
  const snapshot = JSON.parse(await trust.locator('pre').innerText());
  // Earlier decision evidence must not be replaced by the final round's update.
  expect(snapshot.after?.engines ?? snapshot).toEqual(after.data.engines);
  expect(snapshot.before.engines).toEqual(before.data.engines);
  await expect(page.locator('.engine-trust')).toContainText('Decision input → after observed action');
  await page.locator('.future-card.verified').first().click();
  await expect(page.locator('.engine-trust')).toContainText('This branch has no observed trust update');
  expect(JSON.parse(await trust.locator('pre').innerText()).after).toBeNull();
  await expect(page.locator('.error-row')).toHaveCount(0);
  await page.locator('.future-card.executed').first().click();
  await page.getByRole('slider', {name:'Event replay'}).fill(String(outcome.seq - 1));
  expect(JSON.parse(await trust.locator('pre').innerText()).after).toBeNull();
  await expect(page.locator('.error-row')).toHaveCount(0);
  await page.getByRole('button', {name:'Latest'}).click();
  await page.getByRole('combobox', {name:'Decision round'}).selectOption('1');
  const secondOutcome = events.filter((event:any) => event.kind === 'outcome')[1];
  const secondBefore = events.filter((event:any) => event.kind === 'trust_snapshot')[1];
  const secondAfter = events.find((event:any) => event.kind === 'trust_updated' && event.seq > secondOutcome.seq);
  const secondSnapshot = JSON.parse(await trust.locator('pre').innerText());
  expect(secondSnapshot.before.engines).toEqual(secondBefore.data.engines);
  expect(secondSnapshot.after.engines).toEqual(secondAfter.data.engines);
});
