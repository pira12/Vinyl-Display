// Vinyl Display — one web app, two modes:
//   • display    : full-screen now-playing + synced lyrics (the iPad "screen")
//   • add        : browse/add the collection and remote-record sides
// A single read-only websocket feeds the live state to both modes.

const $ = (id) => document.getElementById(id);
const body = document.body;

// ---------- mode switching ----------
function initialMode() {
  const p = new URLSearchParams(location.search).get("mode");
  if (p === "add" || p === "display") return p;
  if (location.pathname === "/manage") return "add";
  return localStorage.getItem("vinyl_mode") || "display";
}

function setMode(mode) {
  body.dataset.mode = mode;
  localStorage.setItem("vinyl_mode", mode);
  document.querySelectorAll("#modebar button").forEach((b) =>
    b.classList.toggle("active", b.dataset.target === mode));
  if (mode === "add") loadCollection();
  showControls();
}

document.querySelectorAll("#modebar button").forEach((b) =>
  (b.onclick = () => setMode(b.dataset.target)));

// Auto-hide the mode switch in display mode after inactivity.
let hideTimer;
function showControls() {
  body.classList.remove("controls-hidden");
  clearTimeout(hideTimer);
  if (body.dataset.mode === "display")
    hideTimer = setTimeout(() => body.classList.add("controls-hidden"), 4000);
}
["pointerdown", "mousemove", "keydown"].forEach((e) =>
  document.addEventListener(e, showControls, { passive: true }));

// ---------- token auth (only needed for /api in add mode) ----------
function loadToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    localStorage.setItem("vinyl_token", fromUrl);
    url.searchParams.delete("token");
    history.replaceState({}, "", url.pathname + url.search);
  }
  return localStorage.getItem("vinyl_token") || "";
}
let token = loadToken();

async function api(path, opts = {}) {
  opts.headers = Object.assign({ "X-Auth-Token": token }, opts.headers || {});
  const res = await fetch(path, opts);
  if (res.status === 401) {
    $("auth-needed").classList.remove("hidden");
    throw new Error("unauthorized");
  }
  return res.json();
}

$("token-btn").onclick = () => {
  token = $("token-input").value.trim();
  localStorage.setItem("vinyl_token", token);
  $("auth-needed").classList.add("hidden");
  loadCollection();
};

// ---------- shared live state ----------
let state = null;
let lineEls = [];
let activeLine = -1;

function fmt(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function currentPosition() {
  if (!state || state.status !== "playing") return 0;
  const drift = (Date.now() - state.updated_at) * (state.speed_factor || 1);
  let pos = (state.position_ms || 0) + drift;
  const dur = state.track && state.track.duration_ms;
  if (dur) pos = Math.min(pos, dur);
  return Math.max(0, pos);
}

function applyState(next) {
  const changed =
    !state || !state.track || !next.track ||
    (state.track && next.track && state.track.title !== next.track.title) ||
    (!!state.track !== !!next.track);
  state = next;

  // display mode
  if (state.status === "playing" && state.track) {
    $("overlay").classList.add("hidden");
    $("now").classList.remove("hidden");
    if (changed) renderTrack();
  } else {
    $("now").classList.add("hidden");
    $("overlay").classList.remove("hidden");
    $("overlay-text").textContent =
      state.status === "unknown" ? "Unknown record" :
      state.status === "listening" ? "Listening…" : "Waiting for a record…";
  }
  renderNowPlayingBar();
}

// ---------- display rendering ----------
function renderTrack() {
  const t = state.track || {}, a = state.album || {};
  $("title").textContent = t.title || "—";
  $("artist").textContent = t.artist || a.artist || "—";
  $("album").textContent = [a.title, a.year].filter(Boolean).join(" · ") || "";
  setArtwork(a.art_url);
  $("duration").textContent = fmt(t.duration_ms);

  if (state.next_track && state.next_track.title) {
    $("next-title").textContent = state.next_track.title;
    $("next").classList.remove("hidden");
  } else {
    $("next").classList.add("hidden");
  }
  renderLyrics();
}

function setArtwork(url) {
  if (!url) {
    $("art").removeAttribute("src");
    $("art").style.visibility = "hidden";
    $("backdrop").classList.remove("show");
    return;
  }
  $("art").classList.add("fade-out");
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    $("art").src = url;
    $("art").style.visibility = "visible";
    $("art").classList.remove("fade-out");
    $("backdrop").style.backgroundImage = `url("${url}")`;
    $("backdrop").classList.add("show");
    applyAccent(img);
  };
  img.onerror = () => { $("art").style.visibility = "hidden"; $("art").classList.remove("fade-out"); };
  img.src = url;
}

