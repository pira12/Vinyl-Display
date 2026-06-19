import { useState } from "react";

// Full-screen overlay for one album: edit metadata, see the tracklist, re-record
// or learn each side, and delete the record. The app is mode-based (no router),
// so this is a panel layered over the collection rather than a route.
export default function AlbumDetail({
  album,
  canRecord,
  busySide,
  onClose,
  onEdit,
  onDelete,
  onRecord,
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    title: album.title || "",
    artist: album.artist || "",
    year: album.year || "",
  });
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const sides = sidesOf(album);

  async function save() {
    setSaving(true);
    const ok = await onEdit(album.id, {
      title: form.title.trim(),
      artist: form.artist.trim(),
      year: form.year.trim(),
    });
    setSaving(false);
    if (ok) setEditing(false);
  }

  return (
    <div className="fixed inset-0 z-[70] overflow-y-auto bg-black/80 backdrop-blur-sm">
      <div className="mx-auto max-w-[720px] px-4 pb-24 pt-6">
        <div className="mb-4 flex items-center justify-between">
          <button
            onClick={onClose}
            className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-sm font-semibold text-fg"
          >
            ← Back
          </button>
          {!editing && (
            <button
              onClick={() => setEditing(true)}
              className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-sm font-semibold text-fg"
            >
              Edit
            </button>
          )}
        </div>

        <div className="flex flex-col gap-4 sm:flex-row">
          <div className="mx-auto aspect-square w-44 flex-none overflow-hidden rounded-lg bg-[#1a1a20] sm:mx-0">
            {album.art_url ? (
              <img src={album.art_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center">
                <div className="h-1/3 w-1/3 rounded-full bg-[radial-gradient(circle,#333_30%,#111_31%,#222_70%,#000_71%)]" />
              </div>
            )}
          </div>

          <div className="min-w-0 flex-1">
            {editing ? (
              <div className="space-y-2">
                {["title", "artist", "year"].map((f) => (
                  <input
                    key={f}
                    className="w-full rounded-lg border border-[#2a2a33] bg-panel p-2.5 text-fg"
                    placeholder={f}
                    value={form[f]}
                    onChange={(e) => setForm((s) => ({ ...s, [f]: e.target.value }))}
                  />
                ))}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={save}
                    disabled={saving}
                    className="flex items-center gap-2 rounded-lg px-4 py-2 font-semibold text-[#181400] disabled:opacity-60"
                    style={{ background: "var(--accent)" }}
                  >
                    {saving && <span className="spinner h-4 w-4" />}
                    {saving ? "Saving…" : "Save"}
                  </button>
                  <button
                    onClick={() => {
                      setEditing(false);
                      setForm({ title: album.title, artist: album.artist, year: album.year });
                    }}
                    className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-fg"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <h1 className="text-2xl font-bold tracking-tight">{album.title}</h1>
                <p className="mt-1 text-muted">
                  {album.artist}
                  {album.year ? ` · ${album.year}` : ""} · {album.track_count} tracks
                </p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {sides.map((side) => {
                    const done = (album.enrolled_sides || []).includes(side);
                    return (
                      <button
                        key={side}
                        disabled={!canRecord || !!busySide}
                        onClick={() => onRecord(album.id, side)}
                        title={done ? `Re-record side ${side}` : `Learn side ${side}`}
                        className={
                          "inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium disabled:opacity-40 " +
                          (done
                            ? "border border-[#2f5128] bg-[#1c2a18] text-[#9fd18a]"
                            : "border border-[#2a2a33] bg-panel text-fg")
                        }
                      >
                        {done ? `Side ${side} · re-record` : `Learn side ${side}`}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>

        <h2 className="mb-2 mt-6 text-sm uppercase tracking-[0.08em] text-muted">
          Tracklist
        </h2>
        <ol className="overflow-hidden rounded-xl border border-[#2a2a33]">
          {(album.tracklist || []).map((t, i) => (
            <li
              key={i}
              className="flex items-center gap-3 border-b border-[#22222a] px-3 py-2 text-sm last:border-b-0"
            >
              <span className="w-8 flex-none text-muted">{t.position || i + 1}</span>
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              {t.has_lyrics && (
                <span className="flex-none text-xs text-muted" title="Lyrics cached">
                  lyrics
                </span>
              )}
            </li>
          ))}
        </ol>

        <div className="mt-8 border-t border-[#2a2a33] pt-4">
          {confirmDelete ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">Delete this record and its fingerprints?</span>
              <button
                onClick={() => onDelete(album.id)}
                className="rounded-lg bg-[#7a2630] px-4 py-2 text-sm font-semibold text-white"
              >
                Delete
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="rounded-lg border border-[#2a2a33] bg-panel px-3 py-2 text-sm text-fg"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="rounded-lg border border-[#5a2026] bg-[#2a1416] px-4 py-2 text-sm font-semibold text-[#e88]"
            >
              Delete record
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function sidesOf(a) {
  const set = [];
  for (const t of a.tracklist || []) {
    const p = (t.position || "").trim();
    if (p && /[A-Za-z]/.test(p[0]) && !set.includes(p[0].toUpperCase())) {
      set.push(p[0].toUpperCase());
    }
  }
  return set.length ? set : ["A", "B"];
}
