import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, setToken, Unauthorized } from "../api.js";
import AlbumCard from "./AlbumCard.jsx";
import AlbumDetail from "./AlbumDetail.jsx";
import SettingsPanel from "./SettingsPanel.jsx";
import MicStatus from "./MicStatus.jsx";

export default function CollectionView({ state, mic, authNeeded, setAuthNeeded, toast }) {
  const [albums, setAlbums] = useState([]);
  const [canRecord, setCanRecord] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [addingId, setAddingId] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [target, setTarget] = useState(null); // { albumId, side } while enrolling
  const [tokenInput, setTokenInput] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("recent");
  const searchSeq = useRef(0);

  const guard = useCallback(
    async (fn) => {
      try {
        return await fn();
      } catch (e) {
        if (e instanceof Unauthorized) setAuthNeeded(true);
        else toast(e.message || String(e));
        return null;
      }
    },
    [setAuthNeeded, toast]
  );

  const refresh = useCallback(async () => {
    const data = await guard(() => api.get("/api/collection"));
    if (!data) return;
    setAlbums(data.albums || []);
    setCanRecord(!!(data.recording && data.recording.can_record));
  }, [guard]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // When AcoustID auto-labels an unknown record, surface it and refresh.
  useEffect(() => {
    if (mic.lastIdentified) {
      toast(`Identified: ${mic.lastIdentified.title} — play a side to learn it`);
      refresh();
    }
  }, [mic.lastIdentified, refresh, toast]);

  // Live search: debounce typing, and ignore a slow response if a newer query
  // has already been issued (stale-response guard).
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearching(false);
      return;
    }
    const mySeq = ++searchSeq.current;
    setSearching(true);
    const timer = setTimeout(async () => {
      const data = await guard(() => api.get("/api/search?q=" + encodeURIComponent(q)));
      if (mySeq !== searchSeq.current) return; // superseded by a newer query
      setSearching(false);
      setResults(data ? data.results || [] : []);
    }, 300);
    return () => clearTimeout(timer);
  }, [query, guard]);

  function clearSearch() {
    searchSeq.current++; // drop any in-flight response
    setQuery("");
    setResults(null);
    setSearching(false);
  }

  async function addAlbum(mbid) {
    setAddingId(mbid);
    const res = await guard(() => api.postJson("/api/albums", { release_mbid: mbid }));
    setAddingId("");
    if (res && res.album) {
      toast("Added " + res.album.title);
      setResults((r) => (r ? r.filter((x) => x.release_mbid !== mbid) : r));
      // Show it immediately, then reconcile with the server.
      setAlbums((prev) =>
        prev.some((a) => a.id === res.album.id) ? prev : [...prev, res.album]
      );
      refresh();
    }
  }

  async function editAlbum(id, fields) {
    const res = await guard(() => api.patchJson("/api/albums/" + id, fields));
    if (res && res.album) {
      setAlbums((prev) => prev.map((a) => (a.id === id ? { ...a, ...res.album } : a)));
      toast("Saved changes");
      return true;
    }
    return false;
  }

  async function deleteAlbum(id) {
    const res = await guard(() => api.del("/api/albums/" + id));
    if (res) {
      setAlbums((prev) => prev.filter((a) => a.id !== id));
      setSelectedId(null);
      toast("Deleted record");
    }
  }

  async function onRecord(albumId, side) {
    const ok = await mic.startEnroll(albumId, side);
    if (ok) setTarget({ albumId, side });
  }
  async function stopRecord() {
    const result = await mic.stopEnroll();
    setTarget(null);
    if (result) toast(`Saved side: ${result.tracks} tracks (${result.duration_s}s).`);
    refresh();
  }
  async function cancelRecord() {
    await mic.cancelEnroll();
    setTarget(null);
  }

  function saveToken() {
    setToken(tokenInput.trim());
    setAuthNeeded(false);
    refresh();
  }

  const shown = useMemo(() => {
    let list = [...albums];
    const f = filter.trim().toLowerCase();
    if (f) {
      list = list.filter((a) =>
        ((a.title || "") + " " + (a.artist || "")).toLowerCase().includes(f)
      );
    }
    if (sort === "recent") list.reverse(); // API is insertion order (oldest first)
    else if (sort === "artist")
      list.sort((a, b) => (a.artist || "").localeCompare(b.artist || ""));
    else if (sort === "title")
      list.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    else if (sort === "enrolled")
      list.sort(
        (a, b) => (b.enrolled_sides?.length || 0) - (a.enrolled_sides?.length || 0)
      );
    return list;
  }, [albums, filter, sort]);

  const selected = selectedId ? albums.find((a) => a.id === selectedId) : null;

  return (
    <div className="mx-auto max-w-[960px] px-4 pb-24 pt-[4.5rem]">
      <div className="mb-5 flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold tracking-tight">Collection</h1>
        <button
          onClick={() => setShowSettings((s) => !s)}
          className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-sm font-semibold text-fg"
        >
          Settings
        </button>
      </div>

      <MicStatus mic={mic} state={state} />

      {authNeeded && (
        <div className="mb-4 rounded-xl border border-[var(--accent)] bg-[#2a1f10] p-3 text-sm">
          This device isn't authorized. Open the <b>?token=…</b> link from the server
          logs, or paste the token:
          <div className="mt-2 flex gap-2">
            <input
              className="flex-1 rounded-lg border border-[#2a2a33] bg-panel p-2.5 text-fg"
              placeholder="token"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
            />
            <button
              onClick={saveToken}
              className="rounded-lg px-4 py-2 font-semibold text-[#181400]"
              style={{ background: "var(--accent)" }}
            >
              Save
            </button>
          </div>
        </div>
      )}

      {showSettings && <SettingsPanel onToast={toast} />}

      {!canRecord && (
        <div className="mb-4 rounded-xl border border-[var(--accent)] bg-[#2a1f10] p-3 text-sm">
          Recording is unavailable (the server has no Olaf backend). You can still
          search and add albums.
        </div>
      )}

      <div className="relative flex items-center">
        <input
          className="w-full rounded-lg border border-[#2a2a33] bg-panel p-3 pr-10"
          placeholder="Search an album to add…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            onClick={clearSearch}
            aria-label="Clear search"
            className="absolute right-2 flex h-7 w-7 items-center justify-center rounded-full text-muted hover:bg-[#2a2a33] hover:text-fg"
          >
            ✕
          </button>
        )}
      </div>

      {searching && <p className="mt-3 text-muted">Searching…</p>}
      {results && !searching && (
        <div className="mt-3 space-y-2">
          {results.length === 0 && <p className="text-muted">No matches.</p>}
          {results.map((r) => {
            const owned = albums.some((a) => a.id === r.release_mbid);
            return (
              <div key={r.release_mbid} className="flex items-center gap-3 rounded-xl bg-panel p-3">
                <img
                  src={r.art_url || undefined}
                  alt=""
                  className="h-14 w-14 rounded-lg bg-[#222] object-cover"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold">{r.title}</div>
                  <div className="truncate text-sm text-muted">
                    {r.artist}
                    {r.year ? ` · ${r.year}` : ""}
                    {r.tracks ? ` · ${r.tracks} trks` : ""}
                  </div>
                </div>
                <button
                  onClick={() => addAlbum(r.release_mbid)}
                  disabled={addingId === r.release_mbid || owned}
                  className="flex items-center gap-2 rounded-lg px-4 py-2 font-semibold text-[#181400] disabled:opacity-60"
                  style={{ background: owned ? "#2f5128" : "var(--accent)" }}
                >
                  {addingId === r.release_mbid && <span className="spinner h-4 w-4" />}
                  {owned ? "Added ✓" : addingId === r.release_mbid ? "Adding…" : "Add"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="mb-3 mt-7 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm uppercase tracking-[0.08em] text-muted">
          Your records ({albums.length})
        </h2>
        {albums.length > 0 && (
          <div className="flex items-center gap-2">
            <input
              className="w-40 rounded-lg border border-[#2a2a33] bg-panel px-3 py-1.5 text-sm"
              placeholder="Filter…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="rounded-lg border border-[#2a2a33] bg-panel px-2 py-1.5 text-sm text-fg"
            >
              <option value="recent">Recently added</option>
              <option value="artist">Artist A–Z</option>
              <option value="title">Title A–Z</option>
              <option value="enrolled">Enrolled first</option>
            </select>
          </div>
        )}
      </div>

      {shown.length === 0 && albums.length > 0 && (
        <p className="text-muted">No records match “{filter}”.</p>
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        {shown.map((a) => (
          <AlbumCard
            key={a.id}
            album={a}
            canRecord={canRecord}
            busySide={target && target.albumId === a.id ? target.side : null}
            onRecord={onRecord}
            onOpen={setSelectedId}
          />
        ))}
      </div>

      {selected && (
        <AlbumDetail
          album={selected}
          canRecord={canRecord}
          busySide={target && target.albumId === selected.id ? target.side : null}
          onClose={() => setSelectedId(null)}
          onEdit={editAlbum}
          onDelete={deleteAlbum}
          onRecord={onRecord}
        />
      )}

      {target && (
        <div className="fixed inset-x-0 bottom-0 z-[80] flex items-center gap-3 border-t border-[#7a2630] bg-[#3a0f12] px-4 py-3">
          <div className="h-3 w-3 flex-none animate-pulse rounded-full bg-[#ff4d4d]" />
          <div className="flex-1 text-sm">
            Recording <b>side {target.side}</b> through the mic — play the side from the start.
          </div>
          <button
            onClick={stopRecord}
            className="rounded-lg px-4 py-2 font-semibold text-[#181400]"
            style={{ background: "var(--accent)" }}
          >
            Stop &amp; save
          </button>
          <button
            onClick={cancelRecord}
            className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-fg"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
