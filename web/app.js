/* Show Me phone page. Plain JS, no framework.
   Flow: Start -> camera -> hold mic -> transcribe || frame upload -> POST /runs -> speak step 1 -> /outline draws the part
         "Done, check it" -> fresh frame -> POST /runs/{id}/advance -> next step / feedback / needs_input
         needs_input -> "Check again" or "I did it, continue" (POST /runs/{id}/confirm, logged as human-confirmed)
   The access key comes once from the address (#k=...) and lives in localStorage. Never in this file. */

const BACKEND = "https://nirup2721--show-me-web.us-east.modal.run";
const FRAME_LONG_SIDE = 768;
const ACCENT = "#4FE3FF";

// ---------- thinking state: sweep, reticle, readout that fills from live timings ----------
const secs = (v) => { const m = /^([\d.]+)s/.exec(v || ""); return m ? `${(+m[1]).toFixed(1)}s` : "wait"; };
function updateReadout() {
  $("readout").textContent = `IDENTIFY ·· ${secs(timing.identify)}\nOUTLINE  ·· ${secs(timing.outline)}\nPLAN     ·· ${secs(timing["first words"])}`;
}
function thinking(on) {
  const motion = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  show("sweep", on && motion); show("reticle", on && motion); show("readout", on);
  if (on) updateReadout();
}

const $ = (id) => document.getElementById(id);
const cam = $("cam"), still = $("still"), overlay = $("overlay");
const mic = $("mic"), micLabel = $("micLabel");

let run = null, prevStep = -1, busy = false, recorder = null, chunks = [];
let frameSource = "camera";
let KEY = "";
let runStartedAt = 0;

// ---------- screens ----------
function screen(name) { document.body.dataset.screen = name; }
function show(id, on) { $(id).hidden = !on; }
function setTop(mode) {              // "idle" | "listen" | "think" | "step" | "need" | null
  show("top", !!mode);
  for (const m of ["t-idle", "t-listen", "t-think", "t-step", "t-need"]) show(m, false);
  if (mode) show("t-" + mode, true);
  show("bar", mode === "think");
  thinking(mode === "think");
}
function setBottom(mode) {           // "listen" | "step" | "need" | null
  show("bottom", !!mode);
  show("c-listen", mode === "listen");
  show("c-step", mode === "step" || mode === "need");
  show("next", mode === "step" || mode === "need");
  $("nextLabel").textContent = mode === "need" ? "Check again" : "Done, check it";
  show("confirm", mode === "need");
  show("confirmNote", mode === "need");
  if (mode !== "listen") { show("heard", false); }
}

// ---------- access key ----------
function getKey() {
  const m = (location.hash + location.search).match(/[#&?]k=([^&]+)/);
  if (m) { localStorage.setItem("showme_key", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname + location.hash.replace(/[#&]k=[^&]+/, "").replace(/^&/, "#")); }
  let k = localStorage.getItem("showme_key");
  if (!k) {
    try { k = prompt("Enter the Show Me access key"); } catch (e) { k = ""; }
    if (k) localStorage.setItem("showme_key", k.trim());
  }
  return k || "";
}
async function loadDevKey() {
  if (!/devkey=1/.test(location.hash) || !/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) return;
  try { const t = (await (await fetch("/dev_key.txt")).text()).trim(); if (t) localStorage.setItem("showme_key", t); } catch (e) {}
}

// ---------- speech ----------
function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "en-US"; u.rate = 1.0;
  speechSynthesis.speak(u);
}

// ---------- camera ----------
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: true });
    stream.getAudioTracks().forEach((t) => t.stop());   // permission asked now; the mic is reopened per hold
    cam.srcObject = new MediaStream(stream.getVideoTracks());
    frameSource = "camera";
    return true;
  } catch (e) {
    frameSource = "still";
    return false;
  }
}
function showStill(src) { still.src = src; still.hidden = false; cam.hidden = true; frameSource = "still"; }
function loadDevImage() {
  const m = location.hash.match(/img=([^&]+)/);
  if (!m) return false;
  showStill(decodeURIComponent(m[1]));
  return true;
}
$("photo").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  showStill(URL.createObjectURL(f));
  setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Photo loaded. Hold the mic or type your question.";
});

