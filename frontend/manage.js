// Companion app: browse the collection, add albums from MusicBrainz, and
// remote-record a side on the Pi to fingerprint it.

const $ = (id) => document.getElementById(id);
let canRecord = false;

async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

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
  const data = await api("/api/collection");
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

$("search-btn").onclick = search;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
$("stop-btn").onclick = stopRecording;
$("cancel-btn").onclick = cancelRecording;

loadCollection();
