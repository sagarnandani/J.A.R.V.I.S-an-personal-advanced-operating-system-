/* A brief becoming a branch you can read.
 *
 * Seeded by seed_change_request.py, because on a check server the model
 * calls that write code cannot run. What that leaves is everything this
 * page is for: that a proposal shows its plan, the parts it said it could
 * NOT do, the diff, and the test result; that failing tests are shown as
 * failing rather than dressed up; that approving is available only on a
 * proposal; and that the page never offers to merge.
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
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

const seeded = await (await fetch(BASE + '/v1/dev')).json();
if (!seeded.requests.length) fail('nothing seeded — run seed_change_request.py first');

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');
await page.click('button[data-view="build"]');
await page.waitForSelector('#buildView:not(.hidden)', { timeout: 5000 });
await page.waitForSelector('#buildList .wf', { timeout: 15000 });
ok('Build tab opens and lists what has been proposed');

// --- the proposal ---------------------------------------------------------
const proposed = seeded.requests.find((r) => r.state === 'proposed');
if (!proposed) fail('no proposed change was seeded');
else {
  await page.click(`#buildList .wf[data-build="${proposed.id}"]`);
  await page.waitForFunction(
    (t) => document.getElementById('buildTitle').textContent === t,
    proposed.title, { timeout: 10000 });
  const body = await page.locator('#buildDetail').textContent();

  for (const want of ['Files', 'Steps', 'What it cannot do', 'Diff', 'Tests'])
    if (!body.includes(want)) fail(`the proposal does not show ${want}`);
  // The most useful part of a plan, and the part a system that wanted to
  // look capable would leave out.
  if (!/needs a Telegram bot token/.test(body))
    fail('what it said it could not do is not shown');
  ok('a proposal shows its plan, what it cannot do, the diff and the tests');

  // --- failing tests look like failing tests ----------------------------
  const tests = await page.locator('#buildDetail .tests').textContent();
  if (!/FAIL/.test(tests)) fail(`failing tests are not shown as failing: "${tests}"`);
  const red = await page.locator('#buildDetail .tests.fail').count();
  if (!red) fail('failing tests are not drawn differently from passing ones');
  ok(`failing tests are stated plainly: "${tests.trim()}"`);

  // --- the diff is readable and does not push the page sideways ---------
  const diff = await page.evaluate(() => {
    const el = document.querySelector('#buildDetail .diff');
    return el ? { scrolls: el.scrollWidth > el.clientWidth,
                  page: document.documentElement.scrollWidth,
                  win: window.innerWidth } : null;
  });
  if (!diff) fail('there is no diff on screen');
  else if (diff.page > diff.win + 1)
    fail(`the diff pushes the page sideways (${diff.page} > ${diff.win})`);
  ok('the diff scrolls inside its own box, not the page');

  // --- deciding ----------------------------------------------------------
  if (await page.locator('#approveBuildBtn').isHidden())
    fail('a proposal waiting for a decision offers no way to approve it');
  if (!(await page.locator('#writeItBtn').isHidden()))
    fail('a change already written is still offering to write it again');

  const buttons = await page.locator('#buildView button').allTextContents();
  if (buttons.some((b) => /merge|push|deploy/i.test(b)))
    fail(`the page offers to merge or push: ${buttons.join(', ')}`);
  ok('it offers approve and discard, and never merge or push');

  await page.click('#approveBuildBtn');
  await page.waitForFunction(
    () => /yours to merge/.test(document.getElementById('buildFooter').textContent || ''),
    null, { timeout: 10000 });
  const footer = await page.locator('#buildFooter').textContent();
  if (!/does not merge/.test(footer)) fail(`approving did not say what it did: "${footer}"`);
  ok(`approving records the decision and says the branch is yours`);
}

// --- a plan is not a proposal --------------------------------------------
const planned = seeded.requests.find((r) => r.state === 'planned');
if (planned) {
  await page.click(`#buildList .wf[data-build="${planned.id}"]`);
  await page.waitForFunction(
    (t) => document.getElementById('buildTitle').textContent === t,
    planned.title, { timeout: 10000 });
  if (!(await page.locator('#approveBuildBtn').isHidden()))
    fail('a plan can be approved, which would be approving something that does not exist');
  if (await page.locator('#writeItBtn').isHidden())
    fail('a plan offers no way to have it written');
  ok('a plan offers to be written, and cannot be approved');
}

await page.screenshot({ path: process.argv[2] || 'build.png' });
const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
