/* What came in and what went out.
 *
 * Rows normally arrive by being said out loud and read out of the
 * exchange; here they are added through the same API the extraction pass
 * writes to, and what is checked is the panel: that the figures are
 * shown, that they are labelled as partial, and that a misheard one can
 * be removed and the totals actually change.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
const ok = (m) => { if (!failed) out.push(m); failed = false; };

// Clear anything a previous run left, so the totals below mean something.
for (const m of (await (await fetch(BASE + '/v1/money?limit=100')).json()).recent)
  await fetch(`${BASE}/v1/money/${m.id}`, { method: 'DELETE' });

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');
await page.waitForFunction(
  () => (document.getElementById('moneyList').textContent || '').trim().length > 0,
  null, { timeout: 10000 });

const empty = await page.locator('#moneyList').textContent();
if (!/Nothing yet/.test(empty)) fail(`an empty ledger should say so: ${empty}`);
if (!/40,000 from the Bengaluru shoot/.test(empty))
  fail('it does not show how to add one');
ok('an empty ledger says so, and how to fill it');

const add = async (body) => (await fetch(BASE + '/v1/money', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})).json();

await add({ direction: 'in', amount_inr: 40000, what: 'Bengaluru shoot', category: 'client work' });
await add({ direction: 'out', amount_inr: 5000, what: 'Drone battery' });
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('#moneyList .mv', { timeout: 10000 });

const totals = await page.evaluate(() => ({
  in: document.getElementById('moneyIn').textContent,
  out: document.getElementById('moneyOut').textContent,
  net: document.getElementById('moneyNet').textContent,
}));
if (!/40,000/.test(totals.in)) fail(`money in is wrong: ${totals.in}`);
if (!/5,000/.test(totals.out)) fail(`money out is wrong: ${totals.out}`);
if (!/35,000/.test(totals.net)) fail(`net is wrong: ${totals.net}`);
ok(`totals add up and read as rupees (${totals.in} in, ${totals.out} out, ${totals.net} net)`);

const note = await page.locator('#moneyNote').textContent();
if (!/no bank feed/.test(note))
  fail(`it does not say the figures are partial: "${note}"`);
ok('it says the figures are only what JARVIS was told');

const rows = await page.locator('#moneyList .mv').allTextContents();
if (!rows.some((r) => /\+.*40,000/.test(r))) fail('money in is not marked as coming in');
if (!rows.some((r) => /−.*5,000/.test(r))) fail('money out is not marked as going out');
ok('in and out are told apart at a glance');

// A misheard figure has to be removable, and the totals must follow.
page.once('dialog', (d) => d.accept());
await page.locator('#moneyList .mv', { hasText: 'Drone battery' }).locator('button').click();
await page.waitForFunction(
  () => !/Drone battery/.test(document.getElementById('moneyList').textContent),
  null, { timeout: 5000 });
const after = await page.locator('#moneyNet').textContent();
if (!/40,000/.test(after)) fail(`the totals did not follow the removal: ${after}`);
ok('removing a misheard figure asks first, then changes the totals');

if (errors.length) fail('console errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
