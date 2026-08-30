/* JARVIS dashboard.
 *
 * The one rule: nothing on screen is invented. Every figure comes from
 * GET /v1/dashboard, which measures it. Where JARVIS cannot yet know
 * something, the panel says so rather than showing a plausible number --
 * a dashboard you cannot trust is worse than no dashboard.
 */
const $ = (id) => document.getElementById(id);
const gate = $("gate"), app = $("app");

// Sign-in lives in a cookie the server sets, so nothing about it is kept
// here -- page memory dies on reload, which is what used to make JARVIS
// appear to forget a sign-in that had actually worked.
const api = (path, opts = {}) =>
  fetch(path, {
    ...opts,
    credentials: "same-origin",
    headers: { ...(opts.headers || {}), "Content-Type": "application/json" },
  });

const esc = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };
const pad = (n) => String(n).padStart(2, "0");

/* ---------------------------------------------------------------- clock */
function tick() {
  const d = new Date();
  let h = d.getHours();
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  $("clock").innerHTML = `${pad(h)}:${pad(d.getMinutes())} <small>${ampm} ${pad(d.getSeconds())}</small>`;
  $("dateBig").textContent = d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
  $("dayName").textContent = d.toLocaleDateString(undefined, { weekday: "long" }).toUpperCase();
}
setInterval(tick, 1000); tick();

/* ------------------------------------------------------------- waveform */
// 22 bars that move only while JARVIS is actually busy. A waveform that
// dances at rest is decoration pretending to be information.
const wave = $("wave");
for (let i = 0; i < 22; i++) wave.appendChild(document.createElement("i"));
const bars = [...wave.children];
let waveTimer = null;
function setBusy(on) {
  $("reactor").classList.toggle("busy", on);
  wave.classList.toggle("active", on);
  clearInterval(waveTimer);
  if (on) {
    waveTimer = setInterval(() => {
      bars.forEach((b, i) => {
        b.style.height = 4 + Math.abs(Math.sin(Date.now() / 190 + i * 0.7)) * 20 + "px";
      });
    }, 70);
  } else {
    bars.forEach((b) => (b.style.height = "4px"));
  }
}
setBusy(false);

/* ----------------------------------------------------------------- voice */
// Safari's built-in speech engine: no API, no key, no cost. Voices load
// asynchronously and iOS will not speak until the page has had a real
// tap, so both are handled rather than assumed.
let speakOn = false;
try { speakOn = localStorage.getItem("jarvis.speak") === "1"; } catch (e) {}
const canSpeak = "speechSynthesis" in window;

function speak(text) {
  if (!speakOn || !canSpeak || !text) return;
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.slice(0, 600));
    const voices = speechSynthesis.getVoices();
    // Prefer a British male voice -- JARVIS is Paul Bettany, after all.
    const pick =
      voices.find((v) => /en-GB/i.test(v.lang) && /(daniel|male|arthur)/i.test(v.name)) ||
      voices.find((v) => /en-GB/i.test(v.lang)) ||
      voices.find((v) => /^en/i.test(v.lang));
    if (pick) u.voice = pick;
    u.rate = 1.02; u.pitch = 0.92;
    speechSynthesis.speak(u);
  } catch (e) { /* never let speech break the reply */ }
}
if (canSpeak) speechSynthesis.getVoices();

/* ------------------------------------------------------- speech-to-text */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, listening = false;
function setupMic() {
  const btn = $("micBtn");
  if (!SR) {
    // Not an error worth shouting about: the iPad keyboard has a
    // microphone key that does the same job, better.
    btn.disabled = true;
    btn.title = "Use the microphone key on your keyboard to dictate";
    return;
  }
  btn.onclick = () => {
    if (listening) { recog && recog.stop(); return; }
    recog = new SR();
    recog.lang = "en-IN";
    recog.interimResults = true;
    recog.onstart = () => { listening = true; btn.classList.add("rec"); setBusy(true); };
    recog.onend = () => { listening = false; btn.classList.remove("rec"); setBusy(false); };
    recog.onerror = () => { listening = false; btn.classList.remove("rec"); setBusy(false); };
    recog.onresult = (e) => {
      const said = [...e.results].map((r) => r[0].transcript).join("");
      $("msg").value = said;
      if (e.results[e.results.length - 1].isFinal) { recog.stop(); send(); }
    };
    try { recog.start(); } catch (e) { /* already running */ }
  };
}

/* ------------------------------------------------------------ dashboard */
let lastReplyMs = null;

