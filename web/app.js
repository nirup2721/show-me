/* Show Me phone page. Plain JS, no framework.
   Flow: Start -> camera -> hold mic -> transcribe || frame upload -> POST /runs -> speak step 1 -> /outline draws the part
         "Done, check it" -> fresh frame -> POST /runs/{id}/advance -> next step / feedback / needs_input
         needs_input -> "Check again" or "I did it, continue" (POST /runs/{id}/confirm, logged as human-confirmed)
   The access key comes once from the address (#k=...) and lives in localStorage. Never in this file. */

const BACKEND = "https://nirup2721--show-me-web.us-east.modal.run";
const FRAME_LONG_SIDE = 768;
const CHECK_LONG_SIDE = 512;      // auto-check frames are smaller: the verifier judges one named thing, not the scene
const CHECK_EVERY_MS = 1000, CHECK_MAX_INFLIGHT = 3, CHECK_GIVE_UP_MS = 60000;
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
function setTop(mode) {              // "idle" | "listen" | "think" | "step" | "need" | "survey" | null
  show("top", !!mode);
  for (const m of ["t-idle", "t-listen", "t-think", "t-step", "t-need", "t-survey"]) show(m, false);
  if (mode) show("t-" + mode, true);
  show("bar", mode === "think");
  thinking(mode === "think");
}
function setBottom(mode) {           // "listen" | "step" | "need" | null
  show("bottom", !!mode);
  show("c-listen", mode === "listen");
  show("c-step", mode === "step" || mode === "need");
  show("next", mode === "need");                       // manual check only as the fallback behind Skip
  show("watchRow", mode === "step");                    // guiding: the camera watches on its own
  $("nextLabel").textContent = mode === "need" ? "Check again" : "Done, check it";
  show("confirm", mode === "need");
  show("confirmNote", mode === "need");
  show("justUse", false);
  if (mode !== "listen") { show("heard", false); }
}
const GENERAL = /^\s*(what('s| is| do you see| can you see| are these| is here| is this| is that| do we have)|which one|look around|what('s| is) (here|around|in front)|show me (what|everything)|what do you see)\b/i;

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
  logEvent("speak", (text || "").slice(0, 60));
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.lang = "en-US"; u.rate = 1.0;
  // iOS can blank the camera when the audio session switches for speech: nudge the video at start and end.
  u.onstart = () => { logEvent("speak", "started"); cameraAlive(false); setTimeout(() => cameraAlive(false), 700); };
  u.onend = () => { logEvent("speak", "ended"); cameraAlive(false); setTimeout(() => cameraAlive(false), 700); };
  u.onerror = (e) => { if (e.error !== "canceled" && e.error !== "interrupted") logError("speech", e.error || "error"); cameraAlive(false); };   // our own cancel is not an error
  speechSynthesis.speak(u);
  cameraAlive(false);
}

