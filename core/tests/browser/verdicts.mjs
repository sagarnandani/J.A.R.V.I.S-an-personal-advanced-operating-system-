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

async function open(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  await page.click('button[data-view="tasks"]');
  await page.waitForSelector('#tasksView:not(.hidden)');
  await page.waitForSelector('#wfList .wf');
  // By name, not by position: "the newest run" is whatever the other
  // check happened to leave behind, which made this pass for the wrong
  // reason and then fail for one.
  await page.click('#wfList .wf:has-text("Karnataka EV subsidy, checked")');
  await page.waitForSelector('#planSteps .step');
  return page;
}

/* --- landscape iPad: the richest rendering path ------------------------- */
const page = await open(1180, 820);

const steps = await page.locator('#planSteps .step').count();
if (steps !== 2) fail(`expected 2 steps, saw ${steps}`);
ok('a two-step research → factcheck run renders both steps');

for (const v of ['supported', 'contradicted', 'unverified']) {
  if (await page.locator(`.verdict.${v}`).count() !== 1) fail(`no ${v} verdict rendered`);
}
ok('every verdict rendered with its own class');

// The colours have to actually differ, or the classes are decoration.
const colours = await page.evaluate(() =>
  ['supported', 'contradicted', 'unverified'].map((v) =>
    getComputedStyle(document.querySelector('.verdict.' + v)).color));
if (new Set(colours).size !== 3) fail(`verdict colours are not distinct: ${colours}`);
ok(`verdicts are visually distinct (${colours.join(', ')})`);

const links = await page.locator('#planSteps a.src').count();
if (links < 3) fail(`only ${links} source link(s) rendered`);
const href = await page.locator('#planSteps a.src').first().getAttribute('href');
const rel = await page.locator('#planSteps a.src').first().getAttribute('rel');
if (!/^https?:\/\//.test(href)) fail(`source link is not a url: ${href}`);
if (!/noopener/.test(rel || '')) fail('source links open without noopener');
ok(`${links} source links, real urls, opened safely`);

const text = await page.locator('#planSteps').textContent();
if (!/Rs\.1,50,000/.test(text)) fail('the contradicted claim text is missing');
if (!/no source addressed the end date/i.test(text)) fail('the unresolved note is missing');
ok('claim text and unresolved notes carried through');

// Nothing may run off the bottom or the side.
const fit = await page.evaluate(() => ({
  scrollW: document.documentElement.scrollWidth, w: window.innerWidth,
  panelBottom: document.getElementById('planSteps').getBoundingClientRect().bottom,
  vh: window.innerHeight,
}));
if (fit.scrollW > fit.w + 1) fail(`sideways scroll (${fit.scrollW} > ${fit.w})`);
if (fit.panelBottom > fit.vh + 1) fail(`results panel runs ${Math.round(fit.panelBottom - fit.vh)}px past the fold`);
ok('fits the landscape viewport exactly');

await page.screenshot({ path: process.argv[2] || 'verdicts.png' });

/* --- held-portrait iPad and a phone ------------------------------------- */
for (const [w, h, label] of [[820, 1180, 'iPad portrait'], [390, 844, 'iPhone']]) {
  const p = await open(w, h);
  const r = await p.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, w: window.innerWidth,
    plan: document.getElementById('planBtn').getBoundingClientRect(),
  }));
  let sound = true;
  if (r.scrollW > r.w + 1) { fail(`${label}: sideways scroll (${r.scrollW} > ${r.w})`); sound = false; }
  if (await p.locator('.verdict.contradicted').count() !== 1) { fail(`${label}: verdicts missing`); sound = false; }
  if (sound) ok(`${label} (${w}×${h}): no sideways scroll, verdicts intact`);
  if (label === 'iPhone') await p.screenshot({ path: process.argv[3] || 'verdicts-phone.png' });
}

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