function captureFrame() {
  return new Promise((resolve, reject) => {
    const src = frameSource === "camera" ? cam : still;
    const sw = frameSource === "camera" ? cam.videoWidth : still.naturalWidth;
    const sh = frameSource === "camera" ? cam.videoHeight : still.naturalHeight;
    if (!sw || !sh) return reject(new Error("No picture yet. Point the camera at the machine."));
    const scale = Math.min(1, FRAME_LONG_SIDE / Math.max(sw, sh));
    const c = document.createElement("canvas");
    c.width = Math.round(sw * scale); c.height = Math.round(sh * scale);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    c.toBlob((b) => b ? resolve({ blob: b, w: c.width, h: c.height }) : reject(new Error("capture failed")), "image/jpeg", 0.72);
  });
}

// ---------- overlay ----------
function displayedRect() {
  const sw = frameSource === "camera" ? cam.videoWidth : still.naturalWidth;
  const sh = frameSource === "camera" ? cam.videoHeight : still.naturalHeight;
  const W = overlay.clientWidth, H = overlay.clientHeight;
  const s = Math.min(W / sw, H / sh);
  return { x: (W - sw * s) / 2, y: (H - sh * s) / 2, w: sw * s, h: sh * s };
}
function drawOutline(opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  overlay.width = overlay.clientWidth * dpr; overlay.height = overlay.clientHeight * dpr;
  const ctx = overlay.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, overlay.clientWidth, overlay.clientHeight);
  const o = run && run.outline; if (!o || !run.frame_size || !(o.box || o.bounds)) return;
  const need = run.status === "needs_input";
  const [fw] = run.frame_size;
  const r = displayedRect();
  const k = r.w / fw;
  const stroke = opts.flash ? "#FFFFFF" : need ? "#8A9099" : ACCENT;
  const fill = need ? "rgba(138,144,153,0.10)" : "rgba(79,227,255,0.14)";
  ctx.save();
  ctx.translate(r.x, r.y); ctx.scale(k, k);
  ctx.lineWidth = 3 / k; ctx.strokeStyle = stroke; ctx.fillStyle = fill;
  if (need) ctx.setLineDash([8 / k, 6 / k]);
  if (o.kind === "mask" && o.svg_path) {
    const [bl, bt, br, bb] = o.bounds;
    ctx.save();
    ctx.translate(bl, bt); ctx.scale((br - bl) / o.mask_w, (bb - bt) / o.mask_h);
    const p = new Path2D(o.svg_path);
    ctx.fill(p);
    ctx.lineWidth = 3 / k / ((br - bl) / o.mask_w);
    ctx.stroke(p);
    ctx.restore();
  } else if (o.box) {
    const [x1, y1, x2, y2] = o.box;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  }
  ctx.restore();
  const [bx, by] = o.box || o.bounds;
  const label = (run.part_name || "") + (need ? " · too far to read" : "");
  if (label.trim()) {
    const sx = r.x + bx * k, sy = r.y + by * k;
    ctx.font = "600 14px 'Instrument Sans', system-ui, sans-serif";
    const tw = ctx.measureText(label).width, ph = 28, pw = tw + 22;
    const px = Math.min(Math.max(6, sx), overlay.clientWidth - pw - 6);
    const py = Math.max(6, sy - ph - 8);
    ctx.fillStyle = "rgba(6,9,13,0.88)";
    ctx.beginPath(); ctx.roundRect(px, py, pw, ph, 14); ctx.fill();
    ctx.strokeStyle = need ? "#8A9099" : ACCENT; ctx.lineWidth = 1.5; ctx.setLineDash([]); ctx.stroke();
    ctx.fillStyle = need ? "#A8ADB5" : "#EAF7FA"; ctx.textBaseline = "middle";
    ctx.fillText(label, px + 11, py + ph / 2);
  }
}
window.addEventListener("resize", () => drawOutline());
cam.addEventListener("loadedmetadata", () => drawOutline());

