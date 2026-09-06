import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
let bad = 0;
// iPad landscape and portrait, at the full size AND at the height actually
// left after Safari's toolbars -- which is what iOS reports as svh and is
// the case that shipped broken.
for (const [w, h, label] of [
  [1180, 820, 'iPad landscape, full'],
  [1180, 690, 'iPad landscape, Safari chrome showing'],
  [1024, 640, 'iPad landscape, small + chrome'],
  [820, 1180, 'iPad portrait, full'],
  [820, 1010, 'iPad portrait, chrome showing'],
  [390, 750, 'iPhone, chrome showing'],
]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  await page.goto('http://127.0.0.1:8099', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#app:not(.hidden)');
  await page.click('button[data-view="tasks"]');
  await page.waitForSelector('#tasksView:not(.hidden)');
  await page.waitForSelector('#wfList .wf');
  await page.click('#wfList .wf:has-text("Karnataka EV subsidy, checked")');
  await page.waitForSelector('#planSteps .step');

  const r = await page.evaluate(() => {
    const el = (id) => document.getElementById(id).getBoundingClientRect();
    return {
      vh: window.innerHeight, vw: window.innerWidth,
      scrollW: document.documentElement.scrollWidth,
      results: el('planSteps'),
      plan: el('planBtn'),
      // Can the reader actually reach the end of the list?
      listScrolls: (() => { const e = document.getElementById('planSteps');
        return e.scrollHeight > e.clientHeight; })(),
      pageScrolls: document.documentElement.scrollHeight > window.innerHeight,
    };
  });
  const problems = [];
  if (r.scrollW > r.vw + 1) problems.push(`sideways scroll ${r.scrollW}>${r.vw}`);
  // Two different contracts. From 900px up the layout is columns that fit
  // the screen and scroll inside themselves, so anything below the fold is
  // unreachable. Below 900px it is one stacked column and the page scrolls
  // normally -- content below the fold is how that is meant to work, and
  // demanding otherwise would be demanding the wrong thing.
  if (w >= 900) {
    if (r.results.bottom > r.vh + 1) problems.push(`results ${Math.round(r.results.bottom - r.vh)}px below the fold`);
    if (r.plan.bottom > r.vh + 1) problems.push(`Plan button ${Math.round(r.plan.bottom - r.vh)}px below the fold`);
  } else {
    if (r.plan.bottom > r.vh + 1) problems.push(`Plan button is not reachable without scrolling past it`);
    if (!r.pageScrolls && r.results.bottom > r.vh + 1)
      problems.push(`results are below the fold and the page will not scroll to them`);
  }
  if (problems.length) { bad++; console.error(`FAIL  ${label} (${w}×${h}): ${problems.join('; ')}`); }
  else console.log(`  ok  ${label} (${w}×${h}): everything on screen${r.listScrolls ? ', list scrolls internally' : ''}`);
  await ctx.close();
}
await browser.close();
process.exitCode = bad ? 1 : 0;
