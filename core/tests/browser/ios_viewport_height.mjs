/* The iOS viewport-height trap, checked where it actually lives.
 *
 * On iOS Safari `100vh` (and `lvh`) is the LARGE viewport: the height the
 * page would have if the toolbars were hidden. What you can see is
 * smaller. So a box sized `height: calc(100vh - x)` is laid out against a
 * number bigger than the screen and its bottom edge is unreachable --
 * which shipped once, and put 50px of the results list past the edge of
 * an iPad.
 *
 * `svh` is the small viewport: toolbars showing, always what is really
 * visible. `dvh` is whatever is visible right now. Either is safe.
 *
 * This used to measure pixels: render at the height iOS reports and
 * assert everything fits inside the height iOS shows. That models `vh`
 * correctly and `svh` wrongly -- Chromium has no toolbar, so its `svh`
 * equals its `vh`, and an `svh`-sized box looked oversized when on a real
 * iPad it is bounded correctly. It failed on a layout that was fine.
 *
 * So it reads the rule instead. Every height that is capped by the
 * viewport must use `svh` or `dvh`, or offer one as the line after a
 * plain `vh` fallback. That is exactly the thing that went wrong, and
 * unlike a pixel count it cannot be fooled by the browser doing the
 * check.
 */
import { readFileSync } from 'fs';

const CSS = readFileSync(new URL('../../static/index.html', import.meta.url), 'utf8');
const out = [];
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; };

// `height` and `max-height` clip. `min-height` can only make a box taller,
// which leaves the page scrollable rather than cutting content off, so a
// plain vh there is not the trap.
// (?<![a-z]) rather than \b: in "30vh" the digit and the v are both
// word characters, so \bvh matches nothing at all. The lookbehind also
// keeps svh, dvh and lvh apart from a bare vh.
const RISKY = /(^|[;{}\s])(max-height|height)\s*:\s*([^;{}]*(?<![a-z])(?:vh|lvh)\b[^;{}]*)/g;

const lines = CSS.split('\n');
let found = 0;

for (let i = 0; i < lines.length; i++) {
  RISKY.lastIndex = 0;
  const m = RISKY.exec(lines[i]);
  if (!m) continue;
  found++;
  const property = m[2];
  const value = m[3].trim();

  // Safe when the same declaration already uses a small/dynamic unit, or
  // when the very next line repeats the property with one -- the standard
  // fallback pair, older browsers taking the first and everything else
  // the second.
  const selfSafe = /\b(svh|dvh)\b/.test(value);
  const nextLine = lines[i + 1] || '';
  const overridden = new RegExp(`${property}\\s*:[^;]*(svh|dvh)\\b`).test(nextLine);

  if (selfSafe || overridden) continue;
  fail(`line ${i + 1}: "${property}: ${value}" is sized against the LARGE ` +
       `viewport, so on an iPad its bottom edge is off screen. Use svh ` +
       `(or dvh), with a plain vh line before it as the fallback.`);
}

if (!found) fail('no viewport-height rules found at all — has the file moved?');
else out.push(`${found} viewport-sized height rule(s), none against the large viewport`);

// And the specific shape that shipped broken, so the lesson stays named.
if (/height:\s*calc\(100vh[^)]*\)/.test(CSS))
  fail('a calc(100vh - x) height is back; that is the exact rule that put ' +
       'the results list past the edge of an iPad');
else out.push('no calc(100vh - x) heights, which is the form that shipped broken');

console.log(out.map((l) => '  ok  ' + l).join('\n'));
