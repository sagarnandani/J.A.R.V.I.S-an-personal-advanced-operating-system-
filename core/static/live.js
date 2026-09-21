/* Live voice: talking to JARVIS, rather than typing at it.
 *
 * The browser's speech engine reads text aloud. This is a different
 * thing: Gemini generates the speech itself, which is why it handles
 * Kannada, Hindi, Marathi and English -- including mixed inside one
 * sentence, which dictation-then-text never manages.
 *
 * Two audio paths, and they use different sample rates, which is not a
 * detail: the wrong rate produces silence or chipmunks, never an error.
 *
 *   microphone -> 16kHz mono PCM16 -> base64 -> server -> Gemini
 *   Gemini -> server -> base64 -> 24kHz PCM16 -> speakers
 *
 * Playback is scheduled rather than fired: each chunk is booked to start
 * exactly where the previous one ends. Playing them as they arrive leaves
 * audible gaps, because network jitter is larger than the chunks are.
 */
const LIVE = {
  ws: null,
  ctx: null,
  micStream: null,
  node: null,
  playAt: 0,
  sources: [],
  active: false,
  inRate: 16000,
  outRate: 24000,
};

function liveState(state, detail) {
  document.dispatchEvent(
    new CustomEvent("jarvis:live", { detail: { state, detail } })
  );
}