function renderDashboard(d) {
  $("ownerLine").textContent = d.owner || "";
  $("cfgOwner").textContent = d.owner || "—";

  const stopped = d.status.emergency_stop;
  $("statusText").textContent = stopped ? "STOPPED" : "ONLINE";
  $("statusText").classList.toggle("stopped", stopped);
  $("reactor").classList.toggle("stopped", stopped);
  $("stopBtn").textContent = stopped ? "Resume JARVIS" : "Emergency stop";
  $("stopBtn").classList.toggle("danger", !stopped);
  $("stopNote").textContent = stopped
    ? "JARVIS is refusing every request. Nothing has been deleted."
    : "Refuses every request until switched back on. Nothing is deleted.";

  $("sMessages").textContent = d.today.messages;
  $("sLearned").textContent = d.today.learning_runs;
  $("sSpend").textContent = "₹" + d.today.spend_inr.toFixed(2);
  $("sSpendNote").textContent =
    d.today.spend_inr === 0 ? "free tier — see BUDGET.md" : "estimated";

  // Gauges read against what actually reaches the model, so "full" means
  // "as much as JARVIS can use", not an invented capacity.
  const pct = d.budget.percent_used;
  // "0%" for a real but tiny spend reads as "nothing spent", which is the
  // wrong direction to be wrong about money.
  const pctLabel = pct === 0 ? "0%" : pct < 1 ? "<1%" : pct.toFixed(0) + "%";
  gauge("gBudget", "gBudgetTxt", pct, 100, pctLabel, d.budget.status);
  gauge("gFacts", "gFactsTxt", d.memory.facts, d.memory.facts_limit, String(d.memory.facts));
  gauge("gRecall", "gRecallTxt", d.memory.turns, d.memory.recall_turns_limit, String(d.memory.turns));

  $("memNote").textContent =
    `${d.memory.forgotten} forgotten · ₹${d.budget.spend_inr.toFixed(2)} of ` +
    `₹${d.budget.ceiling_inr.toFixed(0)} this month`;

  sparkline(d.history || []);

  $("tModel").textContent = d.status.model;
  $("tLast").textContent = lastReplyMs === null ? "—" : (lastReplyMs / 1000).toFixed(1) + "s";
  $("tMem").textContent = `${d.memory.facts}f / ${d.memory.turns}t`;

  $("cfgModel").textContent = `${d.status.provider} · ${d.status.model}`;
  $("cfgRecall").textContent = d.status.recall_enabled
    ? `on · last ${d.memory.recall_turns_limit} turns` : "off";
  $("cfgFacts").textContent = d.status.facts_enabled ? "on" : "off";

  // facts
  const fl2 = $("factsList");
  fl2.innerHTML = "";
  if (!d.facts.length) {
    fl2.innerHTML = "<li class='empty'>Nothing yet. Tell JARVIS something worth keeping.</li>";
  } else {
    for (const fact of d.facts) {
      const li = document.createElement("li");
      li.innerHTML =
        `<span class="cat">${esc(fact.category)}</span>` +
        `<span class="txt">${esc(fact.content)}</span>`;
      const x = document.createElement("button");
      x.className = "x"; x.textContent = "×"; x.title = "Forget this";
      x.onclick = async () => {
        if (!confirm("Make JARVIS forget this? It can be restored from the console.")) return;
        await api(`/v1/memories/${fact.id}/forget`, { method: "POST" });
        refresh();
      };
      li.appendChild(x);
      fl2.appendChild(li);
    }
  }

  // activity
  const feed = $("feed");
  feed.innerHTML = "";
  if (!d.activity.length) {
    feed.innerHTML = "<li class='empty'>No activity recorded yet.</li>";
  } else {
    for (const a of d.activity) {
      const when = new Date(a.at);
      const li = document.createElement("li");
      const risk = a.category === "high_risk" ? "high" : a.category === "medium_risk" ? "med" : "";
      li.innerHTML =
        `<span class="t">${pad(when.getHours())}:${pad(when.getMinutes())}</span>` +
        `<span class="dot ${risk}"></span>` +
        `<span class="d">${esc(describe(a))}` +
        (a.outcome ? `<small>${esc(a.outcome)}</small>` : "") + `</span>`;
      feed.appendChild(li);
    }
  }
}

// The audit log stores machine names. Nobody should have to learn them to
// read their own activity feed.
// A 270-degree arc starting at the lower left, the way a dial reads.
// 151 is the arc's length in this 32-radius circle; the rest of the
// circumference is the gap at the bottom.
const ARC = 151;
function gauge(arcId, textId, value, max, label, status) {
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const el = $(arcId);
  el.style.strokeDashoffset = ARC * (1 - frac);
  el.classList.toggle("warn", status === "warn_50" || status === "warn_80" || (!status && frac >= 0.8));
  el.classList.toggle("bad", status === "exceeded" || (!status && frac >= 1));
  $(textId).textContent = label;
}

