/* This tab, as a pair of hands.
 *
 * You cannot run a program in the background on an iPad or a phone --
 * iOS does not allow it, and no amount of wanting it to changes that.
 * What you CAN do is the thing already open: this dashboard, in Safari,
 * on the desk. While it is on screen it can ask JARVIS for work and do
 * the small set of things a web page is actually able to do.
 *
 * That set is short on purpose, and every item was checked against what
 * a page can do WITHOUT a tap -- because a sidecar acts when JARVIS
 * asks, and nobody is standing there to approve each one:
 *
 *   speak      always works, once audio has been unlocked by any tap
 *   notify     in the page always; as a system notification only where
 *              the browser allows it, which on iOS means installed to
 *              the Home Screen
 *   browser    a link put in front of you, NOT a tab opened behind your
 *              back -- window.open() without a tap is blocked, and on
 *              iOS Safari it returns a Window that never navigates,
 *              which this project has already been bitten by once
 *   clipboard  write only, best effort; Safari wants a recent tap and
 *              says so rather than pretending
 *
 * It polls only while the tab is visible. A hidden tab is a tab iOS is
 * about to suspend, and a sidecar that claims to be listening while the
 * screen is off is lying. The one exception this project already found:
 * in Split View iPadOS does not fire visibilitychange, so JARVIS beside
 * another app keeps working -- which is exactly how you would use it.
 */

const SIDECAR_KEY = "jarvis.sidecar";
// Slower than the native sidecar's three seconds. This is somebody's
// iPad battery, and nothing here is urgent enough to justify twenty
// polls a minute.
const SIDECAR_POLL_MS = 6000;

let sidecarTimer = null;
let sidecarStopped = false;

function sidecarSaved() {
  try {
    return JSON.parse(localStorage.getItem(SIDECAR_KEY) || "null");
  } catch (e) { return null; }
}

function sidecarForget() {
  try { localStorage.removeItem(SIDECAR_KEY); } catch (e) { /* private mode */ }
}

// --- what this tab can actually do ----------------------------------------

async function sidecarSpeak(args) {
  const text = String(args.text || "").slice(0, 1000);
  if (!text.trim()) throw new Error("Nothing to say.");
  if (!("speechSynthesis" in window)) throw new Error("This browser cannot speak.");
  // Through the dashboard's own voice if it has one, so a sidecar
  // message sounds like JARVIS rather than like a different program.
  if (typeof window.say === "function") window.say(text);
  else window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  return { said: text };
}

async function sidecarNotify(args) {
  const text = String(args.text || "").slice(0, 300);
  const title = String(args.title || "JARVIS").slice(0, 100);

  // In the page first, because that always works. A system notification
  // is the nicer version, not the only version.
  sidecarShow(`${title}: ${text}`);

  if (!("Notification" in window)) {
    return { shown: "in the page", system: false,
             why: "This browser has no notifications outside the page." };
  }
  if (Notification.permission === "granted") {
    try {
      new Notification(title, { body: text });
      return { shown: "in the page and as a notification", system: true };
    } catch (e) {
      return { shown: "in the page", system: false, why: String(e) };
    }
  }
  return { shown: "in the page", system: false,
           why: Notification.permission === "denied"
             ? "Notifications are blocked for this site."
             : "Notifications have not been allowed yet. On an iPhone or "
               + "iPad they also need this page added to the Home Screen." };
}

async function sidecarBrowser(args) {
  const url = String(args.url || "");
  if (!/^https?:\/\//i.test(url)) {
    throw new Error(`I will only open http and https addresses, not ${url}`);
  }
  // A link you tap, never a tab opened behind your back. window.open()
  // without a tap is blocked everywhere, and on iOS Safari it returns a
  // Window that never navigates -- this project has already lost a day
  // to that once.
  sidecarShow(`JARVIS wants to open this:`, url);
  return { offered: url, opened: false,
           note: "Put in front of you as a link. A page cannot open a tab "
                 + "on its own, so this waits for your tap." };
}

