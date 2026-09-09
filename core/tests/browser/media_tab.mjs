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
const blocked = [];
page.on('requestfailed', (r) => blocked.push(r.url()));

// The scan needs a real scout, which needs a key this server does not
// have. Only the scan's ANSWER is faked, at the network boundary; the
// panel, the ranking display, the click through to production and every
// piece below are the real thing.
await page.route('**/v1/media/scan', async (route) => {
  if (route.request().method() !== 'POST') return route.continue();
  await route.fulfill({ json: { workflow_id: 'faked', theme: 'x', state: 'running' } });
});
await page.route('**/v1/media/scan/faked', async (route) => {
  await route.fulfill({ json: {
    workflow_id: 'faked', state: 'completed', reason: '',
    nothing_worth_covering: false,
    opportunities: [
      { title: 'The benchmark number everyone is quoting', brand: 'ai_media',
        why_now: 'Three outlets rounded it up this morning.',
        reason_to_exist: 'The card gives a different number than the coverage.',
        score: 0.71 },
      { title: 'A tool launch with nothing behind it', brand: 'ai_media',
        why_now: 'Launched today.', reason_to_exist: '', score: 0.22 },
    ],
  } });
});

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
ok('signed in (dev mode), dashboard visible');

// --- the tab opens --------------------------------------------------------
await page.click('button[data-view="media"]');
await page.waitForSelector('#mediaView:not(.hidden)', { timeout: 5000 });
if (!(await page.locator('.grid:not(#mediaView):not(#tasksView):not(#settingsView)').first().isHidden()))
  fail('the home grid is still showing behind the Media view');
ok('Media tab opens');

// --- it fits, which is the failure that keeps recurring -------------------
const box = await page.evaluate(() => {
  const r = document.getElementById('scanBtn').getBoundingClientRect();
  return { bottom: r.bottom, vh: window.innerHeight,
           scrollW: document.documentElement.scrollWidth, w: window.innerWidth };
});
if (box.bottom > box.vh) fail(`the Look button is ${Math.round(box.bottom - box.vh)}px below the fold`);
if (box.scrollW > box.w + 1) fail(`the page scrolls sideways (${box.scrollW} > ${box.w})`);
ok('controls on screen, no sideways scroll');

// --- the brands come from the server, not from the page -------------------
const brands = await page.locator('#mediaBrand option').count();
if (brands < 2) fail(`the brand picker has ${brands} option(s); there are two properties`);
ok(`brand picker offers ${brands} properties`);

// --- a scan ranks, and the queue is readable ------------------------------
await page.fill('#mediaTheme', 'AI model releases this week');
await page.click('#scanBtn');
await page.waitForSelector('#oppList .opp', { timeout: 15000 });
const opps = await page.locator('#oppList .opp').count();
if (opps !== 2) fail(`expected 2 opportunities, got ${opps}`);
const firstScore = await page.locator('#oppList .opp .score').first().textContent();
if (!/0\.71/.test(firstScore)) fail(`the score is not shown: "${firstScore}"`);
ok(`scan rendered ${opps} ranked opportunities`);

// --- the seeded pieces ----------------------------------------------------
await page.waitForSelector('#pieceList .wf', { timeout: 10000 });
const pieces = await page.locator('#pieceList .wf').count();
if (pieces < 2) fail(`expected the two seeded pieces, got ${pieces}`);

// A decision against publishing must not read as a fault.
const declined = page.locator('#pieceList .wf .s.declined').first();
if (!(await declined.count())) fail('the declined piece is not shown as declined');
const declinedText = await declined.textContent();
if (/fail/i.test(declinedText)) fail(`a decision reads as a failure: "${declinedText}"`);
ok(`${pieces} pieces listed; "decided against" is not shown as a failure`);

// --- the economics line shows both numbers, separately --------------------
const econ = await page.locator('#econNote').textContent();
if (!/billed/.test(econ) || !/paid rates/.test(econ))
  fail(`the economics line does not separate the two figures: "${econ}"`);
ok(`economics: "${econ.trim()}"`);

// --- reading a piece ------------------------------------------------------
await page.click('#pieceList .wf .s.ready');
await page.waitForSelector('#pieceBody .beat', { timeout: 10000 });
const bodyText = await page.locator('#pieceBody').textContent();
if (!/71\.2/.test(bodyText)) fail('the script itself is not on screen');
if (!/rests on/.test(bodyText)) fail('the citations are not shown, so nothing can be checked');
if (!/billed/.test(bodyText)) fail('the piece does not say what it cost');
ok('the script, its citations and both cost figures are on screen');

// --- approving is the owner's, and only once ------------------------------
if (await page.locator('#pieceButtons').isHidden())
  fail('a piece waiting for approval offers no way to approve it');
await page.click('#approveBtn');
await page.waitForFunction(
  () => document.getElementById('pieceButtons').classList.contains('hidden'),
  null, { timeout: 10000 });
const state = await page.locator('#pieceState').textContent();
if (!/approved/i.test(state)) fail(`after approving, the piece says: "${state}"`);
ok('approved once, and the buttons are gone afterwards');

// --- a declined piece is not decidable ------------------------------------
await page.click('#pieceList .wf .s.declined');
await page.waitForTimeout(500);
if (!(await page.locator('#pieceButtons').isHidden()))
  fail('a piece that was decided against is being offered for approval');
ok('a decided-against piece offers no decision');

await page.screenshot({ path: process.argv[2] || 'media.png' });
// Third-party resources are blocked by this sandbox and each one logs a
// console error. Our own failed requests are caught by the check below,
// which is the one that matters.
const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));
const ours = blocked.filter((u) => u.startsWith(BASE));
if (ours.length) fail('requests to JARVIS failed: ' + ours.join(' | '));
if (blocked.length) ok(`(${blocked.length} third-party request(s) blocked by this sandbox: ${[...new Set(blocked.map((u) => new URL(u).host))].join(', ')})`);

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
