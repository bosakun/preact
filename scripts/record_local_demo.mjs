// Record actual runtime/UI operation with honest scope captions; never fabricate events.
import { chromium } from '../web/node_modules/playwright-core/index.mjs';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { dirname } from 'node:path';

const output = process.argv[2] || '.cache/demo-footage';
const base = process.env.PREACT_DEMO_BASE_URL || 'http://127.0.0.1:8000';
const expectedMode = process.env.PREACT_DEMO_EXPECT_MODE || 'local';
const parsedBase = new URL(base);
if (parsedBase.protocol !== 'http:' || parsedBase.hostname !== '127.0.0.1' ||
    parsedBase.username || parsedBase.password || parsedBase.pathname !== '/' ||
    parsedBase.search || parsedBase.hash || !['local','local-model'].includes(expectedMode)) {
  throw new Error('This private recorder requires an explicit loopback local mode');
}
await mkdir(dirname(output), { recursive:true });
await mkdir(output); // Preserve previous evidence; choose a new directory for reruns.
const browser = await chromium.launch({ headless:true });
const context = await browser.newContext({baseURL:base,viewport:{width:1440,height:1100},recordVideo:{dir:output,size:{width:1440,height:1100}}});
const page = await context.newPage(), video = page.video();
const modelMode = expectedMode === 'local-model';
const scope = modelMode
  ? 'Actual local NVIDIA Nemotron Q4_K_M inference on Apple Metal, trusted Python probes and Cartesian MuJoCo simulation. No Nebius, Cosmos, Isaac GPU or hardware evidence.'
  : 'Actual trusted Python probes and Cartesian MuJoCo simulation. No live Nebius/NVIDIA/hardware evidence.';