async function sidecarClipboard(args) {
  if (!args.set) {
    throw new Error(
      "Reading the clipboard needs you to tap first, so JARVIS cannot do "
      + "it on its own in a browser. Paste it into the chat instead.");
  }
  const text = String(args.text || "");
  if (!navigator.clipboard) throw new Error("This browser has no clipboard access.");
  try {
    await navigator.clipboard.writeText(text);
    return { set: true };
  } catch (e) {
    // Safari wants a recent tap. Said plainly rather than reported as
    // done, because "I copied it" when nothing was copied is the exact
    // failure this project keeps guarding against.
    throw new Error(
      "The browser refused the clipboard without a recent tap. Nothing "
      + "was copied.");
  }
}

const SIDECAR_DOES = {
  speak: sidecarSpeak,
  notify: sidecarNotify,
  browser: sidecarBrowser,
  clipboard: sidecarClipboard,
};

// --- showing it ------------------------------------------------------------

function sidecarShow(line, link) {
  const box = document.getElementById("sidecarMessages");
  if (!box) return;
  const row = document.createElement("div");
  row.className = "perm";
  row.textContent = line;
  if (link) {
    const a = document.createElement("a");
    a.className = "btn";
    a.href = link;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "Open";
    a.style.marginLeft = ".5rem";
    row.appendChild(a);
  }
  box.prepend(row);
  while (box.children.length > 6) box.lastChild.remove();
}

// --- the loop --------------------------------------------------------------

async function sidecarRunOne(job, granted) {
  const capability = job.capability || "";
  // Checked here as well as on the server. The server decides what to
  // ask for; this decides what it is willing to do. Same rule as the
  // native sidecar, for the same reason.
  if (!granted.includes(capability)) {
    return { ok: false, result: {},
             error: `This device was not paired with '${capability}'. `
                    + `Refused here, whatever the server asked for.` };
  }
  const doer = SIDECAR_DOES[capability];
  if (!doer) {
    return { ok: false, result: {},
             error: `A browser cannot do '${capability}'.` };
  }
  try {
    return { ok: true, result: await doer(job.arguments || {}), error: "" };
  } catch (e) {
    return { ok: false, result: {}, error: String(e.message || e) };
  }
}

