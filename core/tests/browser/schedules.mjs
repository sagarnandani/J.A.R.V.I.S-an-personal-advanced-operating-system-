/* Setting up work that happens without you.
 *
 * The panel, the real routes, the real database. What is not exercised
 * here is the clock -- a browser check cannot wait until seven in the
 * morning -- so the schedule is made due directly and the tick is asked
 * for through the same code path the cron endpoint uses.
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

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');
await page.click('button[data-view="tasks"]');
await page.waitForSelector('#tasksView:not(.hidden)');
await page.waitForSelector('#schList');

// The list is filled by a fetch, so wait for it rather than reading the
// element the instant the view opens.
await page.waitForFunction(
  () => (document.getElementById('schList').textContent || '').trim().length > 0,
  null, { timeout: 10000 });
const empty = await page.locator('#schList').textContent();
if (!/Nothing scheduled/.test(empty)) fail(`expected an empty list, saw: ${empty}`);
ok('an empty schedule list says so plainly');

await page.fill('#schObjective', 'Check the Karnataka EV policy for changes');
await page.fill('#schTime', '07:00');
await page.click('#schAdd');
await page.waitForSelector('#schList .sch', { timeout: 10000 });

const row = await page.locator('#schList .sch').first().textContent();
if (!/Karnataka EV policy/.test(row)) fail('the schedule does not say what it will do');
if (!/daily 07:00/.test(row)) fail(`the time is not shown plainly: ${row}`);
if (!/not run yet/.test(row)) fail('it does not say it has never run');
ok(`a schedule reads as plain English: "${row.trim().replace(/\s+/g, ' ').slice(0, 60)}"`);

const note = await page.locator('#schNote').textContent();
if (!/60%/.test(note) || !/twice a day/.test(note))
  fail(`the spending limits are not stated: ${note}`);
ok('the limits on unattended spend are stated where they are set');

// Pausing must stop it, visibly.
await page.locator('#schList button', { hasText: 'pause' }).click();
await page.waitForSelector('#schList .sch.paused', { timeout: 5000 });
const listed = await (await fetch(BASE + '/v1/schedules')).json();
if (listed[0].enabled !== false) fail('the server still has it enabled');
ok('pausing stops it, on screen and on the server');

await page.locator('#schList button', { hasText: 'resume' }).click();
await page.waitForFunction(
  () => !document.querySelector('#schList .sch.paused'), null, { timeout: 5000 });
ok('resuming brings it back');

// Removing needs a confirmation, because it is not undoable.
page.once('dialog', (d) => d.accept());
await page.locator('#schList button.x').click();
await page.waitForFunction(
  () => /Nothing scheduled/.test(document.getElementById('schList').textContent),
  null, { timeout: 5000 });
if ((await (await fetch(BASE + '/v1/schedules')).json()).length !== 0)
  fail('it was removed on screen but not on the server');
ok('removing it asks first, then removes it everywhere');

if (errors.length) fail('console errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
