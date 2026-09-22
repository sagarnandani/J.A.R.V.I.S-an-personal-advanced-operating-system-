/* Section 15, on screen: which model is good at this job.
 *
 * The router escalating away from a failing model is covered by pytest.
 * What only a browser can show is whether the owner can SEE that
 * happening -- and, more importantly, whether the panel keeps the
 * router's honesty about small numbers. A page that says "33%" next to a
 * model with three runs has quietly started making a claim the router
 * itself refuses to make.
 *
 * Needs seed_model_history.py first.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const CAPABILITY = 'research.web';
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

// What the server says, fetched independently, so the page is compared
// against the truth rather than against itself.
const truth = await (await fetch(`${BASE}/v1/org/${CAPABILITY}`)).json();
const measured = truth.models && truth.models.measured;
if (!measured || !measured.rows.length)
  fail(`/v1/org/${CAPABILITY} carries no measured models -- seed first`);
ok(`the server reports ${measured.rows.length} measured model(s)`);

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
await page.click('button[data-view="agents"]');
await page.waitForSelector('#orgTree .node', { timeout: 15000 });
await page.click(`#orgTree .node[data-node="${CAPABILITY}"]`);
await page.waitForFunction(
  (cap) => (document.getElementById('agentDetail').textContent || '').includes(cap),
  CAPABILITY, { timeout: 5000 });
const detail = await page.locator('#agentDetail').textContent();
ok('the agent detail panel opens');

// --- the section exists and is about this ---------------------------------
if (!detail.includes('Which model is good at this'))
  fail('the panel does not show the measured per-model record');
if (!detail.includes(`${measured.window_days} days`))
  fail('the panel does not say how far back it is looking');
ok('the section says what window it covers');

// --- every measured model is there, none invented -------------------------
for (const row of measured.rows) {
  if (!detail.includes(row.model)) fail(`${row.model} was measured and is not shown`);
}
ok(`all ${measured.rows.length} measured models shown`);

// --- the one being routed around is marked as such ------------------------
const bad = measured.rows.find((r) => r.model === measured.struggling);
if (!bad) fail('nothing is flagged as struggling -- seed a failing model first');
else {
  if (!detail.includes('routed around'))
    fail(`${bad.model} is failing ${Math.round((1 - bad.success_rate) * 100)}% of the time and is not marked`);
  if (!detail.includes(`${bad.runs} runs`))
    fail('the panel does not say how many runs the judgement rests on');
}
ok(`${measured.struggling} is shown as the one being routed around`);

// --- and the one there is not enough data on is NOT judged ----------------
// The whole point. A model with three runs beside a model with twelve,
// and only one of them judged.
const thin = measured.rows.find((r) => !r.enough_to_judge);
if (!thin) fail('nothing under the threshold was seeded, so this proves nothing');
else {
  if (!detail.includes('too few to judge'))
    fail(`${thin.model} has only ${thin.runs} runs and the panel judged it anyway`);
  if (thin.model === measured.struggling)
    fail('a model with too few runs was picked as the one to route around');
  const tags = await page.locator('#agentDetail .tag').allTextContents();
  const flagged = await page.evaluate((model) => {
    const rows = [...document.querySelectorAll('#agentDetail .kv, #agentDetail .row')];
    const mine = rows.find((r) => r.textContent.includes(model));
    return mine ? mine.textContent.includes('routed around') : null;
  }, thin.model);
  if (flagged) fail(`${thin.model} was marked "routed around" on ${thin.runs} runs`);
  ok(`${thin.model} (${thin.runs} runs) is shown but not judged`);
}

// --- it explains itself ---------------------------------------------------
if (!/never lowers one|not getting it right/.test(detail))
  fail('the panel shows numbers without saying what JARVIS does about them');
if (!detail.includes(String(measured.enough_runs)))
  fail('the panel does not say how many runs it takes before a model is judged');
ok('the panel says what it does with the numbers and when');

// --- narrow screens -------------------------------------------------------
for (const [w, h, label] of [[390, 844, 'iPhone'], [834, 1112, 'iPad']]) {
  const p2 = await (await browser.newContext({ viewport: { width: w, height: h } })).newPage();
  await p2.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('#app:not(.hidden)');
  await p2.click('button[data-view="agents"]');
  await p2.waitForSelector('#orgTree .node');
  await p2.click(`#orgTree .node[data-node="${CAPABILITY}"]`);
  await p2.waitForFunction(
    () => (document.getElementById('agentDetail').textContent || '')
      .includes('Which model is good at this'), null, { timeout: 5000 });
  const box = await p2.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, w: window.innerWidth,
  }));
  if (box.scrollW > box.w + 1)
    fail(`${label}: the new section pushes the page sideways (${box.scrollW} > ${box.w})`);
  ok(`${label} (${w}×${h}): no sideways scroll`);
  if (w === 390) await p2.screenshot({ path: process.argv[2] || 'which-model.png', fullPage: true });
}

const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
