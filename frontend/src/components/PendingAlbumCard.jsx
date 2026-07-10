// A placeholder shown in the collection grid while an album is being added and
// its tracklist/lyrics/art download on the server (can take a while). Uses the
// cover art already returned by search, dimmed under a spinner + shimmer, so the
// user can leave the search box and still see it's working in the background.
export default function PendingAlbumCard({ title, artist, year, art_url }) {
  return (
    <div className="flex flex-col" aria-busy="true">
      <div className="shimmer relative aspect-square w-full overflow-hidden rounded-md bg-[#1a1a20]">
        {art_url ? (
          <img
            src={art_url}
            alt=""
            className="h-full w-full object-cover opacity-40"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <div className="h-1/3 w-1/3 rounded-full bg-[radial-gradient(circle,#333_30%,#111_31%,#222_70%,#000_71%)]" />
          </div>
        )}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/45">
          <div className="spinner h-8 w-8" />
          <span className="text-xs font-medium text-white/80">Adding…</span>
        </div>
      </div>
      <div className="mt-2 min-w-0">
        <div className="truncate font-semibold">{title}</div>
        <div className="truncate text-sm text-muted">
          {artist}
          {year ? ` · ${year}` : ""} · downloading…
        </div>
      </div>
    </div>
  );
}