function applyAccent(img) {
  try {
    const c = document.createElement("canvas");
    const n = 16; c.width = n; c.height = n;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0, n, n);
    const data = ctx.getImageData(0, 0, n, n).data;
    let best = null, bestScore = -1;
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const max = Math.max(r, g, b), min = Math.min(r, g, b);
      const sat = max === 0 ? 0 : (max - min) / max;
      const score = sat * (max / 255);
      if (max > 40 && score > bestScore) { bestScore = score; best = [r, g, b]; }
    }
    if (best) document.documentElement.style.setProperty("--accent", `rgb(${best[0]},${best[1]},${best[2]})`);
  } catch (e) { /* canvas blocked — keep default accent */ }
}

function renderLyrics() {
  $("lyrics").innerHTML = "";
  lineEls = []; activeLine = -1;
  const lyr = state.lyrics || { synced: false, lines: [] };
  $("lyrics").classList.toggle("plain", !lyr.synced);
  if (!lyr.lines || !lyr.lines.length) { $("lyrics-empty").classList.remove("hidden"); return; }
  $("lyrics-empty").classList.add("hidden");
  for (const line of lyr.lines) {
    const div = document.createElement("div");
    div.className = "line";
    div.textContent = line.text || " ";
    $("lyrics").appendChild(div);
    lineEls.push(div);
  }
}

function updateLyricScroll(pos) {
  const lyr = state.lyrics;
  if (!lyr || !lyr.synced || !lineEls.length) return;
  let idx = -1;
  for (let i = 0; i < lyr.lines.length; i++) {
    if (lyr.lines[i].t != null && lyr.lines[i].t <= pos) idx = i; else break;
  }
  if (idx === activeLine) return;
  if (activeLine >= 0 && lineEls[activeLine]) {
    lineEls[activeLine].classList.remove("active");
    lineEls[activeLine].classList.add("passed");
  }
  activeLine = idx;
  if (idx >= 0 && lineEls[idx]) {
    const el = lineEls[idx];
    el.classList.add("active"); el.classList.remove("passed");
    const container = $("lyrics").parentElement;
    const anchor = container.clientHeight * 0.42;
    $("lyrics").style.transform = `translateY(${-(el.offsetTop - anchor + el.clientHeight / 2)}px)`;
  }
}

function renderNowPlayingBar() {
  const np = $("np");
  if (state && state.status === "playing" && state.track) {
    np.classList.remove("hidden");
    $("np-title").textContent = state.track.title || "";
    $("np-sub").textContent = (state.track.artist || "") +
      (state.album && state.album.title ? " · " + state.album.title : "");
    const art = state.album && state.album.art_url;
    if (art) { $("np-art").src = art; $("np-art").style.visibility = "visible"; }
    else { $("np-art").style.visibility = "hidden"; }
  } else {
    np.classList.add("hidden");
  }
}

// animation loop: progress bars + lyric highlight
function tick() {
  if (state && state.status === "playing" && state.track) {
    const pos = currentPosition();
    const dur = state.track.duration_ms || 0;
    const pct = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
    $("elapsed").textContent = fmt(pos);
    $("bar-fill").style.width = pct;
    $("np-fill").style.width = pct;
    updateLyricScroll(pos);
  }
  requestAnimationFrame(tick);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => applyState(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connect, 1500);
  ws.onerror = () => ws.close();
}

// ---------- collection mode ----------
let canRecord = false;

function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3500);
}