// ---------- server ----------
const timing = {};
function footer() {
  const parts = Object.entries(timing).map(([k, v]) => `${k} ${v}`);
  $("cost").textContent = (parts.length && $("run").textContent ? " · " : "") + parts.join(" · ");
  if (!$("readout").hidden) updateReadout();
}
function mark(name, t0, r) {
  const ms = Math.round(performance.now() - t0);
  const st = r && r.headers && r.headers.get("Server-Timing");
  const m = st && /dur=(\d+)/.exec(st);
  timing[name] = m ? `${(ms / 1000).toFixed(1)}s (server ${(+m[1] / 1000).toFixed(1)}s)` : `${(ms / 1000).toFixed(1)}s`;
  footer();
}
async function api(path, form, name) {
  const t0 = performance.now();
  const r = await fetch(BACKEND + path, { method: form ? "POST" : "GET", body: form, headers: { "X-Show-Me-Key": KEY } });
  if (name) mark(name, t0, r);
  if (r.status === 401) throw new Error("Access key wrong. Open the link you were given.");
  if (r.status === 503) throw new Error("Spending limit reached. Paid calls are off.");
  if (!r.ok) throw new Error(`Server error ${r.status}`);
  return r.json();
}
let pendingFrame = null;
function uploadFrameEarly() {
  pendingFrame = (async () => {
    const f = await captureFrame();
    timing.frame = `${f.w}x${f.h} ${Math.round(f.blob.size / 1024)} KB`; footer();
    const form = new FormData(); form.append("frame", f.blob, "frame.jpg");
    return api("/frames", form, "upload");
  })().catch((e) => { pendingFrame = null; throw e; });
  return pendingFrame;
}

// ---------- rendering a run ----------
function firstSentence(s) { const m = /^(.+?[.!?])(\s|$)/.exec(s || ""); return m ? [m[1], (s || "").slice(m[1].length).trim()] : [s || "", ""]; }

