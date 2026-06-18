// One album as a square vinyl cover with title/info below and per-side record
// buttons. Shows a spinner overlay while busy (enrolling a side).
export default function AlbumCard({ album, canRecord, busySide, onRecord }) {
  const sides = sidesOf(album);
  return (
    <div className="flex flex-col">
      <div className="relative aspect-square w-full overflow-hidden rounded-xl bg-[#222]">
        {album.art_url ? (
          <img src={album.art_url} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-muted">♪</div>
        )}
        {busySide && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60">
            <div className="spinner h-8 w-8" />
          </div>
        )}
      </div>
      <div className="mt-2 min-w-0">
        <div className="truncate font-semibold">{album.title}</div>
        <div className="truncate text-sm text-muted">
          {album.artist}
          {album.year ? ` · ${album.year}` : ""} · {album.track_count} tracks
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sides.map((side) => {
            const done = (album.enrolled_sides || []).includes(side);
            return (
              <button
                key={side}
                disabled={!canRecord || !!busySide}
                onClick={() => onRecord(album.id, side)}
                className={
                  "rounded-lg px-2.5 py-1.5 text-xs font-semibold disabled:opacity-40 " +
                  (done
                    ? "border border-[#2f5128] bg-[#22351f] text-[#9fd18a]"
                    : "border border-[#2a2a33] bg-panel text-fg")
                }
              >
                {done ? `Side ${side} ✓` : `Record side ${side}`}
              </button>
            );
          })}
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
  return set.length ? set : ["A"];
}