function sidesOf(a) {
  const set = [];
  for (const t of a.tracklist || []) {
    const p = (t.position || "").trim();
    if (p && /[A-Za-z]/.test(p[0]) && !set.includes(p[0].toUpperCase())) set.push(p[0].toUpperCase());
  }
  return set.length ? set : ["A"];
}

function albumCard(a) {
  const card = document.createElement("div");
  card.className = "card";
  const img = document.createElement("img");
  if (a.art_url) img.src = a.art_url;
  card.appendChild(img);
  const info = document.createElement("div");
  info.className = "info";
  info.innerHTML = `<div class="title">${a.title}</div>` +
    `<div class="sub">${a.artist}${a.year ? " · " + a.year : ""} · ${a.track_count} tracks</div>`;
  const sidesRow = document.createElement("div");
  sidesRow.className = "sides";
  for (const side of sidesOf(a)) {
    const done = (a.enrolled_sides || []).includes(side);
    const btn = document.createElement("button");
    btn.className = "side-btn" + (done ? " done" : "");
    btn.textContent = done ? `Side ${side} ✓` : `Record side ${side}`;
    btn.disabled = !canRecord;
    btn.onclick = () => startRecording(a.id, side);
    sidesRow.appendChild(btn);
  }
  info.appendChild(sidesRow);
  card.appendChild(info);
  return card;
}

async function loadCollection() {
  let data;
  try { data = await api("/api/collection"); } catch (e) { return; }
  canRecord = !!(data.recording && data.recording.can_record);
  $("no-record").classList.toggle("hidden", canRecord);
  const list = $("collection");
  list.innerHTML = "";
  for (const a of data.albums) list.appendChild(albumCard(a));
  $("count").textContent = data.albums.length;
  if (data.recording && data.recording.recording && data.recording.session)
    showRecBar(data.recording.session.side);
}