// ---------- camera ----------
// One media stream for the whole session. iOS Safari stops the first video track when a second getUserMedia call is
// made (for example to open the mic), which froze the picture on the owner's phone; so the mic reuses this stream's
// audio track (muted between holds) and the camera is never re-requested unless its track actually ends.
let mediaStream = null;
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
                                                               audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    attachStream(stream);
    return true;
  } catch (e) {
    try {   // no mic? camera alone still works; the typed box covers questions
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } } });
      attachStream(stream);
      return true;
    } catch (e2) { frameSource = "still"; return false; }
  }
}
function attachStream(stream) {
  logEvent("camera", "stream attached");
  mediaStream = stream;
  stream.getAudioTracks().forEach((t) => { t.enabled = false; });   // muted until the mic is held
  cam.srcObject = new MediaStream(stream.getVideoTracks());
  cam.play().catch(() => {});
  frameSource = "camera";
  const vt = stream.getVideoTracks()[0];
  if (vt) {
    vt.addEventListener("ended", () => { logEvent("camera", "track ended"); cameraAlive(true); });
    vt.addEventListener("mute", () => { logEvent("camera", "track muted"); cam.play().catch(() => {}); });
    vt.addEventListener("unmute", () => { logEvent("camera", "track unmuted"); mutedSince = 0; cam.play().catch(() => {}); });
  }
}
// "Camera alive" check: iOS pauses or ends the video track when the page speaks, the mic is held, or the app is
// backgrounded. Every 2 s (and on return to the foreground) resume the element or re-acquire the stream.
let reacquiring = false, mutedSince = 0;
async function cameraAlive(force) {
  if (frameSource !== "camera" || document.body.dataset.screen === "start" || reacquiring) return;
  const vt = mediaStream && mediaStream.getVideoTracks()[0];
  // A track that stays muted is a black picture even when the element still reports "ready" (iOS after speech or
  // a mic hold): give it 1.5 s to unmute on its own, then re-acquire.
  if (vt && vt.muted) { mutedSince = mutedSince || performance.now(); } else mutedSince = 0;
  const stuckMuted = mutedSince && performance.now() - mutedSince > 1500;
  const dead = force || !cam.srcObject || !vt || vt.readyState === "ended" || stuckMuted;
  if (dead) {
    logEvent("camera", `dead (${vt ? vt.readyState + (vt.muted ? "/muted" : "") : "no track"}), re-acquiring`);
    reacquiring = true; mutedSince = 0;
    try { if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop()); await startCamera(); } finally { reacquiring = false; }
    return;
  }
  if (cam.paused) { logEvent("camera", "video paused, resuming"); cam.play().catch((e) => logError("cam.play", e)); }
}
setInterval(() => cameraAlive(false), 2000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") cameraAlive(false); });
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

function captureFrame(longSide = FRAME_LONG_SIDE) {
  return new Promise((resolve, reject) => {
    const src = frameSource === "camera" ? cam : still;
    const sw = frameSource === "camera" ? cam.videoWidth : still.naturalWidth;
    const sh = frameSource === "camera" ? cam.videoHeight : still.naturalHeight;
    if (!sw || !sh) return reject(new Error("No picture yet. Point the camera at the machine."));
    const scale = Math.min(1, longSide / Math.max(sw, sh));
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
// Draw one outline (mask or box) with its label pill. r = displayed picture rect, k = frame px -> screen px.
function drawOne(ctx, o, r, k, s) {
  ctx.save();
  ctx.translate(r.x, r.y); ctx.scale(k, k);
  ctx.lineWidth = (s.width || 3) / k; ctx.strokeStyle = s.stroke; ctx.fillStyle = s.fill;
  if (s.dashed) ctx.setLineDash([8 / k, 6 / k]);
  if (o.kind === "mask" && o.svg_path) {
    const [bl, bt, br, bb] = o.bounds;
    ctx.save();
    ctx.translate(bl, bt); ctx.scale((br - bl) / o.mask_w, (bb - bt) / o.mask_h);
    const p = new Path2D(o.svg_path);
    ctx.fill(p);
    ctx.lineWidth = (s.width || 3) / k / ((br - bl) / o.mask_w);
    ctx.stroke(p);
    ctx.restore();
  } else if (o.box) {
    const [x1, y1, x2, y2] = o.box;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  }
  ctx.restore();
  if (!s.label) return;
  const [bx, by, bx2, by2] = o.box || o.bounds;
  const sx = r.x + bx * k, sy = r.y + by * k;
  ctx.font = "600 14px 'Instrument Sans', system-ui, sans-serif";
  const tw = ctx.measureText(s.label).width, ph = 28, pw = tw + 22;
  // Keep the pill fully on screen, above the outline when there is room, else just inside its top edge.
  const px = Math.min(Math.max(6, sx), overlay.clientWidth - pw - 6);
  let py = sy - ph - 8;
  if (py < 6) py = Math.max(6, sy + 6);
  py = Math.min(py, overlay.clientHeight - ph - 6);
  // Do not overlap pills already placed (look-around draws several): step down until clear.
  if (s.placed) {
    const hits = (y) => s.placed.some((p) => px < p.x + p.w + 6 && px + pw + 6 > p.x && y < p.y + p.h + 4 && y + ph + 4 > p.y);
    let tries = 0;
    while (hits(py) && tries++ < 12) py += ph + 6;
    if (py > overlay.clientHeight - ph - 6) { py = sy - ph - 8; while (hits(py) && py > 6) py -= ph + 6; py = Math.max(6, py); }
    s.placed.push({ x: px, y: py, w: pw, h: ph });
  }
  ctx.fillStyle = s.pillFill || "rgba(6,9,13,0.88)";
  ctx.beginPath(); ctx.roundRect(px, py, pw, ph, 14); ctx.fill();
  ctx.strokeStyle = s.stroke; ctx.lineWidth = 1.5; ctx.setLineDash([]); ctx.stroke();
  ctx.fillStyle = s.text || "#EAF7FA"; ctx.textBaseline = "middle";
  ctx.fillText(s.label, px + 11, py + ph / 2);
}
function clearOverlay() {
  const dpr = window.devicePixelRatio || 1;
  overlay.width = overlay.clientWidth * dpr; overlay.height = overlay.clientHeight * dpr;
  const ctx = overlay.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, overlay.clientWidth, overlay.clientHeight);
  return ctx;
}
function drawOutline(opts = {}) {
  const ctx = clearOverlay();
  if (survey) { drawSurvey(ctx); return; }
  const o = run && run.outline; if (!o || !run.frame_size || !(o.box || o.bounds)) return;
  const need = run.status === "needs_input";
  const r = displayedRect(), k = r.w / run.frame_size[0];
  drawOne(ctx, o, r, k, {
    stroke: opts.flash ? "#FFFFFF" : need ? "#8A9099" : ACCENT,
    fill: need ? "rgba(138,144,153,0.10)" : "rgba(79,227,255,0.14)", dashed: need,
    label: (run.part_name || "") + (need ? " · too far to read" : ""), text: need ? "#A8ADB5" : "#EAF7FA",
  });
}

// ---------- look around (survey) ----------
let survey = null;      // {objects:[{name, box, outline}], frame_size, frame_id, chosen}
function drawSurvey(ctx) {
  const r = displayedRect(), k = r.w / survey.frame_size[0];
  const placed = [];   // pill rectangles already drawn, so later pills step aside
  const order = survey.objects.map((o, i) => i).sort((a, b) => survey.objects[a].box[1] - survey.objects[b].box[1]);  // top to bottom
  for (const i of order) {
    const obj = survey.objects[i];
    const chosen = survey.chosen === i, dimmed = survey.chosen != null && !chosen;
    drawOne(ctx, obj.outline || { kind: "box", box: obj.box }, r, k, {
      stroke: dimmed ? "rgba(79,227,255,0.35)" : chosen ? "#FFFFFF" : ACCENT, width: chosen ? 4 : 3,
      fill: dimmed ? "rgba(79,227,255,0.05)" : chosen ? "rgba(255,255,255,0.18)" : "rgba(79,227,255,0.14)",
      label: obj.name, text: dimmed ? "#8A9099" : "#EAF7FA", pillFill: chosen ? "rgba(79,227,255,0.25)" : undefined, placed,
    });
  }
}
const article = (n) => (/^[aeiou]/i.test(n) ? "an " : "a ") + n;
function listSentence(names) {
  if (!names.length) return "";
  const a = names.map(article);
  return "I see " + (a.length === 1 ? a[0] : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]) + ".";
}
function surveyHit(clientX, clientY) {
  if (!survey) return -1;
  const rect = overlay.getBoundingClientRect();
  const r = displayedRect(), k = r.w / survey.frame_size[0];
  const fx = (clientX - rect.left - r.x) / k, fy = (clientY - rect.top - r.y) / k;
  let best = -1, bestArea = Infinity;
  survey.objects.forEach((o, i) => {
    const [x1, y1, x2, y2] = o.box;
    const area = (x2 - x1) * (y2 - y1);
    if (fx >= x1 - 12 && fx <= x2 + 12 && fy >= y1 - 12 && fy <= y2 + 12 && area < bestArea) { best = i; bestArea = area; }
  });
  return best;
}
overlay.addEventListener("pointerdown", (e) => { if (!survey) return; const i = surveyHit(e.clientX, e.clientY); if (i >= 0) chooseObject(i); });

async function lookAround() {
  if (busy) return;
  busy = true; run = null; survey = null; prevStep = -1;
  for (const k in timing) delete timing[k]; footer();
  screen("live"); show("s-done", false); setTop("think"); setBottom("listen"); show("heard", false);
  const t0 = performance.now();
  try {
    const form = new FormData();
    let up = null; try { up = pendingFrame ? await pendingFrame : null; } catch (e) { up = null; }
    pendingFrame = null;
    if (up && up.frame_id) form.append("frame_id", up.frame_id);
    else { const f = await captureFrame(); timing.frame = `${f.w}x${f.h} ${Math.round(f.blob.size / 1024)} KB`; form.append("frame", f.blob, "frame.jpg"); }
    const s = await api("/survey", form, "look");
    timing.cost = `$${(s.usd || 0).toFixed(3)}`; footer();
    survey = { objects: s.objects || [], frame_size: s.frame_size, frame_id: s.frame_id, chosen: null };
    overlay.style.pointerEvents = survey.objects.length ? "auto" : "none";
    $("surveyCount").textContent = survey.objects.length ? `Looking around · ${survey.objects.length} thing${survey.objects.length === 1 ? "" : "s"} in view` : "Looking around";
    $("surveyHead").textContent = s.prompt;
    speak(survey.objects.length ? `${listSentence(survey.objects.map((o) => o.name))} ${s.prompt}` : s.prompt);
    setTop("survey"); setBottom("listen"); drawOutline();
  } catch (e) { fail(e.message); }
  busy = false;
}
function chooseObject(i, followUp) {
  if (!survey || !survey.objects[i]) return;
  survey.chosen = i;
  const name = survey.objects[i].name;
  drawOutline();
  if (followUp) { startRun(followUp, { name, box: survey.objects[i].box }); return; }
  $("surveyCount").textContent = name;
  $("surveyHead").textContent = "What do you need help with?";
  speak(`${name}. What do you need help with?`);
  setTop("survey"); setBottom("listen"); show("justUse", true);
}
// A spoken or typed answer while looking around: an object name picks it; the rest of the sentence is the question.
function surveyAnswer(text) {
  if (!survey) return false;
  const t = text.toLowerCase();
  let pick = survey.chosen;
  if (pick == null) {
    survey.objects.forEach((o, i) => {
      const words = o.name.toLowerCase().split(/\s+/).filter((w) => w.length > 2);
      if (pick == null && words.some((w) => t.includes(w))) pick = i;
    });
  }
  if (pick == null) { $("surveyHead").textContent = "Tap the one you mean, or say its name."; speak("Tap the one you mean, or say its name."); return true; }
  const isQuestion = /\b(how|what|why|where|which|can|do|does|should|turn|open|start|reset|fix|set)\b/i.test(text) && text.trim().split(/\s+/).length >= 3;
  chooseObject(pick, isQuestion ? text : null);
  return true;
}
$("look").addEventListener("click", () => { uploadFrameEarly().catch(() => {}); lookAround(); });
$("justUse").addEventListener("click", () => { if (survey && survey.chosen != null) chooseObject(survey.chosen, `how do I use the ${survey.objects[survey.chosen].name}?`); });
window.addEventListener("resize", () => drawOutline());
cam.addEventListener("loadedmetadata", () => drawOutline());
still.addEventListener("load", () => drawOutline());   // outlines scale by the picture's size, so redraw once it is known

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
async function api(path, form, name, signal) {
  const t0 = performance.now();
  const r = await fetch(BACKEND + path, { method: form ? "POST" : "GET", body: form, headers: { "X-Show-Me-Key": KEY }, signal });
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
  if (!opts.quiet) logEvent("render", `${r.status} step ${(r.current_step || 0) + 1}${r.display && r.display.step ? ": " + r.display.step.slice(0, 50) : ""}`);
  const passed = run && r.current_step > prevStep && prevStep >= 0 && r.status !== "failed";
  const failed = run && r.status === "guiding" && r.current_step === prevStep && /^Not yet\./.test((r.display || {}).step || "");
  run = r; prevStep = r.current_step;
  $("run").textContent = r.id ? `run ${r.id}${r.cost_usd ? ` · $${r.cost_usd.toFixed(3)}` : ""}` : "";
  footer();
  if (opts.quiet) { drawOutline(); return; }       // outline arrived after speech began
  speak(r.message);
  if (r.status === "done") { renderDone(r); drawOutline(); return; }
  screen("live"); show("s-done", false);
  if (r.status !== "guiding") stopAutoCheck();
  if (r.status === "needs_input") renderNeed(r);
  else if (r.status === "failed") { $("stepLine").textContent = r.message || "Something went wrong."; $("feedback").textContent = ""; setTop("step"); setBottom("listen"); }
  else renderStep(r, passed);
  if (failed) { const p = $("top"); p.classList.remove("shake"); void p.offsetWidth; p.classList.add("shake"); }
  if (passed) { setTimeout(flashPass, 50); } else drawOutline();
}

// ---------- auto-check: the camera watches the step, no button ----------
// Every CHECK_EVERY_MS a small frame goes to /check. Up to CHECK_MAX_INFLIGHT requests overlap. The server records
// each verdict; when it reports two consecutive passes the page asks the server to advance, and the server itself
// re-checks that rule. Results that arrive for an older step, or after a pass already advanced, are dropped.
const CHECK_TIMEOUT_MS = 8000;
const auto = { timer: null, seq: 0, inflight: 0, step: -1, startedAt: 0, paused: false, lastReason: "", advancing: false, lastDone: 0 };
function startAutoCheck() {
  stopAutoCheck();
  if (!run || run.status !== "guiding") return;
  logEvent("loop", `start step ${run.current_step + 1}`);
  auto.step = run.current_step; auto.startedAt = performance.now(); auto.lastReason = ""; auto.advancing = false; auto.firstPassAt = 0;
  auto.inflight = 0; auto.lastDone = performance.now();      // a fresh step never inherits a stuck in-flight count
  $("watch").classList.remove("pass"); $("watchLabel").textContent = "Watching…";
  auto.timer = setInterval(autoTick, CHECK_EVERY_MS);
  setTimeout(autoTick, 400);   // first look soon after the step is spoken
}
function stopAutoCheck() { if (auto.timer) clearInterval(auto.timer); auto.timer = null; }
// Keep the loop alive no matter what: if a run is being guided and nothing is watching it, start watching.
setInterval(() => { if (run && run.status === "guiding" && !auto.timer && !busy) startAutoCheck(); }, 3000);
async function autoTick() {
  if (!run || run.status !== "guiding" || run.current_step !== auto.step) { stopAutoCheck(); if (run && run.status === "guiding") startAutoCheck(); return; }
  if (auto.inflight >= CHECK_MAX_INFLIGHT && performance.now() - auto.lastDone > CHECK_TIMEOUT_MS + 2000) auto.inflight = 0;   // recover from lost requests
  if (auto.paused || busy || auto.advancing || auto.inflight >= CHECK_MAX_INFLIGHT) return;
  if (reacquiring || (frameSource === "camera" && !cam.videoWidth)) return;   // a stream swap is in progress: no frame to judge yet
  if (performance.now() - auto.startedAt > CHECK_GIVE_UP_MS) { stopAutoCheck(); giveUp("I've been watching for a minute and can't confirm it."); return; }
  const seq = ++auto.seq, step = auto.step, rid = run.id;
  auto.inflight++;
  try {
    const f = await captureFrame(CHECK_LONG_SIDE);
    const form = new FormData(); form.append("frame", f.blob, "check.jpg"); form.append("seq", String(seq)); form.append("auto_advance", "1");
    const t0 = performance.now();
    const ctl = new AbortController(); const tmo = setTimeout(() => ctl.abort(), CHECK_TIMEOUT_MS);
    let out;
    try { out = await api(`/runs/${rid}/check`, form, null, ctl.signal); } finally { clearTimeout(tmo); }
    timing.check = `${((performance.now() - t0) / 1000).toFixed(1)}s`; footer();
    const vv = (out && out.verdict) || {};
    if (vv.verdict) { dbg.lastVerdict = `${vv.verdict} (seq ${seq}, ${vv.seconds}s${vv.hedged ? ", hedged" : ""})`; dbg.lastVerdictAt = performance.now(); logEvent("check", `seq ${seq} ${vv.verdict} ${timing.check}${out.advanced ? " -> advanced" : ""}`); }
    else logEvent("check", `seq ${seq} no verdict${out && out.error ? ": " + out.error : ""}`);
    if (!run || run.id !== rid || run.current_step !== step || auto.step !== step) { logEvent("check", `seq ${seq} dropped (stale)`); return; }   // stale: step moved on
    if (!out.ok) return;
    const v = out.verdict || {};
    if (out.advanced && out.run) {                      // the server reached its two-pass rule and moved on
      auto.advancing = true;
      timing.advance = auto.firstPassAt ? `${((performance.now() - auto.firstPassAt) / 1000).toFixed(1)}s after first pass` : "on first pass";
      try { render(out.run); } catch (e) { run = out.run; }
      finally { auto.advancing = false; if (run && run.status === "guiding") startAutoCheck(); }
      return;
    }
    if (v.verdict === "pass") {
      $("watch").classList.add("pass"); $("watchLabel").textContent = "Looks done, confirming…";
      if (!auto.firstPassAt) auto.firstPassAt = performance.now();
      setTimeout(autoTick, 0);                            // confirm right away instead of waiting for the next tick
    } else {
      $("watch").classList.remove("pass"); $("watchLabel").textContent = v.verdict === "fail" ? "Not yet" : "Watching…";
      const reason = v.verdict === "fail" ? (v.reason || "") : "";
      $("feedback").textContent = reason ? firstSentence(reason)[0] : "";
      if (reason && reason !== auto.lastReason) { auto.lastReason = reason; }   // shown, not spoken: the next step is spoken
    }
  } catch (e) { logError("check", e); }
  finally { auto.inflight = Math.max(0, auto.inflight - 1); auto.lastDone = performance.now(); }
}
async function giveUp(why) {
  if (!run) return;
  try { const form = new FormData(); form.append("reason", why); render(await api(`/runs/${run.id}/help`, form)); } catch (e) {}
}
$("skip").addEventListener("click", () => { stopAutoCheck(); giveUp("skipped by the user"); });

// ---------- actions ----------
async function startRun(question, focus) {
  if (busy || !question) return;
  if (!focus && GENERAL.test(question)) { $("heard").textContent = `“${question}”`; show("heard", true); return lookAround(); }
  busy = true; runStartedAt = performance.now(); prevStep = -1;
  setTop("think"); setBottom("listen"); $("heard").textContent = `“${question}”`; show("heard", true);
  const t0 = performance.now();
  try {
    const form = new FormData(); form.append("question", question);
    if (focus) {
      form.append("focus_name", focus.name);
      if (focus.box) form.append("focus_box", focus.box.join(","));
      if (survey && survey.frame_id) form.append("frame_id", survey.frame_id);   // same frame the user chose from
    }
    if (!form.has("frame_id")) {
      let up = null; try { up = pendingFrame ? await pendingFrame : null; } catch (e) { up = null; }
      pendingFrame = null;
      if (up && up.frame_id) form.append("frame_id", up.frame_id);
      else { const f = await captureFrame(); form.append("frame", f.blob, "frame.jpg"); }
    }
    survey = null; overlay.style.pointerEvents = "none";
    const r = await api("/runs", form, "identify");
    if (r.status === "survey_needed") { busy = false; return lookAround(); }
    timing["first words"] = `${((performance.now() - t0) / 1000).toFixed(1)}s`; footer();
    render(r);
    busy = false;
    if (r.status === "guiding") startAutoCheck();
    if (r.status === "guiding" && r.id) {
      try { render(await api(`/runs/${r.id}/outline`, new FormData(), "outline"), { quiet: true }); } catch (e) {}
    }
  } catch (e) { logError("startRun", e); fail(e.message); }
  busy = false;
}
async function advance() {           // manual check: the fallback behind Skip / needs_input
  if (busy || !run) return;
  stopAutoCheck();
  busy = true; $("next").disabled = true; setTop("think");
  try {
    const f = await captureFrame(CHECK_LONG_SIDE);   // same size as auto-check: the verifier was tuned on it
    const form = new FormData(); form.append("frame", f.blob, "frame.jpg");
    const r = await api(`/runs/${run.id}/advance`, form, "check");
    render(r); if (r.status === "guiding") startAutoCheck();
  } catch (e) { fail(e.message); }
  $("next").disabled = false; busy = false;
}
async function confirmHuman() {
  if (busy || !run) return;
  busy = true;
  try { const r = await api(`/runs/${run.id}/confirm`, new FormData()); render(r); if (r.status === "guiding") startAutoCheck(); } catch (e) { fail(e.message); }
  busy = false;
}
function fail(msg) { $("warnText").textContent = ""; $("warn").classList.add("empty"); $("stepLine").textContent = msg; $("feedback").textContent = ""; $("segments").innerHTML = ""; setTop("step"); setBottom("listen"); }
function askAgain() {
  stopAutoCheck();
  run = null; prevStep = -1; survey = null; overlay.style.pointerEvents = "none"; speechSynthesis.cancel();
  for (const k in timing) delete timing[k]; footer(); $("run").textContent = "";
  show("s-done", false); screen("live"); drawOutline();
  setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Hold the mic and ask, or tap the camera to look around.";
  setBottom("listen");
}
$("next").addEventListener("click", advance);
$("confirm").addEventListener("click", confirmHuman);
$("askagain").addEventListener("click", askAgain);
$("again").addEventListener("click", askAgain);
$("repeat").addEventListener("click", () => { logEvent("ui", "repeat tapped"); cameraAlive(false); if (run) speak(run.message); });

// ---------- hold to talk ----------
// Preferred path: raw 16 kHz mono PCM from the Web Audio API, wrapped as WAV on the phone, so the server sends it to
// the voice model as-is (no ffmpeg). Fallback: MediaRecorder (webm/m4a), converted on the server.
let pcm = null;   // {ctx, stream, node, chunks: Float32Array[]} while a WAV recording is running

function wavFromFloat32(chunks, rate) {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) { const s = Math.max(-1, Math.min(1, c[i])); v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true); }
  return new Blob([buf], { type: "audio/wav" });
}
async function startPcm(stream) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error("no AudioContext");
  const ctx = new AC({ sampleRate: 16000 });
  if (ctx.sampleRate !== 16000) { await ctx.close(); throw new Error("16 kHz not supported"); }
  const src = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(node); node.connect(ctx.destination);
  pcm = { ctx, stream, node, chunks };
}
async function stopPcm() {
  const p = pcm; pcm = null;
  p.node.disconnect();
  if (p.release) p.release(); else p.stream.getTracks().forEach((t) => t.stop());   // shared track: mute, don't stop
  await p.ctx.close();
  return wavFromFloat32(p.chunks, 16000);
}

