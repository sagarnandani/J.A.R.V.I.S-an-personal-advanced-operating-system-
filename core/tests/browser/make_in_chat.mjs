/* Asking JARVIS in the chat box to write something, and getting it.
 *
 * This is the bug the owner reported, as a check. He asked for a script,
 * JARVIS said it was displaying it on screen, and nothing appeared: it
 * knew media.script existed, had no way to reach it from a conversation,
 * and narrated the action instead.
 *
 * The mock provider cannot decide to mark a message, so the marked reply
 * is injected at the network boundary. Everything downstream is real: the
 * real card, the real media endpoint, the real Director, the real content
 * record, and the real Media tab it lands on.
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

// A model that decided this one is a piece of content, not a look-up.
await page.route('**/v1/message', async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.reply = 'I can put that together properly, sir.';
  body.offer = {
    objective: 'the benchmark number everyone is quoting wrong',
    kind: 'make',
    does: 'research, verify and draft this for you to approve',
    cost_note: 'this would be the first one, so I have no measurement yet',
    typical_cost_inr: null,
  };
  await route.fulfill({ response: res, json: body });
});

// Nothing else is stubbed. The check server has no model key, so the
// chain's first step fails honestly and the piece settles as failed --
// which is the correct outcome here and still exercises everything this
// file is about: the route the card takes, the endpoint it posts to, the
// record that gets written, and the tab the link opens. What a finished
// piece looks like is media_tab.mjs's job, from seeded data.

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');

// --- the card says which route it would take -----------------------------
await page.fill('#msg', 'Write me a script about the benchmark everyone is quoting');
await page.click('#sendBtn');
await page.waitForSelector('.offer', { timeout: 30000 });
const card = await page.locator('.offer .what').first().textContent();
if (!/research, verify and draft/.test(card))
  fail(`the card does not say what it would do: "${card}"`);
if (/look this up/.test(card))
  fail('a request to write something was offered as a look-up');
ok('the card says it would research, verify and draft, not look up');

// --- accepting starts the real chain, not a planned workflow -------------
const posted = [];
page.on('request', (r) => {
  if (r.method() === 'POST') posted.push(new URL(r.url()).pathname);
});
await page.click('.offer button.go');
await page.waitForFunction(
  () => /Rs\./.test(document.querySelector('.offer')?.textContent || ''),
  null, { timeout: 90000 });

if (!posted.includes('/v1/media/produce'))
  fail(`accepting posted to ${posted.join(', ') || 'nothing'}`);
if (posted.includes('/v1/workflows'))
  fail('a piece was started through the planner, which skips the gates');
ok('accepting goes to the Media Director, never through the planner');

// --- the answer names the piece and where it is --------------------------
const result = await page.locator('.offer').first().textContent();
if (!/at paid rates/.test(result)) fail('the result does not show both cost figures');
const link = page.locator('.offer [data-open-piece]');
if (!(await link.count())) fail('there is no way to get to the piece from the chat');
ok(`the chat answer carries the outcome and a way to open it: "${result.slice(0, 80).trim()}…"`);

// --- and it actually opens it --------------------------------------------
await link.click();
await page.waitForSelector('#mediaView:not(.hidden)', { timeout: 10000 });
await page.waitForFunction(
  () => (document.getElementById('pieceState')?.textContent || '').trim().length > 0,
  null, { timeout: 10000 });
const state = await page.locator('#pieceState').textContent();
const shown = await page.locator('#pieceBody').textContent();
if (!state.trim()) fail('the Media tab opened on nothing');
ok(`the link opens the piece on the Media tab: "${state.trim().slice(0, 70)}"`);

// --- the record exists, whatever the outcome was -------------------------
const pieces = await (await fetch(BASE + '/v1/media/pieces?limit=1')).json();
if (!pieces.length) fail('nothing was recorded, so nothing was really made');
else if (pieces[0].state === 'producing') fail('the piece never settled');
else ok(`recorded as "${pieces[0].state}" — ${(pieces[0].reason || '').slice(0, 60)}`);

await page.screenshot({ path: process.argv[2] || 'make.png' });
const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
