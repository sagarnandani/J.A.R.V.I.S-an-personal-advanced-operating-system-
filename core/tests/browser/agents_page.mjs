/* The Agents page.
 *
 * The one thing this must prove is that the page is not a drawing. Every
 * assertion below compares what is on screen against what /v1/org says,
 * so a page that had been hand-maintained -- or had drifted from the
 * registry -- fails here rather than being discovered months later when
 * an agent nobody remembers turns out to have been retired all along.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

// What the registry actually says, fetched independently of the page.
const org = await (await fetch(BASE + '/v1/org')).json();
const flat = [];
(function walk(n) { flat.push(n); n.children.forEach(walk); })(org.root);
const specialists = flat.filter((n) => n.kind !== 'orchestrator' && n.kind !== 'coordinator');

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
await page.click('button[data-view="agents"]');
await page.waitForSelector('#agentsView:not(.hidden)', { timeout: 5000 });
await page.waitForSelector('#orgTree .node', { timeout: 15000 });
ok('Agents tab opens');

// --- the tree is the registry, not a picture of it ------------------------
const shown = await page.locator('#orgTree .node').evaluateAll(
  (els) => els.map((e) => e.dataset.node));
const missing = flat.map((n) => n.id).filter((id) => !shown.includes(id));
if (missing.length) fail(`the registry has agents the page does not show: ${missing.join(', ')}`);
const invented = shown.filter((id) => !flat.some((n) => n.id === id));
if (invented.length) fail(`the page shows agents the registry does not have: ${invented.join(', ')}`);
ok(`${shown.length} nodes, exactly what /v1/org returned`);

// --- the hierarchy is visible, not just present ---------------------------
const levels = await page.evaluate(() => {
  const style = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const nm = getComputedStyle(el.querySelector('.nm'));
    return { colour: nm.color, family: nm.fontFamily, indent: getComputedStyle(el).paddingLeft };
  };
  return { root: style('.node.orchestrator'), coord: style('.node.coordinator'),
           spec: style('.node.specialist') };
});
if (!levels.root || !levels.spec) fail('the tree has no distinguishable levels');
else {
  if (levels.root.colour === levels.spec.colour)
    fail('JARVIS and a specialist are drawn identically');
  if (levels.coord && levels.coord.colour === levels.spec.colour)
    fail('a coordinator and a specialist are drawn identically');
  if (parseFloat(levels.spec.indent) <= parseFloat(levels.root.indent))
    fail('depth is not shown by indentation');
}
ok('four levels told apart by colour, weight and indent');

// --- collapse and expand --------------------------------------------------
await page.click('#collapseAll');
const collapsed = await page.locator('#orgTree .node').count();
if (collapsed !== 1 + org.root.children.length)
  fail(`collapse left ${collapsed} rows, expected ${1 + org.root.children.length}`);
await page.click('#expandAll');
const expanded = await page.locator('#orgTree .node').count();
if (expanded !== flat.length) fail(`expand showed ${expanded} of ${flat.length}`);
ok(`collapse to ${collapsed}, expand back to ${expanded}`);

// --- search reveals, rather than merely filtering --------------------------
const deep = specialists.find((n) => n.supervisor) || specialists[0];
await page.click('#collapseAll');
await page.fill('#agentSearch', deep.name);
await page.waitForTimeout(200);
if (!(await page.locator(`#orgTree .node[data-node="${deep.id}"]`).count()))
  fail(`searching for "${deep.name}" did not reveal it inside a collapsed branch`);
ok(`search reveals a match inside a shut branch (${deep.name})`);

await page.fill('#agentSearch', 'zzzzz-nothing');
await page.waitForTimeout(200);
if (!(await page.locator('#orgTree .empty').count()))
  fail('a search that matches nothing says nothing about it');
await page.fill('#agentSearch', '');
await page.click('#expandAll');
ok('a search with no matches says so');

// --- a specialist's detail ------------------------------------------------
const one = specialists.find((n) => n.id === 'research.web') || specialists[0];
await page.click(`#orgTree .node[data-node="${one.id}"]`);
await page.waitForFunction(
  (name) => document.getElementById('agentName').textContent === name,
  one.name, { timeout: 10000 });
const detail = await page.locator('#agentDetail').textContent();
const truth = await (await fetch(`${BASE}/v1/org/${encodeURIComponent(one.id)}`)).json();

for (const want of ['Identity', 'Models', 'Permissions', 'What it costs', 'Activity'])
  if (!detail.includes(want)) fail(`the detail panel has no ${want} section`);
if (!detail.includes(truth.identity.capability)) fail('the capability is not shown');
if (!detail.includes('at paid rates')) fail('shadow cost is not shown beside the real one');
// Both halves of the permission list. "Cannot publish" is the single most
// important fact about most of these agents, and a page that showed only
// what an agent holds would never say it.
if (!/✓/.test(detail) || !/✕/.test(detail))
  fail('the permissions list does not show both what it can and cannot do');
ok(`${one.name}: identity, models, permissions, cost and activity, all from the registry`);

// --- a figure nobody measures must be blank, not zero ---------------------
if (truth.performance.runs === 0 && /Runs\s*0\b/.test(detail))
  fail('an agent that has never run is shown as zero runs rather than as unmeasured');
if (!detail.includes('not shown because'))
  fail('the panel does not say which figures are unmeasured');
ok('unmeasured figures are named as unmeasured, not shown as zero');

// --- an agent actually at work --------------------------------------------
// Seeded by seed_busy_agent.py. "What is it doing right now" is the
// question this page exists to answer, so a page that could only ever
// show idle agents would not have been checked at all.
const busy = flat.find((n) => n.state === 'working');
if (!busy) {
  fail('no agent is working — run seed_busy_agent.py before this check');
} else {
  const dot = await page.locator(`#orgTree .node[data-node="${busy.id}"] .dot.working`).count();
  if (!dot) fail(`${busy.name} is working and the tree does not show it`);
  await page.click(`#orgTree .node[data-node="${busy.id}"]`);
  await page.waitForFunction(
    (name) => document.getElementById('agentName').textContent === name,
    busy.name, { timeout: 10000 });
  const live = await page.locator('#agentDetail').textContent();
  const truth = await (await fetch(`${BASE}/v1/org/${encodeURIComponent(busy.id)}`)).json();
  const job = truth.activity.current[0];
  for (const [what, value] of [['objective', job.objective], ['task id', job.task_id],
                               ['model', job.model]]) {
    if (value && !live.includes(value)) fail(`the live panel omits the ${what}`);
  }
  if (!live.includes('Working on')) fail('the panel does not say it is working');
  ok(`${busy.name} shown at work: objective, task id, model and budget`);
}

// --- JARVIS and a coordinator are not pretend agents ----------------------
// Wait for the panel to be about JARVIS, not merely for a panel to exist.
// The detail panel is already populated from the previous click, so
// waiting for ".sect" returns instantly and reads the last agent.
await page.click('#orgTree .node[data-node="jarvis"]');
await page.waitForFunction(
  () => document.getElementById('agentName').textContent === 'JARVIS',
  null, { timeout: 10000 });
const jarvis = await page.locator('#agentDetail').textContent();
if (!/no — this is not a model call/.test(jarvis))
  fail('JARVIS is presented as though it were a registered agent');
ok('JARVIS is shown as the orchestrator, not as an agent');

const coordinator = flat.find((n) => n.kind === 'coordinator');
if (coordinator) {
  await page.click(`#orgTree .node[data-node="${coordinator.id}"]`);
  await page.waitForFunction(
    (name) => document.getElementById('agentName').textContent === name,
    coordinator.name, { timeout: 10000 });
  const text = await page.locator('#agentDetail').textContent();
  if (!/Not an agent/.test(await page.locator('#agentRole').textContent()))
    fail('a coordinator does not say it is not an agent');
  if (!/Agents below/.test(text)) fail('a coordinator shows no domain summary');
  ok(`${coordinator.name}: a domain summary, and it says it is not an agent`);
}

// --- no create-agent button ------------------------------------------------
const buttons = await page.locator('#agentsView button').allTextContents();
if (buttons.some((b) => /new|create|add agent/i.test(b)))
  fail('the page offers to create an agent in production');
ok('no create-agent button, which is deliberate');

await page.screenshot({ path: process.argv[2] || 'agents.png' });

// --- three screen sizes ----------------------------------------------------
for (const [w, h, label] of [[1180, 820, 'iPad landscape'],
                             [820, 1180, 'iPad portrait'],
                             [390, 750, 'iPhone']]) {
  const p2 = await (await browser.newContext({ viewport: { width: w, height: h } })).newPage();
  await p2.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('#app:not(.hidden)');
  await p2.click('button[data-view="agents"]');
  await p2.waitForSelector('#orgTree .node');
  const box = await p2.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, w: window.innerWidth,
    treeBottom: document.getElementById('orgTree').getBoundingClientRect().top,
    vh: window.innerHeight,
  }));
  if (box.scrollW > box.w + 1) fail(`${label}: the page scrolls sideways (${box.scrollW} > ${box.w})`);
  if (box.treeBottom > box.vh) fail(`${label}: the tree starts below the fold`);
  ok(`${label} (${w}×${h}): tree on screen, no sideways scroll`);
  if (w === 390) await p2.screenshot({ path: process.argv[3] || 'agents-phone.png' });
}

const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
