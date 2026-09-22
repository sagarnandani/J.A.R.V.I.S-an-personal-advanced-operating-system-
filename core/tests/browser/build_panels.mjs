/* The new panels on the Build tab: a trial, and what JARVIS may reach.
 *
 * Two things only a browser can check. That the panels render what the
 * server actually said -- each is compared against its own endpoint, so
 * a panel that had drifted into showing something plausible fails here.
 * And that adding them did not push anything off the screen, which is
 * exactly what happened the last time this column grew.
 *
 * Needs seed_trial.py, and a server with COMPUTER_ACCESS=true.
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

// What the server says, fetched independently of the page.
const trial = await (await fetch(BASE + '/v1/trials')).json();
const reach = await (await fetch(BASE + '/v1/computer')).json();
const web = await (await fetch(BASE + '/v1/search-policy')).json();
if (!trial.running) fail('no trial running — run seed_trial.py first');
if (!reach.enabled) fail('computer access is off — start the server with COMPUTER_ACCESS=true');
ok('the server has a trial running and computer access on');

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
await page.click('button[data-view="build"]');
await page.waitForSelector('#buildView:not(.hidden)', { timeout: 5000 });
await page.waitForFunction(
  () => (document.getElementById('trialSaid').textContent || '').length > 0,
  null, { timeout: 10000 });
ok('Build tab opens and the trial panel fills');

// --- the trial panel says what the server said ---------------------------
const trialText = await page.locator('#trialPanel').textContent();
// Scoped to the rows, NOT the whole panel. The server's verdict sentence
// sits in the same panel and already contains both rates and both run
// counts -- so asserting against the panel passed happily with the rows
// showing one arm and no run counts at all.
const armsText = await page.locator('#trialArms').textContent();
const v = trial.running.verdict;
if (!trialText.includes(trial.running.capability))
  fail('the trial panel does not say which agent is being tried');
for (const [label, arm] of [['candidate', v.candidate], ['baseline', v.baseline]]) {
  const shown = `${Math.round(arm.success_rate * 100)}%`;
  if (!armsText.includes(shown))
    fail(`the ${label}'s rate (${shown}) is not in the rows`);
  if (!armsText.includes(`${arm.runs} run`))
    fail(`the ${label}'s run count (${arm.runs}) is not in the rows`);
  if (!armsText.includes(`v${arm.version}`))
    fail(`the ${label} is not labelled with its version (v${arm.version})`);
}
ok('both arms shown, each with its version, rate and run count');

// The thresholds, because "90% against 55%" means nothing without them.
if (!trialText.includes(String(trial.runs_needed_each)))
  fail('the panel does not say how many runs it takes before judging');
if (!trialText.includes(String(Math.round(trial.margin * 100))))
  fail('the panel does not say how big a gap has to be');
ok('it says what it takes before a difference counts');

// --- and the buttons are the owner's -------------------------------------
for (const id of ['promoteTrialBtn', 'rejectTrialBtn', 'abandonTrialBtn']) {
  if (!(await page.locator('#' + id).isVisible()))
    fail(`#${id} is not on screen while a trial is running`);
}
ok('promote, reject and stop are all offered');

// --- what JARVIS may reach -----------------------------------------------
const reachText = await page.locator('#reachPanel').textContent();
if (!reachText.includes(reach.said)) fail('the reach panel does not say the server line');
for (const action of reach.runs_without_asking.slice(0, 3)) {
  if (!reachText.includes(action.what))
    fail(`'${action.what}' is a check JARVIS can run and is not shown`);
  if (!reachText.includes(action.command))
    fail(`the actual command for '${action.what}' is not shown`);
}
// The half that matters: everything ELSE asks.
if (!reachText.includes('does not run until you say yes'))
  fail('the panel lists what it can do without saying everything else asks');
ok(`${reach.runs_without_asking.length} checks listed, with the rule about the rest`);

if (!reachText.includes(web.means))
  fail('the search policy is not shown beside what it can reach');
ok('the search policy is shown in words');

// --- and nothing was pushed off the screen -------------------------------
// The last time this column grew, a panel ended up 134px below the fold
// and the owner reported the tab as broken.
for (const [w, h, label] of [[1180, 820, 'iPad landscape'],
                             [820, 1180, 'iPad portrait'],
                             [390, 844, 'iPhone']]) {
  const p2 = await (await browser.newContext({ viewport: { width: w, height: h } })).newPage();
  await p2.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('#app:not(.hidden)');
  await p2.click('button[data-view="build"]');
  await p2.waitForFunction(
    () => (document.getElementById('trialSaid').textContent || '').length > 0,
    null, { timeout: 10000 });

  const box = await p2.evaluate(() => {
    const spills = [];
    for (const id of ['trialPanel', 'reachPanel']) {
      const el = document.getElementById(id);
      const r = el.getBoundingClientRect();
      // Content taller than the box it is drawn in is the "panels look
      // merged" bug: the text runs straight through the border.
      if (el.scrollHeight > Math.ceil(r.height) + 2)
        spills.push(`${id} (${el.scrollHeight} > ${Math.round(r.height)})`);
    }
    return { scrollW: document.documentElement.scrollWidth,
             w: window.innerWidth, spills };
  });
  if (box.scrollW > box.w + 1)
    fail(`${label}: the page scrolls sideways (${box.scrollW} > ${box.w})`);
  if (box.spills.length)
    fail(`${label}: content spills out of ${box.spills.join(', ')}`);

  // Reachable by scrolling is the bar, not visible without scrolling.
  const reachable = await p2.evaluate(() => {
    const el = document.getElementById('reachPanel');
    el.scrollIntoView();
    const r = el.getBoundingClientRect();
    return r.top < window.innerHeight && r.bottom > 0 && r.height > 40;
  });
  if (!reachable) fail(`${label}: the reach panel cannot be scrolled to`);
  ok(`${label} (${w}×${h}): both panels whole, reachable, no sideways scroll`);
  if (w === 390) await p2.screenshot({ path: process.argv[2] || 'build-panels.png', fullPage: true });
}

const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