// Float samples from the microphone, resampled to what Gemini expects and
// converted to signed 16-bit. Straight-line resampling is enough here:
// speech at 16kHz has nothing near the Nyquist limit for the browser's
// usual 44.1/48kHz capture rate.
function toPcm16(float32, fromRate, toRate) {
  const ratio = fromRate / toRate;
  const outLength = Math.floor(float32.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const s = Math.max(-1, Math.min(1, float32[Math.floor(i * ratio)]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function b64(bytes) {
  let s = "";
  const chunk = 0x8000; // one call per 32k samples; the whole buffer overflows the arg list
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

function unb64(str) {
  const bin = atob(str);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function play(bytes) {
  if (!LIVE.ctx) return;
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
  const buf = LIVE.ctx.createBuffer(1, pcm.length, LIVE.outRate);
  const channel = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;

  const src = LIVE.ctx.createBufferSource();
  src.buffer = buf;
  src.connect(LIVE.ctx.destination);

  // Book this chunk where the last one ends. A small cushion absorbs
  // jitter without being audible as delay.
  const now = LIVE.ctx.currentTime;
  if (LIVE.playAt < now) LIVE.playAt = now + 0.06;
  src.start(LIVE.playAt);
  LIVE.playAt += buf.duration;

  LIVE.sources.push(src);
  src.onended = () => {
    LIVE.sources = LIVE.sources.filter((s) => s !== src);
    if (!LIVE.sources.length) liveState("listening");
  };
  liveState("speaking");
}

// Interruption: when you start talking over JARVIS, the model stops
// generating and everything already queued must be dropped. Letting it
// finish means it talks over your interruption, which is worse than not
// being interruptible at all.
function stopPlayback() {
  for (const s of LIVE.sources) { try { s.stop(); } catch (e) {} }
  LIVE.sources = [];
  LIVE.playAt = 0;
}

async function startLive() {
  if (LIVE.active) return;
  liveState("connecting");

  try {
    LIVE.micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    liveState("error", "Microphone permission was refused, so JARVIS cannot hear you.");
    return;
  }

  // One AudioContext for both directions. Created inside the tap that
  // started this, because iOS will not let audio begin otherwise.
  LIVE.ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (LIVE.ctx.state === "suspended") await LIVE.ctx.resume();

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  LIVE.ws = new WebSocket(`${proto}//${location.host}/v1/live`);

  LIVE.ws.onopen = () => { /* the server speaks first, with 'ready' */ };

  LIVE.ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "ready":
        LIVE.inRate = msg.input_rate || 16000;
        LIVE.outRate = msg.output_rate || 24000;
        LIVE.active = true;
        beginCapture();
        liveState("listening", `${msg.voice} · ${msg.model}`);
        break;
      case "resumed":
        // The server picked a dropped conversation back up. The
        // microphone never stopped, so nothing restarts here.
        liveState("listening", `${msg.voice} · ${msg.model}`);
        break;
      case "reconnecting":
        liveState("reconnecting", msg.attempt);
        break;
      case "audio":
        play(unb64(msg.data));
        break;
      case "you":
        liveState("heard", msg.text);
        break;
      case "jarvis":
        liveState("said", msg.text);
        break;
      case "interrupted":
        stopPlayback();
        liveState("listening");
        break;
      case "turn_complete":
        liveState("turn");
        break;
      case "offer":
      case "offer_running":
      case "offer_done":
      case "offer_failed":
      case "offer_closed":
      // "open" was added to the server and to app.js and NOT to this
      // list, so every spoken "open YouTube" was dropped right here,
      // silently, by a switch with no default. The server had already
      // told the model it had opened something, so JARVIS said so out
      // loud while nothing happened -- which is the exact shape of lie
      // the whole claims guard exists to prevent, arriving through a
      // case label.
      case "open":
        // JARVIS asked out loud and the owner answers out loud; these
        // frames only keep the screen honest about what is happening.
        // Rendering is app.js's job, so they are dispatched, not drawn.
        document.dispatchEvent(new CustomEvent("jarvis:offer", { detail: msg }));
        break;
      case "error":
        liveState("error", msg.message);
        stopLive();
        break;
      default:
        // Never silent again. A frame this file does not know about is
        // a frame somebody added at the other end and forgot to relay,
        // and the cost of finding that out was three rounds of "it says
        // it did it and nothing happened".
        console.warn(
          `live: unrelayed message type "${msg.type}" — if the page is ` +
          `meant to act on this, add it to the switch in live.js`);
        document.dispatchEvent(new CustomEvent("jarvis:offer", { detail: msg }));
        break;
    }
  };

  LIVE.ws.onerror = () => liveState("error", "The live connection failed.");
  LIVE.ws.onclose = () => {
    // The tab being suspended closes the socket. That is not an error
    // and must not look like one -- it is simply where the session
    // ended, and visibilitychange above brings it back.
    if (LIVE.active) stopLive();
  };
}

function beginCapture() {
  const source = LIVE.ctx.createMediaStreamSource(LIVE.micStream);
  // ScriptProcessor is deprecated in favour of AudioWorklet, and is used
  // anyway: it needs no separate module file, works in every browser that
  // matters here including Safari, and the deprecation has no removal
  // date. An AudioWorklet would be tidier and is a change for later.
  const node = LIVE.ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = (e) => {
    if (!LIVE.ws || LIVE.ws.readyState !== WebSocket.OPEN) return;
    const pcm = toPcm16(e.inputBuffer.getChannelData(0), LIVE.ctx.sampleRate, LIVE.inRate);
    LIVE.ws.send(JSON.stringify({
      type: "audio",
      data: b64(new Uint8Array(pcm.buffer)),
    }));
  };
  source.connect(node);
  // ScriptProcessor only runs while connected to a destination. Routed
  // through a silent gain node so the microphone is not echoed to the
  // speakers, which would be a feedback loop.
  const mute = LIVE.ctx.createGain();
  mute.gain.value = 0;
  node.connect(mute);
  mute.connect(LIVE.ctx.destination);
  LIVE.node = node;
}

// --- leaving JARVIS and coming back ---------------------------------------
//
// Opening Spotify sends JARVIS to the background, and a backgrounded tab
// on iOS is suspended: no microphone, no socket, no JavaScript at all.
// The connection closes, stopLive() runs, and nothing ever started it
// again -- so coming back to the tab found a dead microphone and a live
// button saying otherwise. "I go back to JARVIS and it cannot do it."
//
// What CANNOT be fixed here, and is not a coding gap: listening while in
// the background. Apple suspends background web pages, and no web app on
// iOS can hold a microphone through it. Only a native app can, and this
// is a web page on purpose.
//
// What can be fixed is the return. If voice was on when the tab went
// away, it comes back on when the tab does.
let wasListening = false;

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // Remember BEFORE the socket dies; onclose calls stopLive, which
    // clears the flag this reads.
    wasListening = LIVE.active;
    return;
  }
  if (!wasListening || LIVE.active) return;

  // iOS requires a gesture to open a microphone, and returning to a tab
  // is not one. So it is attempted -- which works where the permission
  // is already granted for the session -- and if it is refused the mic
  // button is simply off, honestly, rather than looking on.
  startLive().catch(() => { wasListening = false; });
});

function stopLive() {
  LIVE.active = false;
  stopPlayback();
  try { LIVE.ws && LIVE.ws.readyState === WebSocket.OPEN && LIVE.ws.send(JSON.stringify({ type: "end" })); } catch (e) {}
  try { LIVE.ws && LIVE.ws.close(); } catch (e) {}
  try { LIVE.node && LIVE.node.disconnect(); } catch (e) {}
  try { LIVE.micStream && LIVE.micStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { LIVE.ctx && LIVE.ctx.close(); } catch (e) {}
  LIVE.ws = LIVE.node = LIVE.micStream = LIVE.ctx = null;
  liveState("off");
}

function liveActive() { return LIVE.active; }

export { startLive, stopLive, liveActive };


/* A check that says where voice stops working, instead of leaving you to
 * guess between "my speakers", "my browser", "the server" and "Google".
 *
 * It walks the same path a real conversation takes, in order, and reports
 * each step in words. The tone at the end is the important one: if you
 * hear it, every part of the audio pipeline on your side is fine and any
 * remaining fault is upstream.
 */
async function voiceCheck(log) {
  const say = (line) => log(line);

  // 1. The browser's own speech engine (used for typed replies).
  if (!("speechSynthesis" in window)) {
    say("Browser voice: NOT AVAILABLE in this browser.");
  } else {
    const v = speechSynthesis.getVoices() || [];
    say(`Browser voice: available, ${v.length} voice(s) installed.`);
    if (!v.length) say("   -> none installed yet; iPad: Settings > Accessibility > Spoken Content > Voices.");
  }

  // 2. Can we make sound at all? Proves speakers, volume and the audio
  //    path, which is otherwise indistinguishable from a server fault.
  let ctx;
  try {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") await ctx.resume();
    say(`Audio output: ready (${ctx.state}, ${ctx.sampleRate}Hz).`);
  } catch (e) {
    say(`Audio output: FAILED - ${e.message}`);
    return;
  }

  // 3. The live relay. Report the server's own first word verbatim.
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  say(`Live voice: connecting to ${proto}//${location.host}/v1/live ...`);
  const result = await new Promise((resolve) => {
    let ws;
    const done = (r) => { try { ws && ws.close(); } catch (e) {} resolve(r); };
    const timer = setTimeout(() => done({ ok: false, why: "no answer within 20 seconds" }), 20000);
    try {
      ws = new WebSocket(`${proto}//${location.host}/v1/live`);
    } catch (e) {
      clearTimeout(timer);
      return resolve({ ok: false, why: e.message });
    }
    ws.onmessage = (ev) => {
      clearTimeout(timer);
      const m = JSON.parse(ev.data);
      if (m.type === "ready") done({ ok: true, model: m.model, voice: m.voice });
      else done({ ok: false, why: m.message || m.type });
    };
    ws.onerror = () => { clearTimeout(timer); done({ ok: false, why: "the connection was refused" }); };
    ws.onclose = (e) => { clearTimeout(timer); done({ ok: false, why: `closed (code ${e.code})` }); };
  });

  if (result.ok) say(`Live voice: WORKING - ${result.model}, voice ${result.voice}.`);
  else say(`Live voice: NOT WORKING - ${result.why}`);

  // 4. A tone, through exactly the path Gemini's audio uses.
  say("Playing a test tone now. If you hear a beep, your audio is fine.");
  try {
    const rate = 24000, seconds = 0.6;
    const buf = ctx.createBuffer(1, rate * seconds, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < ch.length; i++) ch[i] = 0.25 * Math.sin((2 * Math.PI * 440 * i) / rate);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start();
    await new Promise((r) => setTimeout(r, seconds * 1000 + 200));
    say("Tone finished. Heard nothing? Check the volume and the silent switch.");
  } catch (e) {
    say(`Tone: FAILED - ${e.message}`);
  }
  try { await ctx.close(); } catch (e) {}
}

export { voiceCheck };