async function sendAudio(blob, type) {
  if (blob.size < 2000) { setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Too short. Hold the button while you speak."; return; }
  busy = true; setTop("think");
  try {
    const form = new FormData();
    form.append("audio", blob, "q." + (type.includes("wav") ? "wav" : type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm"));
    timing.audio = `${Math.round(blob.size / 1024)} KB ${type.includes("wav") ? "wav" : "compressed"}`; footer();
    const t = await api("/transcribe", form, "transcribe");
    busy = false;
    if (!t.text) { setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "I didn't catch that. Try again."; return; }
    if (survey && surveyAnswer(t.text)) return;
    await startRun(t.text);
  } catch (e) { busy = false; fail(e.message); }
}

async function micStream() {
  // Reuse the session stream's audio track (never a second getUserMedia while the camera runs, see startCamera).
  const live = mediaStream && mediaStream.getAudioTracks().find((t) => t.readyState === "live");
  if (live) { live.enabled = true; return { stream: new MediaStream([live]), shared: true }; }
  const s = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  return { stream: s, shared: false };
}
function releaseMic(ms) {
  if (ms.shared) mediaStream.getAudioTracks().forEach((t) => { t.enabled = false; });
  else ms.stream.getTracks().forEach((t) => t.stop());
}
async function startRecording() {
  if (busy || recorder || pcm) return;
  logEvent("mic", "hold");
  auto.paused = true;                 // the camera stops judging while the user is talking
  try {
    const ms = await micStream();
    const stream = ms.stream;
    try {
      await startPcm(stream);                       // WAV path
      pcm.release = () => releaseMic(ms);
    } catch (e) {                                   // fallback: MediaRecorder
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunks = [];
      recorder.ondataavailable = (ev) => ev.data.size && chunks.push(ev.data);
      recorder.onstop = () => {
        releaseMic(ms);
        const type = recorder.mimeType || "audio/webm";
        recorder = null; mic.classList.remove("rec"); micLabel.textContent = "Hold to talk";
        sendAudio(new Blob(chunks, { type }), type);
      };
      recorder.start();
    }
    mic.classList.add("rec"); micLabel.textContent = "Listening";
    speechSynthesis.cancel();
    if (!survey) { setTop("listen"); show("heard", false); for (const k in timing) delete timing[k]; footer(); uploadFrameEarly().catch(() => {}); }
  } catch (e) {
    auto.paused = false;
    setTop("idle"); $("t-idle").querySelector(".t-sub").textContent = "Microphone not available. Type your question instead.";
    show("typed", true);
  }
}
async function stopRecording() {
  logEvent("mic", "release");
  auto.paused = false;
  if (pcm) { mic.classList.remove("rec"); micLabel.textContent = "Hold to talk"; const blob = await stopPcm(); sendAudio(blob, "audio/wav"); return; }
  if (recorder && recorder.state === "recording") recorder.stop();
}
mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startRecording(); });
for (const ev of ["pointerup", "pointercancel", "pointerleave"]) mic.addEventListener(ev, stopRecording);
mic.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- typed question ----------
function toggleTyped() { const t = $("typed"); t.hidden = !t.hidden; if (!t.hidden) $("question").focus(); }
$("type").addEventListener("click", toggleTyped);
$("type2").addEventListener("click", toggleTyped);
$("ask").addEventListener("click", () => {
  const q = $("question").value.trim(); if (!q) return;
  show("typed", false);
  if (survey && surveyAnswer(q)) return;
  for (const k in timing) delete timing[k]; uploadFrameEarly().catch(() => {}); startRun(q);
});
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
  if (state === "survey") {
    survey = { frame_size: [576, 768], frame_id: "demo", chosen: null, objects: [
      { name: "breaker panel", box: [60, 40, 500, 740] }, { name: "main breaker", box: [250, 60, 420, 150] }, { name: "register breaker", box: [255, 300, 340, 350] }] };
    overlay.style.pointerEvents = "auto";
    $("surveyCount").textContent = "Looking around · 3 things in view"; $("surveyHead").textContent = "Which one do you need help with?";
    setTop("survey"); setBottom("listen"); drawOutline();
  }
}

