import { BUILD } from "../build.js";

// Prominent, unambiguous microphone state: off / listening / identifying /
// recognized, with a live input-level meter so you can see the mic is hearing
// the music.
export default function MicStatus({ mic, state }) {
  const playing = state && state.status === "playing" && state.track;
  let phase, label, sub, dot;
  if (!mic.micActive) {
    phase = "off";
    label = "Microphone off";
    sub = "Tap Start to listen for a record";
    dot = "bg-muted";
  } else if (playing) {
    phase = "playing";
    label = "Recognized";
    sub = `${state.track.title} — ${state.track.artist || ""}`;
    dot = "bg-[#5fd06e]";
  } else if (mic.identifying) {
    phase = "identifying";
    label = "Identifying record…";
    sub = "Checking what's playing";
    dot = "bg-[#e8c37a]";
  } else {
    phase = "listening";
    label = "Listening…";
    sub = "Searching for a record — keep the iPad near the speaker";
    dot = "bg-[var(--accent)]";
  }

  return (
    <div className="mb-5 flex items-center gap-3 rounded-xl border border-[#2a2a33] bg-panel p-3.5">
      <span className="relative flex h-3 w-3 flex-none">
        {(phase === "listening" || phase === "identifying") && (
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${dot} opacity-60`} />
        )}
        <span className={`relative inline-flex h-3 w-3 rounded-full ${dot}`} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">{label}</div>
        <div className="truncate text-xs text-muted">{sub}</div>
        {mic.micActive && !playing && (
          <div className="mt-2 flex h-8 items-end gap-1" aria-label="listening level">
            {Array.from({ length: 18 }).map((_, i) => {
              const lvl = mic.level || 0;
              const center = 1 - Math.abs(i - 8.5) / 9; // peak in the middle
              const jitter = 0.55 + Math.random() * 0.45;
              const h = Math.max(2, Math.min(100, lvl * 130 * (0.45 + center) * jitter));
              return <span key={i} className="eq-bar" style={{ height: `${h}%` }} />;
            })}
          </div>
        )}
        <div className="mt-2 font-mono text-[10px] text-muted/70">
          {BUILD} · lvl {(mic.level || 0).toFixed(2)} · {mic.debug ? mic.debug.sent : 0} sent ·{" "}
          {mic.debug ? mic.debug.last : "-"}
        </div>
      </div>
      <button
        onClick={mic.toggleListening}
        className={
          "flex-none rounded-lg border px-3.5 py-2 text-sm font-semibold " +
          (mic.micActive ? "border-[#2a2a33] bg-bg text-fg" : "border-transparent text-[#181400]")
        }
        style={mic.micActive ? undefined : { background: "var(--accent)" }}
      >
        {mic.micActive ? "Stop" : "Start"}
      </button>
    </div>
  );
}
