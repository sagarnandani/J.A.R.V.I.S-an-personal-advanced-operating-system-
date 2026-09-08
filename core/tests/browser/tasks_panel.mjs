import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
// ok() rather than ok(): an earlier version pushed its success
// line whether or not the assertion above it held, so a run could
// print FAIL and then claim the same step had worked.
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
// A real iPad viewport in landscape -- the layout this dashboard is built for.
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));
const blocked = [];
page.on('requestfailed', (r) => blocked.push(r.url()));

// Same reason as the other checks: no Gemini key here, so a real plan
// would name research.web and fail on the missing key. The plan is
// steered to a capability that runs keyless; the panel, the approval and
// the run are all real.
await page.route('**/v1/plans', async (route) => {
  const res = await route.fetch();
  const plan = await res.json();
  plan.steps = [{ name: 'step1', capability: 'general.analysis',
                  objective: 'Look into it', after: [] }];
  await route.fulfill({ response: res, json: plan });
});
// The check server runs with no Gemini key, so research.web cannot run --
// and since the fallback now correctly reaches for it, every run would end
// in an honest "no API key" failure. These checks are about the panel and
// the card, not about which agent gets picked, so the workflow is given an
// explicit step that works keyless. Everything else -- the real workflow,
// the real polling, the real rendering -- is untouched.
await page.route('**/v1/workflows', async (route) => {
  if (route.request().method() !== 'POST') return route.continue();
  const body = route.request().postDataJSON() || {};
  if (!body.steps || !body.steps.length) {
    body.steps = [{ capability: 'general.analysis', objective: body.objective,
                    name: 'step1', after: [] }];
  }
  await route.continue({ postData: JSON.stringify(body) });
});

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
ok('signed in (dev mode), dashboard visible');

// --- reach the panel ------------------------------------------------------
await page.click('button[data-view="tasks"]');
await page.waitForSelector('#tasksView:not(.hidden)', { timeout: 5000 });
if (!(await page.locator('.grid:not(#tasksView):not(#settingsView)').first().isHidden()))
  fail('the home grid is still showing behind the Tasks view');
ok('Tasks tab opens');

// --- everything must fit, which is the recurring failure here -------------
const box = await page.evaluate(() => {
  const r = document.getElementById('planBtn').getBoundingClientRect();
  return { bottom: r.bottom, vh: window.innerHeight,
           scrollW: document.documentElement.scrollWidth, w: window.innerWidth };
});
if (box.bottom > box.vh) fail(`the Plan button is ${Math.round(box.bottom - box.vh)}px below the fold`);
if (box.scrollW > box.w + 1) fail(`the page scrolls sideways (${box.scrollW} > ${box.w})`);
ok('controls on screen, no sideways scroll');

// --- planning spends nothing ----------------------------------------------
const count = async () => (await (await fetch(BASE + '/v1/workflows?limit=50')).json()).length;
const startedWith = await count();

await page.fill('#objective', 'Find out what the EV subsidy in Karnataka is');
await page.click('#planBtn');
await page.waitForSelector('#planSteps .step', { timeout: 20000 });
const steps = await page.locator('#planSteps .step').count();
if (steps < 1) fail('no steps rendered');
if (await page.locator('#runBtn').isHidden()) fail('Run did not appear after planning');
ok(`plan rendered: ${steps} step(s), Run offered`);

// Compared against the count taken before planning, not against zero:
// the database persists between runs of this script.
const afterPlanning = await count();
if (afterPlanning !== startedWith) fail(`planning created ${afterPlanning - startedWith} workflow(s) before approval`);
else ok('planning created no work');

// --- discard really discards ----------------------------------------------
await page.click('#discardBtn');
if (await page.locator('#planSteps .step').count() !== 0) fail('discard left the plan on screen');
if (!(await page.locator('#runBtn').isHidden())) fail('Run still offered after discard');
ok('discard clears the plan');

// --- run it ---------------------------------------------------------------
await page.click('#planBtn');
await page.waitForSelector('#planSteps .step', { timeout: 20000 });
await page.click('#runBtn');
await page.waitForFunction(
  () => /completed|failed/.test(document.getElementById('taskNote').textContent),
  null, { timeout: 60000 });
const note = await page.locator('#taskNote').textContent();
if (!/completed/.test(note)) fail(`workflow did not complete: ${note}`);
ok(`ran to completion: "${note.trim()}"`);

const done = await page.locator('#planSteps .step.completed').count();
if (done < 1) fail('no step showed as done');
ok(`${done} step(s) shown complete with their output`);

// --- history --------------------------------------------------------------
await page.waitForSelector('#wfList .wf', { timeout: 10000 });
const history = await page.locator('#wfList .wf').count();
if (history < 1) fail('the run did not appear in Recent');
await page.click('#wfList .wf');
await page.waitForSelector('#planSteps .step', { timeout: 10000 });
ok('Recent lists the run and reopens it');

await page.screenshot({ path: process.argv[2] || 'panel.png' });
const ours = blocked.filter((u) => u.startsWith(BASE));
if (ours.length) fail('requests to JARVIS failed: ' + ours.join(' | '));
if (blocked.length) ok(`(${blocked.length} third-party request(s) blocked by this sandbox: ${[...new Set(blocked.map((u) => new URL(u).host))].join(', ')})`);

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