async function search() {
  const q = $("q").value.trim();
  if (!q) return;
  $("results").innerHTML = '<p class="muted">Searching…</p>';
  let data;
  try { data = await api("/api/search?q=" + encodeURIComponent(q)); } catch (e) { return; }
  const box = $("results"); box.innerHTML = "";
  for (const r of data.results) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<img ${r.art_url ? `src="${r.art_url}"` : ""}/>` +
      `<div class="info"><div class="title">${r.title}</div>` +
      `<div class="sub">${r.artist}${r.year ? " · " + r.year : ""}` +
      `${r.tracks ? " · " + r.tracks + " trks" : ""}${r.country ? " · " + r.country : ""}</div></div>`;
    const add = document.createElement("button");
    add.textContent = "Add";
    add.onclick = async () => {
      add.disabled = true; add.textContent = "Adding…";
      let res;
      try {
        res = await api("/api/albums", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ release_mbid: r.release_mbid }),
        });
      } catch (e) { return; }
      if (res.error) { toast("Error: " + res.error); add.disabled = false; add.textContent = "Add"; return; }
      toast("Added " + res.album.title);
      card.remove(); loadCollection();
    };
    card.appendChild(add);
    box.appendChild(card);
  }
  if (!data.results.length) box.innerHTML = '<p class="muted">No matches.</p>';
}

let recSide = null;
function showRecBar(side) { recSide = side; $("rec-label").textContent = "side " + side; $("rec-bar").classList.remove("hidden"); }

async function startRecording(albumId, side) {
  let res;
  try {
    res = await api("/api/record/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ album_id: albumId, side }),
    });
  } catch (e) { return; }
  if (res.error) { toast("Error: " + res.error); return; }
  showRecBar(side);
}

async function stopRecording() {
  $("stop-btn").disabled = true; $("stop-btn").textContent = "Saving…";
  let res;
  try { res = await api("/api/record/stop", { method: "POST" }); }
  finally { $("stop-btn").disabled = false; $("stop-btn").textContent = "Stop & save"; }
  $("rec-bar").classList.add("hidden");
  if (res.error) { toast("Error: " + res.error); return; }
  const r = res.result || {};
  toast(`Saved side ${recSide}: ${r.tracks} tracks (${r.duration_s}s).`);
  loadCollection();
}

async function cancelRecording() {
  try { await api("/api/record/cancel", { method: "POST" }); } catch (e) {}
  $("rec-bar").classList.add("hidden");
}

$("search-btn").onclick = search;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
$("stop-btn").onclick = stopRecording;
$("cancel-btn").onclick = cancelRecording;

// ---------- settings ----------
let settingsValues = {};   // last values loaded from the server

function deviceFromSelect() {
  const v = $("set-device").value;
  if (v === "") return null;
  return /^\d+$/.test(v) ? parseInt(v, 10) : v;
}

function readSettings() {
  return {
    "audio.device": deviceFromSelect(),
    "recognition.backend": $("set-backend").value,
    "audio.silence_rms": parseFloat($("set-silence").value),
    "playback.speed_factor": parseFloat($("set-speed").value),
    "recognition.interval_seconds": parseFloat($("set-slow").value),
    "recognition.fast_interval_seconds": parseFloat($("set-fast").value),
    "recognition.min_match_score": parseInt($("set-score").value, 10),
    "lyrics.enabled": $("set-lyrics").checked,
    "metadata.musicbrainz_useragent": $("set-ua").value.trim(),
  };
}

async function loadSettings() {
  let data;
  try { data = await api("/api/settings"); } catch (e) { return; }
  settingsValues = data.values || {};
  const v = settingsValues;

  const dsel = $("set-device");
  dsel.innerHTML = '<option value="">System default</option>';
  for (const d of data.devices || []) {
    const o = document.createElement("option");
    o.value = String(d.index);
    o.textContent = `${d.index}: ${d.name}`;
    dsel.appendChild(o);
  }
  const dev = v["audio.device"];
  if (dev === null || dev === undefined) {
    dsel.value = "";
  } else if ([...dsel.options].some((o) => o.value === String(dev))) {
    dsel.value = String(dev);
  } else {
    // configured device not currently detected — keep it as a choice.
    const o = document.createElement("option");
    o.value = String(dev); o.textContent = `${dev} (not detected)`;
    dsel.appendChild(o); dsel.value = String(dev);
  }

  $("set-backend").value = v["recognition.backend"] || "olaf";
  $("set-silence").value = v["audio.silence_rms"];
  $("set-speed").value = v["playback.speed_factor"];
  $("set-slow").value = v["recognition.interval_seconds"];
  $("set-fast").value = v["recognition.fast_interval_seconds"];
  $("set-score").value = v["recognition.min_match_score"];
  $("set-lyrics").checked = !!v["lyrics.enabled"];
  $("set-ua").value = v["metadata.musicbrainz_useragent"] || "";
}

async function saveSettings() {
  const now = readSettings();
  const changes = {};
  for (const k in now) {
    if (JSON.stringify(now[k]) !== JSON.stringify(settingsValues[k]))
      changes[k] = now[k];
  }
  if (!Object.keys(changes).length) { toast("No changes to save."); return; }

  const btn = $("settings-save");
  btn.disabled = true; btn.textContent = "Saving…";
  let res;
  try {
    res = await api("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
  } catch (e) { btn.disabled = false; btn.textContent = "Save settings"; return; }
  btn.disabled = false; btn.textContent = "Save settings";

  if (res.error) {
    const fields = res.fields ? " (" + Object.keys(res.fields).join(", ") + ")" : "";
    toast("Couldn't save" + fields + ": " + res.error);
    return;
  }
  settingsValues = Object.assign({}, settingsValues, changes);
  if (res.restart_required && res.restart_required.length)
    toast("Saved. Restart the app to apply: " + res.restart_required.join(", "));
  else
    toast("Saved and applied.");
}

$("settings-btn").onclick = () => {
  const panel = $("settings-panel");
  const opening = panel.classList.contains("hidden");
  panel.classList.toggle("hidden");
  if (opening) loadSettings();
};
$("settings-save").onclick = saveSettings;

// ---------- boot ----------
setMode(initialMode());
connect();
requestAnimationFrame(tick);
