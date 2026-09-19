/* Every panel can actually be got to, on every screen he uses.
 *
 * Adding the Model keys panel put six panels in one column and one in
 * the other. On a laptop the column scrolled 70-93 pixels past the fold
 * and nothing said so -- an overlay scrollbar is invisible until you are
 * already scrolling -- so the panel existed and could not be found.
 *
 * Then moving it to the emptier column fixed the laptop and buried it at
 * y=1138 on a portrait iPad, which is worse, because that is the device
 * this is actually read on.
 *
 * Both of those are the same mistake: changing a layout by eye at one
 * size. This measures instead, at all of them.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let bad = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; bad = true; };
const ok = (m) => { if (!bad) out.push(m); bad = false; };

const SIZES = [
  ['laptop', 1366, 768],
  ['small laptop', 1366, 600],
  ['iPad landscape', 1180, 820],
  ['iPad portrait', 820, 1180],
  ['phone', 390, 844],
];

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });

for (const [label, width, height] of SIZES) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  await page.click('button[data-view="build"]');
  await page.waitForSelector('#buildView:not(.hidden)');
  await page.waitForTimeout(400);

  const report = await page.evaluate(() => {
    const view = document.getElementById('buildView');
    const panels = [...view.querySelectorAll('.panel')];
    const named = panels.map((panel) => {
      const title = panel.querySelector('h2');
      return { name: title ? title.textContent.trim() : '(untitled)', panel };
    });

    // Reachable means: scroll its own container to it and it lands
    // inside the window. A panel nothing can scroll to is unreachable
    // however tidy the markup is.
    const unreachable = [];
    for (const { name, panel } of named) {
      panel.scrollIntoView({ block: 'nearest' });
      const r = panel.getBoundingClientRect();
      const onScreen = r.bottom > 0 && r.top < window.innerHeight
                       && r.width > 0 && r.height > 0;
      if (!onScreen) unreachable.push(name);
    }

    // A column that scrolls must look like it does. Overlay scrollbars
    // take no layout width, which is exactly how a scrollable column
    // reads as a finished one.
    const columns = [...view.querySelectorAll('.col')].map((col) => ({
      scrolls: col.scrollHeight - col.clientHeight > 4,
      gutter: col.offsetWidth - col.clientWidth,
      overflow: getComputedStyle(col).overflowY,
    }));

    return { count: named.length, names: named.map((n) => n.name),
             unreachable, columns,
             bodyScrolls: document.documentElement.scrollHeight
                          > window.innerHeight };
  });

  if (report.count < 6)
    fail(`${label}: only ${report.count} panels found — the view did not render`);
  else if (report.unreachable.length)
    fail(`${label}: cannot reach ${report.unreachable.join(', ')}`);
  else ok(`${label}: all ${report.count} panels reachable`);

  // Where the column itself scrolls, it must advertise it. Where the
  // page scrolls instead, the page's own scrollbar is the affordance.
  const silent = report.columns.filter(
    (c) => c.scrolls && c.overflow === 'auto' && c.gutter < 4);
  if (silent.length)
    fail(`${label}: a column scrolls with no visible scrollbar (gutter ` +
         `${silent.map((c) => c.gutter).join(', ')}px)`);
  else ok(`${label}: nothing scrolls without showing that it does`);

  await ctx.close();
}

// --- the specific thing he could not find ---------------------------------
for (const [label, width, height] of [['iPad portrait', 820, 1180],
                                      ['laptop', 1366, 768]]) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  await page.click('button[data-view="build"]');
  await page.waitForSelector('#buildView:not(.hidden)');
  await page.waitForTimeout(400);

  const where = await page.evaluate(() => {
    const panel = document.getElementById('providerList').closest('.panel');
    const r = panel.getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom),
             viewport: window.innerHeight };
  });

  // Not "the top edge is technically inside the window" -- that passed
  // at y=1138 of an 1180-tall iPad, where 42 pixels of a 440-pixel panel
  // were showing and the fix that caused it went undetected. Enough of
  // it has to be on screen to be seen and used.
  const shown = Math.max(0, Math.min(where.bottom, where.viewport) -
                            Math.max(where.top, 0));
  const tall = where.bottom - where.top;
  const enough = Math.min(tall * 0.5, 200);

  if (shown < enough)
    fail(`${label}: only ${Math.round(shown)}px of the ${tall}px Model keys ` +
         `panel is on screen when the tab opens (needs ${Math.round(enough)}px) ` +
         `— it starts at y=${where.top} of ${where.viewport}`);
  else ok(`${label}: ${Math.round(shown)}px of Model keys is on screen ` +
          `without scrolling`);
  await ctx.close();
}

console.log(out.map((l) => '  ' + l).join('\n'));
await browser.close();