function renderStep(r, animate) {
  const d = r.display || {};
  const n = r.current_step, total = r.steps.length, step = r.steps[n];
  $("warnText").textContent = d.warning || "";
  $("warn").classList.toggle("empty", !d.warning);
  $("stepLine").textContent = step ? `Step ${n + 1}. ${step.instruction}` : d.step || "";
  // "Not yet. <reason> <instruction>" from the verifier -> feedback line under the step
  let fb = "";
  if (/^Not yet\./.test(d.step || "")) fb = (d.step || "").replace(step ? step.instruction : "", "").trim();
  $("feedback").textContent = fb;
  const seg = $("segments"); seg.innerHTML = "";
  for (let i = 0; i < total; i++) { const s = document.createElement("i"); if (i < n) s.className = "on"; else if (i === n) s.className = "cur"; seg.appendChild(s); }
  setTop("step"); setBottom("step");
  const el = $("t-step"); el.classList.remove("in"); if (animate) { void el.offsetWidth; el.classList.add("in"); }
}
// One sentence for the strip, built from the run; the server's longer message is what gets spoken.
function needSentence(r) {
  const part = r.part_name || "the part";
  const label = r.matched_label || part;
  const d = r.display || {};
  const el = $("needHead"); el.textContent = "";
  const add = (t, cls) => { const s = document.createElement("span"); if (cls) s.className = cls; s.textContent = t; el.appendChild(s); };
  if (/still can't confirm/i.test(d.step || "")) { add(`Still can't confirm the ${part}. Hold the camera on it and check again.`); return; }
  add(`Can't see the ${part}. Move closer so `); add(label, "accent"); add(" fills the screen.");
}
function renderNeed(r) {
  const n = r.current_step, total = r.steps.length;
  $("needCount").textContent = `Step ${Math.min(n + 1, total)} of ${total} · waiting on you`;
  needSentence(r);
  setTop("need"); setBottom("need");
}
function renderDone(r) {
  $("doneObj").textContent = r.object_name || "";
  const byCam = r.steps.filter((s) => s.confirmed_by === "camera").length;
  $("doneLine").textContent = byCam === r.steps.length ? "Every step was confirmed through the camera." : `${byCam} of ${r.steps.length} steps confirmed through the camera.`;
  const ol = $("doneSteps"); ol.innerHTML = "";
  r.steps.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="ck"><svg><use href="#i-check"/></svg></span><span>${s.instruction}</span><span class="by">${s.confirmed_by === "camera" ? "camera" : "you"}</span>`;
    ol.appendChild(li);
  });
  const secs = runStartedAt ? Math.round((performance.now() - runStartedAt) / 1000) : null;
  $("doneFoot").textContent = [secs != null ? `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}` : null, `${byCam} of ${r.steps.length} by camera`, r.cost_usd ? `$${r.cost_usd.toFixed(3)}` : null].filter(Boolean).join(" · ");
  setTop(null); setBottom(null); show("s-done", true); screen("done");
}
function flashPass() {
  drawOutline({ flash: true });
  show("flash", true);
  setTimeout(() => { show("flash", false); drawOutline(); }, 1000);
}

function render(r, opts = {}) {
  const passed = run && r.current_step > prevStep && prevStep >= 0 && r.status !== "failed";
  const failed = run && r.status === "guiding" && r.current_step === prevStep && /^Not yet\./.test((r.display || {}).step || "");
  run = r; prevStep = r.current_step;
  $("run").textContent = r.id ? `run ${r.id}${r.cost_usd ? ` · $${r.cost_usd.toFixed(3)}` : ""}` : "";
  footer();
  if (opts.quiet) { drawOutline(); return; }       // outline arrived after speech began
  speak(r.message);
  if (r.status === "done") { renderDone(r); drawOutline(); return; }
  screen("live"); show("s-done", false);
  if (r.status === "needs_input") renderNeed(r);
  else if (r.status === "failed") { $("stepLine").textContent = r.message || "Something went wrong."; $("feedback").textContent = ""; setTop("step"); setBottom("listen"); }
  else renderStep(r, passed);
  if (failed) { const p = $("top"); p.classList.remove("shake"); void p.offsetWidth; p.classList.add("shake"); }
  if (passed) { setTimeout(flashPass, 50); } else drawOutline();
}

// ---------- actions ----------
async function startRun(question) {
  if (busy || !question) return;
  busy = true; runStartedAt = performance.now(); prevStep = -1;
  setTop("think"); setBottom("listen"); $("heard").textContent = `“${question}”`; show("heard", true);
  const t0 = performance.now();
  try {
    const form = new FormData(); form.append("question", question);
    let up = null; try { up = pendingFrame ? await pendingFrame : null; } catch (e) { up = null; }
    pendingFrame = null;
    if (up && up.frame_id) form.append("frame_id", up.frame_id);
    else { const f = await captureFrame(); form.append("frame", f.blob, "frame.jpg"); }
    const r = await api("/runs", form, "identify");
    timing["first words"] = `${((performance.now() - t0) / 1000).toFixed(1)}s`; footer();
    render(r);
    if (r.status === "guiding" && r.id) {
      try { render(await api(`/runs/${r.id}/outline`, new FormData(), "outline"), { quiet: true }); } catch (e) {}
    }
  } catch (e) { fail(e.message); }
  busy = false;
}
async function advance() {
  if (busy || !run) return;
  busy = true; $("next").disabled = true; setTop("think");
  try {
    const f = await captureFrame();
    const form = new FormData(); form.append("frame", f.blob, "frame.jpg");
    render(await api(`/runs/${run.id}/advance`, form, "check"));
  } catch (e) { fail(e.message); }
  $("next").disabled = false; busy = false;
}
async function confirmHuman() {
  if (busy || !run) return;
  busy = true;
  try { render(await api(`/runs/${run.id}/confirm`, new FormData())); } catch (e) { fail(e.message); }
  busy = false;
}
function fail(msg) { $("warnText").textContent = ""; $("warn").classList.add("empty"); $("stepLine").textContent = msg; $("feedback").textContent = ""; $("segments").innerHTML = ""; setTop("step"); setBottom("listen"); }
function askAgain() {
  run = null; prevStep = -1; speechSynthesis.cancel();
  for (const k in timing) delete timing[k]; footer(); $("run").textContent = "";
  show("s-done", false); screen("live"); drawOutline();
  setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Point at the machine, then hold the mic and ask.";
  setBottom("listen");
}
$("next").addEventListener("click", advance);
$("confirm").addEventListener("click", confirmHuman);
$("askagain").addEventListener("click", askAgain);
$("again").addEventListener("click", askAgain);
$("repeat").addEventListener("click", () => run && speak(run.message));

// ---------- hold to talk ----------
async function startRecording() {
  if (busy || recorder) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = recorder.mimeType || "audio/webm";
      recorder = null; mic.classList.remove("rec"); micLabel.textContent = "Hold to talk";
      const blob = new Blob(chunks, { type });
      if (blob.size < 2000) { setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Too short. Hold the button while you speak."; return; }
      busy = true; setTop("think");
      try {
        const form = new FormData();
        form.append("audio", blob, "q." + (type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm"));
        timing.audio = `${Math.round(blob.size / 1024)} KB`; footer();
        const t = await api("/transcribe", form, "transcribe");
        busy = false;
        if (!t.text) { setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "I didn't catch that. Try again."; return; }
        await startRun(t.text);
      } catch (e) { busy = false; fail(e.message); }
    };
    recorder.start();
    mic.classList.add("rec"); micLabel.textContent = "Listening";
    setTop("listen"); show("heard", false);
    speechSynthesis.cancel();
    for (const k in timing) delete timing[k]; footer();
    uploadFrameEarly().catch(() => {});
  } catch (e) {
    setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Microphone not available. Type your question instead.";
    show("typed", true);
  }
}
function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }
mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startRecording(); });
for (const ev of ["pointerup", "pointercancel", "pointerleave"]) mic.addEventListener(ev, stopRecording);
mic.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- typed question ----------
function toggleTyped() { const t = $("typed"); t.hidden = !t.hidden; if (!t.hidden) $("question").focus(); }
$("type").addEventListener("click", toggleTyped);
$("type2").addEventListener("click", toggleTyped);
$("ask").addEventListener("click", () => { const q = $("question").value.trim(); if (q) { for (const k in timing) delete timing[k]; uploadFrameEarly().catch(() => {}); show("typed", false); startRun(q); } });
$("question").addEventListener("keydown", (e) => { if (e.key === "Enter") $("ask").click(); });

// ---------- start ----------
$("start").addEventListener("click", async () => {
  screen("live");
  const ok = loadDevImage() || await startCamera();
  setTop("idle"); setBottom("listen");
  if (!ok) $("t-idle").querySelector(".t-sub").textContent = "No camera here. Use the photo button, or type a question.";
});

// ---------- demo states (screenshots and the video; #demo=listening|step|needs_input|done) ----------
const DEMO = {
  object_name: "Breaker panel", part_name: "Register breaker", frame_size: [576, 768],
  display: { warning: "Keep hands dry. Touch only the breaker handle.", object: "", step: "Step 1. Push the Register breaker handle down to OFF." },
  steps: [{ instruction: "Push the Register breaker handle down to OFF.", confirmed_by: "camera" }, { instruction: "Push the same handle firmly back to ON.", confirmed_by: "camera" },
          { instruction: "Check that the register screen is lit.", confirmed_by: "camera" }, { instruction: "Confirm the register responds.", confirmed_by: "you" }],
  outline: { kind: "box", box: [255, 300, 340, 350], confidence: 0.94 }, cost_usd: 0.031, id: "demo",
};
function demo(state) {
  screen("live"); show("s-done", false);
  if (!loadDevImage()) { startCamera(); }
  timing.frame = "576x768 59 KB"; timing.transcribe = "2.1s"; timing.identify = "2.9s"; timing["first words"] = "5.0s"; timing.outline = "1.7s"; footer();
  if (state === "listening") { setTop("listen"); setBottom("listen"); mic.classList.add("rec"); micLabel.textContent = "Listening"; $("heard").textContent = "“the register has no pow”"; show("heard", true); }
  if (state === "thinking") { delete timing.outline; delete timing["first words"]; setTop("think"); setBottom("listen"); $("heard").textContent = "“the register has no power. What do I do?”"; show("heard", true); footer(); }
  if (state === "step") { run = { ...DEMO, status: "guiding", current_step: 0 }; prevStep = 0; renderStep(run, false); drawOutline(); $("run").textContent = "run demo · $0.031"; }
  if (state === "needs_input") { run = { ...DEMO, status: "needs_input", current_step: 1, part_name: "switch", matched_label: "Register", display: { ...DEMO.display, step: "I can't see it clearly. Hold the camera on the switch and tap Done again, or tap 'I did it' to continue." } }; prevStep = 1; renderNeed(run); drawOutline(); }
  if (state === "done") { run = { ...DEMO, status: "done", current_step: 4 }; runStartedAt = performance.now() - 74000; renderDone(run); }
}

// ---------- boot ----------
(async () => {
  await loadDevKey();
  KEY = getKey();
  const m = location.hash.match(/demo=([a-z_]+)/);
  if (m) demo(m[1]); else screen("start");
})();