const evidence = {status:'failed',source_revision:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),mode:expectedMode,scope,segments:[],waiting_segments:[],runs:[],page_errors:[]};
page.on('pageerror', error => evidence.page_errors.push(error.name));
const started = Date.now();
async function caption(text, seconds=0) {
  await page.evaluate(text => {
    let element = document.getElementById('preact-demo-caption');
    if (!element) {element=document.createElement('div');element.id='preact-demo-caption';document.body.append(element);}
    element.textContent=text;
    element.style.cssText='position:fixed;bottom:12px;left:5%;right:5%;z-index:9999;background:#10151def;color:#e4e9f1;padding:16px 24px;border:1px solid #beec83;font:16px/1.5 system-ui;border-radius:8px;pointer-events:none';
  },text);
  if(seconds) await page.waitForTimeout(seconds*1000);
}
async function run(domain) {
  await caption(modelMode
    ? 'Live local Nemotron inference on Apple Metal. The final review edit may compress this labeled waiting interval; no events or outcomes are fabricated.'
    : 'Running actual local verification and execution.');
  const waitStart=(Date.now()-started)/1000;
  const responsePromise=page.waitForResponse(r=>r.url()===base+'/api/runs' && r.request().method()==='POST');
  await page.getByRole('button',{name:'Run experiment'}).click();
  const response=await responsePromise;
  if(response.status()!==202) throw new Error('Run creation failed');
  const {id}=await response.json();
  await page.locator('.run-status').filter({hasText:'complete'}).waitFor({timeout:330000});
  evidence.waiting_segments.push({domain,start_seconds:waitStart,end_seconds:(Date.now()-started)/1000});
  const archiveResponse=await page.request.get(`/api/runs/${id}/events`);
  if(!archiveResponse.ok()) throw new Error('Archive unavailable');
  const archive=await archiveResponse.json();
  if(!archive.some(e=>e.kind==='outcome')) throw new Error('No actual execution evidence');
  await writeFile(`${output}/${domain}-events.json`,JSON.stringify(archive,null,2)+'\n');
  evidence.runs.push({id,domain,events:archive.length,event_hash:createHash('sha256').update(JSON.stringify(archive)).digest('hex')});
}
try {
  const health=await page.request.get(base+'/api/health');
  if(!health.ok() || (await health.json()).mode!==expectedMode) throw new Error('Actual service mode does not match the explicitly requested local recorder scope');
  await page.goto(base);
  await caption(`PreAct explores possible futures, verifies evidence, executes one action, and learns from observed error. ${modelMode ? 'Local NVIDIA Nemotron reasoning; Python and MuJoCo verification.' : 'This recording uses the local execution lab.'}`,5);
  const softwareStart=(Date.now()-started)/1000;
  await run('software');
  await page.locator('.tree-area').scrollIntoViewIfNeeded();
  await page.locator('.future-card.rejected').first().click();
  await caption('Software World: a small attractive patch fails executable invariant checks. The Decision Gate rejects it before execution.',10);
  await page.locator('.future-card.executed').first().click();
  await caption('The safe first action prepares validation. Descendants show the follow-up repair; only the first action is authorized.',10);
  await page.getByRole('combobox',{name:'Decision round'}).selectOption('1');
  await page.locator('.future-card.executed').first().click();
  await page.locator('.evidence-area').scrollIntoViewIfNeeded();
  await caption('PreAct reobserves and replans. Actual subprocess outcomes label executed predictions; unchosen alternatives remain unlabeled.',15);
  await page.getByText('Calibrated engine trust',{exact:false}).click();
  await caption('The durable ledger records prediction error and contextual trust. This local benchmark does not show a calibration improvement.',10);
  evidence.segments.push({domain:'software',start_seconds:softwareStart,end_seconds:(Date.now()-started)/1000});
  await page.getByRole('button',{name:'Physical World'}).click();
  const physicalStart=(Date.now()-started)/1000;
  await run('physical');
  await page.locator('.tree-area').scrollIntoViewIfNeeded();
  await page.locator('.future-card.rejected').first().click();
  await caption('Physical World uses the same Core, tree, gate and ledger. Real local MuJoCo measurements reject the obstructed route. This is not Isaac or hardware validation.',12);
  await page.locator('.future-card.executed').first().click();
  await caption('Multi-step search selects lifting before transfer. The shown probability and risk belong to their stated local measurement scope.',14);
  await page.getByRole('combobox',{name:'Decision round'}).selectOption('1');
  await page.locator('.future-card.executed').first().click();
  await page.locator('.evidence-area').scrollIntoViewIfNeeded();
  await caption('The authoritative simulation moves the cube. Its observed position and trajectory are compared with the predicted future.',12);
  await page.getByRole('combobox',{name:'Decision round'}).selectOption('2');
  await page.locator('.future-card.executed').first().click();
  await caption('Release is separately verified and executed. Task completion requires the actual observed outcome, rather than a promising imagined branch.',12);
  await page.getByRole('slider',{name:'Event replay'}).fill('1');
  await page.locator('.tree-area').scrollIntoViewIfNeeded();
  await caption('Recorded replay inspects durable events without executing actions. The replay label keeps recorded evidence distinct from a new experiment.',10);
  await page.getByRole('button',{name:'Latest'}).click();
  await caption('Cheap inference alone cannot authorize safety. Missing evidence, failed infrastructure or unresolved uncertainty leads to verification or abstention.',10);
  evidence.segments.push({domain:'physical',start_seconds:physicalStart,end_seconds:(Date.now()-started)/1000});
  await caption(modelMode
    ? 'Five-seed local-model comparison: Physical PreAct completes 90/120, with 24 abstentions and 6 timeouts. Calibration worsens; latency rises. Nebius, Cosmos and Isaac GPU remain unvalidated.'
    : 'Local heuristic evaluation includes 20 physical abstentions and higher verification work/latency. Nebius, Cosmos and Isaac GPU integrations remain unvalidated. This clip uses Python and MuJoCo.',10);
  if(evidence.page_errors.length) throw new Error('Recording encountered a browser page error');
  evidence.status='completed';
} finally {
  await context.close();await browser.close();
  const path=await video.path();
  evidence.duration_wall_seconds=(Date.now()-started)/1000;
  evidence.video={path,sha256:createHash('sha256').update(await readFile(path)).digest('hex')};
  await writeFile(output+'/evidence.json',JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify({status:evidence.status,video:path,scope:evidence.scope,duration_wall_seconds:evidence.duration_wall_seconds}));
}