async function sidecarPoll() {
  const saved = sidecarSaved();
  if (!saved || sidecarStopped) return;

  // A hidden tab is a tab iOS is about to suspend. Claiming to be
  // listening while the screen is off would be a lie the owner cannot
  // check. (In Split View iPadOS never fires this, so JARVIS beside
  // another app keeps working -- which is how you would actually use it.)
  if (document.hidden) return;

  let answer;
  try {
    const res = await fetch("/v1/sidecar/poll", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: `Bearer ${saved.token}` },
      body: JSON.stringify({ reported: { machine: navigator.userAgent.slice(0, 180) } }),
    });
    if (res.status === 401) {
      sidecarShow("JARVIS no longer recognises this device. It was probably "
                  + "revoked. Pair it again if that was not deliberate.");
      sidecarForget();
      sidecarStop();
      sidecarPaint();
      return;
    }
    if (!res.ok) return;
    answer = await res.json();
  } catch (e) { return; /* offline; try again next tick */ }

  for (const job of answer.jobs || []) {
    const done = await sidecarRunOne(job, saved.capabilities || []);
    try {
      await fetch(`/v1/sidecar/jobs/${job.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   Authorization: `Bearer ${saved.token}` },
        body: JSON.stringify(done),
      });
    } catch (e) { /* it will expire; better than a wrong answer */ }
  }
}

function sidecarStart() {
  sidecarStopped = false;
  clearInterval(sidecarTimer);
  sidecarTimer = setInterval(sidecarPoll, SIDECAR_POLL_MS);
  sidecarPoll();
}

function sidecarStop() {
  sidecarStopped = true;
  clearInterval(sidecarTimer);
  sidecarTimer = null;
}

// --- pairing, and saying honestly what it can do --------------------------

async function sidecarPairThis(capabilities) {
  const name = (document.getElementById("sidecarName") || {}).value
    || sidecarGuessName();
  const res = await fetch("/v1/sidecars/this-browser", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name, capabilities,
      reported: { machine: navigator.userAgent.slice(0, 180) },
    }),
  });
  if (!res.ok) {
    const said = await res.json().catch(() => ({}));
    sidecarShow(said.detail || "That could not be paired.");
    return;
  }
  const got = await res.json();
  try {
    localStorage.setItem(SIDECAR_KEY, JSON.stringify({
      token: got.token, name: got.sidecar.name,
      capabilities: got.sidecar.capabilities,
    }));
  } catch (e) {
    sidecarShow("This browser will not save the pairing (private browsing?), "
                + "so it will be forgotten when you close the tab.");
  }
  sidecarStart();
  sidecarPaint();
}

function sidecarGuessName() {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua)) return "iPad";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return "Phone";
  if (/Macintosh/.test(ua)) return "Mac browser";
  if (/Windows/.test(ua)) return "PC browser";
  return "This browser";
}

function sidecarPaint() {
  const box = document.getElementById("sidecarThis");
  if (!box) return;
  const saved = sidecarSaved();

  if (!saved) {
    box.innerHTML =
      `<p class="note" style="margin-top:0">This ${esc(sidecarGuessName())} can `
      + `be a pair of hands for JARVIS while this tab is open — it can speak, `
      + `show you things, and offer you links to tap.</p>`
      + `<div class="row" style="margin-top:.5rem">`
      + `<button class="btn" id="sidecarPairBtn">Use this device</button></div>`;
    const btn = document.getElementById("sidecarPairBtn");
    if (btn) btn.onclick = () => sidecarPairThis(["speak", "notify", "browser"]);
    return;
  }

  const listening = !sidecarStopped && !document.hidden;
  box.innerHTML =
    kv("This device", `${esc(saved.name)} — ${esc((saved.capabilities || []).join(", "))}`)
    + kv("Right now", listening
        ? `listening`
        : `${unknown("not listening — this tab is in the background")}`)
    + `<p class="note">It works while this tab is on screen. On an iPad, `
    + `keep JARVIS beside another app in Split View and it keeps working.</p>`
    + `<div class="row" style="margin-top:.5rem">`
    + `<button class="btn" id="sidecarNotifyBtn">Allow notifications</button>`
    + `<button class="btn danger" id="sidecarForgetBtn">Stop using this device</button>`
    + `</div>`;

  const allow = document.getElementById("sidecarNotifyBtn");
  if (allow) {
    allow.onclick = async () => {
      if (!("Notification" in window)) {
        sidecarShow("This browser has no notifications outside the page.");
        return;
      }
      const answer = await Notification.requestPermission();
      sidecarShow(answer === "granted"
        ? "Notifications allowed."
        : "Not allowed. On an iPhone or iPad this page also has to be added "
          + "to the Home Screen before notifications work at all.");
    };
  }
  const forget = document.getElementById("sidecarForgetBtn");
  if (forget) {
    forget.onclick = async () => {
      const saved2 = sidecarSaved();
      sidecarStop();
      sidecarForget();
      if (saved2) {
        // Revoked on the server too, or it sits in his list for ever
        // looking like a device that might answer.
        await fetch(`/v1/sidecars/${encodeURIComponent(saved2.name)}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "revoked" }),
        }).catch(() => {});
      }
      sidecarPaint();
      if (typeof loadReach === "function") loadReach();
    };
  }
}

// Coming back to the tab starts it listening again; leaving stops it.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { clearInterval(sidecarTimer); sidecarTimer = null; }
  else if (sidecarSaved() && !sidecarStopped) sidecarStart();
  sidecarPaint();
});

// Painted on load, and again whenever the panel it lives in is
// refreshed. Without the first call the panel was an empty box until
// something had already been paired -- so the one state that needed an
// explanation, "you have not set this up yet", was the one state that
// showed nothing at all.
function sidecarReady() {
  sidecarPaint();
  if (sidecarSaved()) sidecarStart();
}

window.sidecarPaint = sidecarPaint;
window.sidecarReady = sidecarReady;

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", sidecarReady);
} else {
  sidecarReady();
}
