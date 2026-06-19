import { useEffect, useRef, useState } from "react";
import { currentPosition } from "../hooks/useNowPlaying.js";
import LyricScroller from "./LyricScroller.jsx";

function fmt(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function DisplayView({ state, mic }) {
  const [pos, setPos] = useState(0);
  const raf = useRef(null);

  useEffect(() => {
    const tick = () => {
      setPos(currentPosition(state));
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [state]);

  const playing = state && state.status === "playing" && state.track;
  if (!playing) {
    const active = mic && mic.micActive;
    const identifying = mic && mic.identifying;
    const text = identifying
      ? "Identifying record…"
      : active
        ? "Listening for a record…"
        : "Microphone off";
    const sub = active
      ? "Keep the iPad near the speaker"
      : "Open Collection and tap Start to listen";
    return (
      <div className="fixed inset-0 z-[3] flex items-center justify-center bg-bg">
        <div className="text-center text-muted">
          <div className="disc mx-auto h-40 w-40" />
          <div className="mt-8 flex items-center justify-center gap-3">
            <span className="relative flex h-3 w-3">
              {active && (
                <span
                  className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-70"
                  style={{ background: "var(--accent)" }}
                />
              )}
              <span
                className="relative inline-flex h-3 w-3 rounded-full"
                style={{ background: active ? "var(--accent)" : "#555" }}
              />
            </span>
            <p className="text-2xl tracking-wide">{text}</p>
          </div>
          <p className="mt-2 text-sm">{sub}</p>
          {active && (
            <div className="mx-auto mt-5 h-1 w-40 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full transition-[width] duration-150"
                style={{ width: `${Math.round((mic.level || 0) * 100)}%`, background: "var(--accent)" }}
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  const t = state.track || {};
  const a = state.album || {};
  const dur = t.duration_ms || 0;
  const pct = dur ? Math.min(100, (pos / dur) * 100) : 0;

  return (
    <main className="grid h-screen grid-cols-1 portrait:grid-rows-[auto_1fr] landscape:grid-cols-[42%_58%]">
      <section className="flex flex-col justify-center gap-[3vh] px-[4vw] py-[5vh]">
        <div className="flex aspect-square max-h-[46vh] w-full items-center justify-center">
          {a.art_url ? (
            <img
              src={a.art_url}
              alt=""
              className="h-full w-full rounded-[10px] object-cover shadow-[0_20px_60px_rgba(0,0,0,0.6)]"
            />
          ) : (
            <div className="h-full w-full rounded-[10px] bg-[#181818]" />
          )}
        </div>
        <div>
          <h1 className="text-[2.6rem] leading-[1.1]">{t.title || "—"}</h1>
          <h2 className="mt-1 text-2xl font-medium" style={{ color: "var(--accent)" }}>
            {t.artist || a.artist || "—"}
          </h2>
          <p className="mt-2 text-muted">
            {[a.title, a.year].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
            <div
              className="h-full rounded-full"
              style={{ width: `${pct}%`, background: "var(--accent)" }}
            />
          </div>
          <div className="mt-2 flex justify-between text-muted tabular">
            <span>{fmt(pos)}</span>
            <span>{fmt(dur)}</span>
          </div>
        </div>
        {state.next_track && state.next_track.title && (
          <div className="flex items-baseline gap-3 border-t border-white/5 pt-[1.5vh]">
            <span className="text-xs uppercase tracking-[0.12em] text-muted">Up next</span>
            <span className="text-lg">{state.next_track.title}</span>
          </div>
        )}
      </section>
      <section className="relative overflow-hidden px-[4vw] py-[5vh]">
        <LyricScroller lyrics={state.lyrics} position={pos} />
      </section>
    </main>
  );
}
