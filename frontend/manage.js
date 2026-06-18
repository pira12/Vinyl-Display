// Companion app: browse the collection, add albums from MusicBrainz, and
// remote-record a side on the Pi to fingerprint it.

const $ = (id) => document.getElementById(id);
let canRecord = false;

// ---- token auth ----
// Token can arrive as ?token=… (then it's saved and stripped from the URL),
// or be entered manually. It's sent as X-Auth-Token on every API call.
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

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3500);
}

function albumCard(a) {
  const card = document.createElement("div");
  card.className = "card";

  const img = document.createElement("img");
  if (a.art_url) img.src = a.art_url;
  card.appendChild(img);

  const info = document.createElement("div");
  info.className = "info";
  info.innerHTML =
    `<div class="title">${a.title}</div>` +
    `<div class="sub">${a.artist}${a.year ? " · " + a.year : ""} · ${a.track_count} tracks</div>`;

  // A side button per side present on the record.
  const sidesRow = document.createElement("div");
  sidesRow.className = "sides";
  const allSides = sidesOf(a);
  for (const side of allSides) {
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

// Derive side letters from track position labels (A1, B2…); default to ["A"].
function sidesOf(a) {
  const set = [];
  for (const t of a.tracklist || []) {
    const p = (t.position || "").trim();
    if (p && /[A-Za-z]/.test(p[0]) && !set.includes(p[0].toUpperCase()))
      set.push(p[0].toUpperCase());
  }
  return set.length ? set : ["A"];
}

async function loadCollection() {
  let data;
  try {
    data = await api("/api/collection");
  } catch (e) {
    return;   // 401 banner already shown
  }
  canRecord = !!(data.recording && data.recording.can_record);
  $("no-record").classList.toggle("hidden", canRecord);

  const list = $("collection");
  list.innerHTML = "";
  for (const a of data.albums) list.appendChild(albumCard(a));
  $("count").textContent = data.albums.length;

  // Restore an in-progress recording (e.g. after a phone refresh).
  if (data.recording && data.recording.recording && data.recording.session) {
    showRecBar(data.recording.session.side, data.recording.session.album_id);
  }
}

async function search() {
  const q = $("q").value.trim();
  if (!q) return;
  $("results").innerHTML = '<p class="muted">Searching…</p>';
  const data = await api("/api/search?q=" + encodeURIComponent(q));
  const box = $("results");
  box.innerHTML = "";
  for (const r of data.results) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      `<img ${r.art_url ? `src="${r.art_url}"` : ""}/>` +
      `<div class="info"><div class="title">${r.title}</div>` +
      `<div class="sub">${r.artist}${r.year ? " · " + r.year : ""}` +
      `${r.tracks ? " · " + r.tracks + " trks" : ""}${r.country ? " · " + r.country : ""}</div></div>`;
    const add = document.createElement("button");
    add.textContent = "Add";
    add.onclick = async () => {
      add.disabled = true; add.textContent = "Adding…";
      const res = await api("/api/albums", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ release_mbid: r.release_mbid }),
      });
      if (res.error) { toast("Error: " + res.error); add.disabled = false; add.textContent = "Add"; return; }
      toast("Added " + res.album.title);
      card.remove();
      loadCollection();
    };
    card.appendChild(add);
    box.appendChild(card);
  }
  if (!data.results.length) box.innerHTML = '<p class="muted">No matches.</p>';
}

let recAlbum = null, recSide = null;

function showRecBar(side, albumId) {
  recAlbum = albumId; recSide = side;
  $("rec-label").textContent = "side " + side;
  $("rec-bar").classList.remove("hidden");
}

async function startRecording(albumId, side) {
  const res = await api("/api/record/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ album_id: albumId, side }),
  });
  if (res.error) { toast("Error: " + res.error); return; }
  showRecBar(side, albumId);
}

async function stopRecording() {
  $("stop-btn").disabled = true; $("stop-btn").textContent = "Saving…";
  const res = await api("/api/record/stop", { method: "POST" });
  $("rec-bar").classList.add("hidden");
  $("stop-btn").disabled = false; $("stop-btn").textContent = "Stop & save";
  if (res.error) { toast("Error: " + res.error); return; }
  const r = res.result || {};
  toast(`Saved side ${recSide}: ${r.tracks} tracks (${r.duration_s}s).`);
  loadCollection();
}

async function cancelRecording() {
  await api("/api/record/cancel", { method: "POST" });
  $("rec-bar").classList.add("hidden");
}

// ---- live now-playing bar (read-only websocket, doubles as a remote) ----
let npState = null;

function fmt(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function renderNowPlaying(m) {
  npState = m;
  const np = $("np");
  if (m.status === "playing" && m.track) {
    np.classList.remove("hidden");
    $("np-title").textContent = m.track.title || "";
    $("np-sub").textContent = (m.track.artist || "") +
      (m.album && m.album.title ? " · " + m.album.title : "");
    const art = m.album && m.album.art_url;
    if (art) { $("np-art").src = art; $("np-art").style.visibility = "visible"; }
    else { $("np-art").style.visibility = "hidden"; }
  } else {
    np.classList.add("hidden");
  }
}

function tickNowPlaying() {
  if (npState && npState.status === "playing" && npState.track) {
    const drift = (Date.now() - npState.updated_at) * (npState.speed_factor || 1);
    const dur = npState.track.duration_ms || 0;
    const pos = Math.min((npState.position_ms || 0) + drift, dur || Infinity);
    $("np-fill").style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
  }
  requestAnimationFrame(tickNowPlaying);
}

function connectNowPlaying() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => renderNowPlaying(JSON.parse(ev.data));
  ws.onclose = () => setTimeout(connectNowPlaying, 2000);
  ws.onerror = () => ws.close();
}

$("search-btn").onclick = search;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
$("stop-btn").onclick = stopRecording;
$("cancel-btn").onclick = cancelRecording;

loadCollection();
connectNowPlaying();
requestAnimationFrame(tickNowPlaying);