// ---------- debug panel (long-press the footer) ----------
// Live state every 500 ms, the last 5 errors, the last 20 events, and a Report button that copies all of it.
const dbg = { errors: [], events: [], lastVerdict: null, lastVerdictAt: 0, open: false };
const stamp = () => new Date().toISOString().slice(11, 23);
function logEvent(kind, detail) {
  dbg.events.push(`${stamp()} ${kind}${detail ? " " + detail : ""}`);
  if (dbg.events.length > 20) dbg.events.shift();
}
function logError(where, err) {
  const msg = (err && (err.message || err.reason && err.reason.message)) || String(err);
  dbg.errors.push(`${stamp()} ${where}: ${msg}`.slice(0, 200));
  if (dbg.errors.length > 5) dbg.errors.shift();
  logEvent("error", `${where}: ${msg}`.slice(0, 120));
}
window.addEventListener("error", (e) => logError("window", e.error || e.message));
window.addEventListener("unhandledrejection", (e) => logError("promise", e.reason));
function trackState(t) { return t ? `${t.readyState}${t.muted ? "/muted" : ""}${t.enabled ? "" : "/disabled"}` : "none"; }
function loopState() {
  if (!run || run.status !== "guiding") return "idle";
  if (!auto.timer) return "stopped";
  if (auto.paused) return "paused (mic)";
  if (auto.inflight >= CHECK_MAX_INFLIGHT && performance.now() - auto.lastDone > CHECK_TIMEOUT_MS) return "STALLED";
  return "watching";
}
function debugState() {
  const vt = mediaStream && mediaStream.getVideoTracks()[0], at = mediaStream && mediaStream.getAudioTracks()[0];
  const age = dbg.lastVerdictAt ? `${((performance.now() - dbg.lastVerdictAt) / 1000).toFixed(1)}s ago` : "";
  return [
    `page      v${document.querySelector('script[src^="app.js"]')?.getAttribute("src").split("v=")[1] || "?"}  ${location.hostname}  ${navigator.userAgent.replace(/Mozilla\/5.0 \(/, "").slice(0, 60)}`,
    `screen    ${document.body.dataset.screen}  run ${run ? run.id + " " + run.status + " step " + (run.current_step + 1) + "/" + run.steps.length : "none"}`,
    `camera    track ${trackState(vt)}  video ${cam.paused ? "paused" : "playing"} ready=${cam.readyState} ${cam.videoWidth}x${cam.videoHeight}  source=${frameSource}`,
    `mic       track ${trackState(at)}  recording=${pcm ? "wav" : recorder ? "compressed" : "no"}`,
    `loop      ${loopState()}  inflight ${auto.inflight}/${CHECK_MAX_INFLIGHT}  seq ${auto.seq}  step ${auto.step + 1}  since-start ${auto.startedAt ? ((performance.now() - auto.startedAt) / 1000).toFixed(0) + "s" : "-"}`,
    `verdict   ${dbg.lastVerdict ? dbg.lastVerdict + "  " + age : "none yet"}`,
    `speech    ${"speechSynthesis" in window ? (speechSynthesis.speaking ? "speaking" : "quiet") : "unsupported"}  online=${navigator.onLine}  visible=${document.visibilityState}`,
    `timing    ${Object.entries(timing).map(([k, v]) => k + " " + v).join(" · ") || "-"}`,
  ].join("\n");
}
function renderDebug() {
  if (!dbg.open) return;
  $("dbgState").textContent = debugState();
  $("dbgErrors").textContent = dbg.errors.length ? dbg.errors.join("\n") : "none";
  $("dbgEvents").textContent = dbg.events.slice().reverse().join("\n") || "none";
}
function toggleDebug(on) { dbg.open = on == null ? !dbg.open : on; show("debug", dbg.open); renderDebug(); }
setInterval(renderDebug, 500);
$("dbgClose").addEventListener("click", () => toggleDebug(false));
$("dbgReport").addEventListener("click", async () => {
  const text = `SHOW ME REPORT ${new Date().toISOString()}\n${debugState()}\n\nERRORS\n${dbg.errors.join("\n") || "none"}\n\nEVENTS (newest first)\n${dbg.events.slice().reverse().join("\n") || "none"}`;
  try { await navigator.clipboard.writeText(text); $("dbgReport").textContent = "Copied"; }
  catch (e) { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); $("dbgReport").textContent = "Copied"; } catch (e2) { $("dbgReport").textContent = "Copy failed"; } ta.remove(); }
  setTimeout(() => { $("dbgReport").textContent = "Report"; }, 1500);
});
// long-press (600 ms) on the footer opens it
let pressTimer = null;
$("foot").addEventListener("pointerdown", () => { pressTimer = setTimeout(() => toggleDebug(true), 600); });
for (const ev of ["pointerup", "pointercancel", "pointerleave"]) $("foot").addEventListener(ev, () => clearTimeout(pressTimer));
$("foot").style.pointerEvents = "auto";
if (/debug=1/.test(location.hash)) toggleDebug(true);

// ---------- boot ----------
(async () => {
  await loadDevKey();
  KEY = getKey();
  const m = location.hash.match(/demo=([a-z_]+)/);
  if (m) demo(m[1]); else screen("start");
})();