// Messages per day for the past week. Drawn from the series the server
// sends, which includes quiet days as zero -- a chart that skipped them
// would draw a flattering line instead of a true one.
function sparkline(history) {
  const svg = $("spark");
  if (!history.length) { svg.innerHTML = ""; return; }
  const vals = history.map((h) => h.messages);
  const peak = Math.max(1, ...vals);
  const W = 300, H = 46, step = W / Math.max(1, history.length - 1);
  const y = (v) => H - 4 - (v / peak) * (H - 10);
  const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`);

  svg.innerHTML =
    `<defs><linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
       <stop offset="0%" stop-color="#48c8ff" stop-opacity=".35"/>
       <stop offset="100%" stop-color="#48c8ff" stop-opacity="0"/>
     </linearGradient></defs>` +
    `<polygon points="0,${H} ${pts.join(" ")} ${W},${H}" fill="url(#sparkFill)"/>` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="url(#gSpark)" stroke-width="2"
       stroke-linejoin="round" stroke-linecap="round"/>` +
    vals.map((v, i) =>
      `<circle cx="${(i * step).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${i === vals.length - 1 ? 3 : 1.8}"
        fill="${i === vals.length - 1 ? "#bfefff" : "#48c8ff"}"/>`).join("");

  const first = new Date(history[0].day);
  $("sparkFrom").textContent = first.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  $("sparkPeak").textContent = `peak ${peak}`;
}

function describe(a) {
  const map = {
    llm_message_exchange: "Message answered",
    memory_learn: "Learned something new",
    memory_forget: "Forgot a memory",
    memory_restore: "Restored a memory",
    memory_delete: "Deleted a memory",
    memory_forget_all: "Forgot everything",
    memory_purge: "Erased forgotten memories",
    sign_in: "Signed in",
    sign_in_refused_not_owner: "Refused a sign-in",
  };
  if (map[a.action]) return map[a.action];
  if (a.action.startsWith("emergency_stop")) return "Emergency stop changed";
  return a.action.replace(/_/g, " ");
}

async function refresh() {
  try {
    const res = await api("/v1/dashboard");
    if (!res.ok) return false;
    renderDashboard(await res.json());
    return true;
  } catch (e) { return false; }
}

/* ------------------------------------------------------------ messaging */
const convo = $("convo");
function addMsg(who, text, tail, cls = "") {
  if (convo.querySelector(".empty")) convo.innerHTML = "";
  const div = document.createElement("div");
  div.className = `msg ${who} ${cls}`;
  div.innerHTML =
    `<div class="from">${who === "you" ? "You" : "J.A.R.V.I.S"}</div>` +
    `<div class="body">${esc(text)}</div>` +
    (tail ? `<div class="tail">${esc(tail)}</div>` : "");
  convo.appendChild(div);
  convo.scrollTop = convo.scrollHeight;
  return div;
}

async function send() {
  const input = $("msg");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMsg("you", text);
  setBusy(true);
  $("convoNote").textContent = "Thinking… the first message after a break can take a minute while the server wakes.";

  try {
    const res = await api("/v1/message", { method: "POST", body: JSON.stringify({ text }) });
    let data = {};
    try { data = await res.json(); } catch (e) {}

    if (res.ok) {
      lastReplyMs = data.total_ms;
      addMsg("jarvis", data.reply,
        `${data.provider} · ${data.recalled_turns} turns, ${data.recalled_facts} facts · ` +
        `${(data.total_ms / 1000).toFixed(1)}s`);
      speak(data.reply);
    } else {
      addMsg("jarvis", explain(res.status, data.detail || ""), null, "err");
    }
  } catch (e) {
    addMsg("jarvis", `Could not reach JARVIS: ${e.message}. It may still be waking up — wait a minute and try again.`, null, "err");
  } finally {
    setBusy(false);
    $("convoNote").textContent = "";
    refresh();
  }
}

// Plain English, and pointing at the real cause rather than the most
// likely-sounding one.
function explain(status, detail) {
  if (status === 401) return "You are not signed in. Reload the page and sign in again.";
  if (status === 403) return "Signed in, but not as the owner of this JARVIS.\n\n" + detail;
  if (status === 503 && /Emergency Stop/i.test(detail))
    return "JARVIS is stopped. Turn the emergency stop off in Settings to resume.";
  if (status === 502) {
    const retired = detail.match(/use\s+models\/([A-Za-z0-9._-]+)/);
    if (retired)
      return `Google has retired the model JARVIS uses and says to use ${retired[1]}.\n\n` +
             `Fix: Render → Environment → set GEMINI_MODEL to ${retired[1]} → Save.\n\n${detail}`;
    if (/RESOURCE_EXHAUSTED|quota|429/i.test(detail))
      return "The free daily quota for your Gemini key is used up. It resets every 24 hours.\n\n" + detail;
    if (/API_KEY_INVALID|PERMISSION_DENIED|UNAUTHENTICATED/i.test(detail))
      return "The API key is missing or wrong. Check GEMINI_API_KEY in Render.\n\n" + detail;
    return "JARVIS could not get an answer from the model.\n\n" + detail;
  }
  if (status >= 500)
    return "Something failed inside JARVIS.\n\n" + detail +
           "\n\nIf this mentions the database, the Supabase project may have paused — open its dashboard and restore it.";
  return `Unexpected response (HTTP ${status}).\n\n${detail}`;
}

