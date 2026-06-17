// Vinyl Display frontend.
// Receives state over the websocket and runs its own clock between updates so
// the progress bar and synced lyrics stay smooth without constant server traffic.

const els = {
  overlay: document.getElementById("overlay"),
  overlayText: document.getElementById("overlay-text"),
  now: document.getElementById("now"),
  art: document.getElementById("art"),
  title: document.getElementById("title"),
  artist: document.getElementById("artist"),
  album: document.getElementById("album"),
  barFill: document.getElementById("bar-fill"),
  elapsed: document.getElementById("elapsed"),
  duration: document.getElementById("duration"),
  next: document.getElementById("next"),
  nextTitle: document.getElementById("next-title"),
  lyrics: document.getElementById("lyrics"),
  lyricsEmpty: document.getElementById("lyrics-empty"),
};

let state = null;
let lineEls = [];      // rendered lyric line elements
let activeLine = -1;

function fmt(ms) {
  if (!ms || ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Position now, interpolated from the last server sync.
function currentPosition() {
  if (!state || state.status !== "playing") return 0;
  const drift = (Date.now() - state.updated_at) * (state.speed_factor || 1);
  let pos = (state.position_ms || 0) + drift;
  const dur = state.track && state.track.duration_ms;
  if (dur) pos = Math.min(pos, dur);
  return Math.max(0, pos);
}

function renderTrack() {
  const t = state.track || {};
  const a = state.album || {};
  els.title.textContent = t.title || "—";
  els.artist.textContent = t.artist || a.artist || "—";
  const parts = [a.title, a.year].filter(Boolean);
  els.album.textContent = parts.join(" · ") || "";

  if (a.art_url) {
    els.art.src = a.art_url;
    els.art.style.visibility = "visible";
  } else {
    els.art.removeAttribute("src");
    els.art.style.visibility = "hidden";
  }
  els.duration.textContent = fmt(t.duration_ms);

  if (state.next_track && state.next_track.title) {
    els.nextTitle.textContent = state.next_track.title;
    els.next.classList.remove("hidden");
  } else {
    els.next.classList.add("hidden");
  }

  renderLyrics();
}

function renderLyrics() {
  els.lyrics.innerHTML = "";
  lineEls = [];
  activeLine = -1;
  const lyr = state.lyrics || { synced: false, lines: [] };
  els.lyrics.classList.toggle("plain", !lyr.synced);

  if (!lyr.lines || lyr.lines.length === 0) {
    els.lyricsEmpty.classList.remove("hidden");
    return;
  }
  els.lyricsEmpty.classList.add("hidden");

  for (const line of lyr.lines) {
    const div = document.createElement("div");
    div.className = "line";
    div.textContent = line.text || " ";
    div.dataset.t = line.t == null ? "" : line.t;
    els.lyrics.appendChild(div);
    lineEls.push(div);
  }
}

function updateLyricScroll(pos) {
  const lyr = state.lyrics;
  if (!lyr || !lyr.synced || lineEls.length === 0) return;

  let idx = -1;
  for (let i = 0; i < lyr.lines.length; i++) {
    if (lyr.lines[i].t != null && lyr.lines[i].t <= pos) idx = i;
    else break;
  }
  if (idx === activeLine) return;

  if (activeLine >= 0 && lineEls[activeLine]) {
    lineEls[activeLine].classList.remove("active");
    lineEls[activeLine].classList.add("passed");
  }
  activeLine = idx;
  if (idx >= 0 && lineEls[idx]) {
    const el = lineEls[idx];
    el.classList.add("active");
    el.classList.remove("passed");
    // Center the active line within the lyrics viewport.
    const container = els.lyrics.parentElement;
    const offset = el.offsetTop - container.clientHeight / 2 + el.clientHeight / 2;
    els.lyrics.style.transform = `translateY(${-offset}px)`;
  }
}

function showOverlay(text) {
  els.overlayText.textContent = text;
  els.overlay.classList.remove("hidden");
  els.now.classList.add("hidden");
}

function showNow() {
  els.overlay.classList.add("hidden");
  els.now.classList.remove("hidden");
}

function applyState(next) {
  const trackChanged =
    !state || !state.track || !next.track ||
    (state.track && next.track && state.track.key !== next.track.key) ||
    (!!state.track !== !!next.track);
  state = next;

  if (state.status === "playing" && state.track) {
    showNow();
    if (trackChanged) renderTrack();
  } else if (state.status === "unknown") {
    showOverlay("Unknown record");
  } else if (state.status === "listening") {
    showOverlay("Listening…");
  } else {
    showOverlay("Waiting for a record…");
  }
}

// Animation loop: progress bar + lyric highlight.
function tick() {
  if (state && state.status === "playing" && state.track) {
    const pos = currentPosition();
    const dur = state.track.duration_ms || 0;
    els.elapsed.textContent = fmt(pos);
    els.barFill.style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
    updateLyricScroll(pos);
  }
  requestAnimationFrame(tick);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => applyState(JSON.parse(ev.data));
  ws.onclose = () => {
    showOverlay("Reconnecting…");
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
}

connect();
requestAnimationFrame(tick);
