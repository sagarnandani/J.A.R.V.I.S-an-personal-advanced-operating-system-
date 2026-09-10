/* Seeing what is actually running.
 *
 * The question that could not be answered: JARVIS said the Media Director
 * was working on it, there was no script, and from outside there was no
 * way to tell whether nothing had started or something had stalled.
 *
 * Seeded by seed_stalled_work.py, which leaves one step running long
 * enough to count as stuck and one piece half-made. Everything the page
 * does with that is real.
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

const live = await (await fetch(BASE + '/v1/activity')).json();
if (!live.busy) fail('nothing seeded — run seed_stalled_work.py first');

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');

// --- it is on the main screen, not buried in a tab ------------------------
await page.waitForSelector('#nowPanel:not(.hidden)', { timeout: 15000 });
await page.waitForSelector('#nowList .live', { timeout: 10000 });
const rows = await page.locator('#nowList .live').count();
if (rows < 1) fail('the panel is showing but lists nothing');
ok(`"Right now" is on the home screen with ${rows} live row(s)`);

// --- it names the work, the agent and how long ---------------------------
const text = await page.locator('#nowList').textContent();
const step = live.running[0];
if (step && !text.includes(step.capability.split('.').pop()))
  fail(`the running agent (${step.capability}) is not named`);
if (!/min ago|just now|waiting/.test(text)) fail('no elapsed time is shown');
ok(`each row names the agent, the work and how long: "${text.trim().slice(0, 70)}…"`);

// --- stuck looks different from working ----------------------------------
if (live.stalled) {
  if (!(await page.locator('#nowList .live.stalled').count()))
    fail('work that has been going far too long is drawn the same as work that is fine');
  const note = await page.locator('#nowNote').textContent();
  if (!/stuck, not slow/.test(note)) fail(`the note does not say it is stuck: "${note}"`);
  ok('work that has stalled is marked as stuck rather than shown as working');
}

// --- a piece being made links to itself -----------------------------------
if (live.pieces.length) {
  const link = page.locator('#nowList [data-now-piece]').first();
  if (!(await link.count())) fail('a piece being made cannot be opened from here');
  else {
    await link.click();
    await page.waitForSelector('#mediaView:not(.hidden)', { timeout: 10000 });
    // Wait for the panel to be about THIS piece. The empty placeholder is
    // longer than any length threshold, so waiting on size reads the
    // previous content and passes for the wrong reason.
    await page.waitForFunction(
      (topic) => document.getElementById('pieceTitle').textContent === topic,
      live.pieces[0].topic, { timeout: 10000 });
    const body = await page.locator('#pieceBody').textContent();
    if (!/steps/.test(body)) fail('the piece does not show which step it reached');
    ok('a piece being made opens on the Media tab showing the steps it reached');
  }
}

// --- and the panel disappears when nothing is running ---------------------
await page.route('**/v1/activity', async (route) => {
  await route.fulfill({ json: {
    busy: false, running: [], queued: [], waiting_approval: [], pieces: [],
    stalled: false, pieces_ever: 0,
    said: 'Nothing is running, and no piece of content has ever been started.',
  } });
});
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');
await page.waitForTimeout(1500);
if (!(await page.locator('#nowPanel').isHidden()))
  fail('the panel is showing "nothing", which is a panel you stop reading');
ok('it disappears entirely when nothing is running');

await page.screenshot({ path: process.argv[2] || 'now.png' });
const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