/* ------------------------------------------------------------------ nav */
// In the landscape layout every panel is on screen at once, so "go to
// memory" had nothing to go to. Home and Settings remain; full screen is
// genuinely useful for leaving this up on a spare monitor.
const views = {
  home: () => { $("settingsView").classList.add("hidden"); document.querySelector(".grid").classList.remove("hidden"); },
  settings: () => { document.querySelector(".grid").classList.add("hidden"); $("settingsView").classList.remove("hidden"); },
};
$("nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  [...$("nav").querySelectorAll("button[data-view]")].forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  views[btn.dataset.view]();
});

$("fsBtn").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (e) { /* Safari on iOS refuses; nothing useful to say about it */ }
};
document.addEventListener("fullscreenchange", () =>
  $("fsBtn").classList.toggle("active", !!document.fullscreenElement));

/* ------------------------------------------------------------- controls */
$("sendBtn").onclick = send;
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

$("speakToggle").checked = speakOn;
$("speakToggle").onchange = (e) => {
  speakOn = e.target.checked;
  try { localStorage.setItem("jarvis.speak", speakOn ? "1" : "0"); } catch (err) {}
  // Speaking now doubles as priming: iOS only allows speech that follows
  // a real tap, and this tap is one.
  if (speakOn) speak("Voice enabled.");
};
$("voiceNote").textContent = canSpeak
  ? "Uses your device's built-in voice. No API, no cost."
  : "This browser has no speech engine, so JARVIS cannot speak here.";

$("stopBtn").onclick = async () => {
  const stopping = $("stopBtn").textContent.startsWith("Emergency");
  if (stopping && !confirm("Stop JARVIS? It will refuse every request until you turn this off.")) return;
  await api("/v1/admin/emergency-stop", { method: "POST", body: JSON.stringify({ stop: stopping }) });
  refresh();
};

$("signOutBtn").onclick = async () => {
  await api("/auth/logout", { method: "POST" });
  location.reload();
};

/* ------------------------------------------------------------- start-up */
async function start() {
  const ok = await refresh();
  if (ok) {
    gate.classList.add("hidden");
    app.classList.remove("hidden");
    setupMic();
    // Keep the numbers honest without hammering a sleepy free-tier server.
    setInterval(refresh, 60000);
    return;
  }

  // Not signed in: show Google's own in-page button. It neither pops up
  // nor navigates, which is the only thing that works reliably on iPad.
  let cfg = {};
  try { cfg = await (await fetch("/public/firebase-config")).json(); } catch (e) {}

  if (!cfg.googleClientId) {
    $("gateMsg").innerHTML =
      "<span class='err'>GOOGLE_CLIENT_ID is not set on this server, so sign-in cannot be shown.</span>";
    $("gateHint").textContent = "See docs/DEPLOYMENT.md step 4f.";
    return;
  }

  $("gateMsg").textContent = "Sign in to continue.";
  const render = () => {
    if (!(window.google && window.google.accounts)) return setTimeout(render, 200);
    window.google.accounts.id.initialize({
      client_id: cfg.googleClientId,
      callback: async (r) => {
        $("gateMsg").textContent = "Signing in…";
        const res = await api("/auth/session", {
          method: "POST", body: JSON.stringify({ credential: r.credential }),
        });
        if (!res.ok) {
          const b = await res.json().catch(() => ({}));
          $("gateMsg").innerHTML = `<span class='err'>${esc(b.detail || "Sign-in refused (HTTP " + res.status + ").")}</span>`;
          return;
        }
        if (!(await refresh())) {
          $("gateMsg").innerHTML =
            "<span class='err'>Signed in, but your browser did not keep the login cookie.</span>";
          $("gateHint").textContent =
            "This is usually Private Browsing, or cookies blocked for this site. Try a normal tab.";
          return;
        }
        gate.classList.add("hidden");
        app.classList.remove("hidden");
        setupMic();
        setInterval(refresh, 60000);
      },
    });
    window.google.accounts.id.renderButton($("gsi"), {
      theme: "filled_black", size: "large", shape: "pill", text: "signin_with",
    });
  };
  render();
}
start();
