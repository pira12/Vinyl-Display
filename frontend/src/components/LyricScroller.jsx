import { useEffect, useMemo, useRef, useState } from "react";

// Spotify-style synced lyrics. Highlights the active line and scrolls it to a
// fixed anchor. Plain (unsynced) lyrics render statically.
export default function LyricScroller({ lyrics, position }) {
  const containerRef = useRef(null);
  const trackRef = useRef(null);
  const lineRefs = useRef([]);
  const [activeIdx, setActiveIdx] = useState(-1);

  const lines = (lyrics && lyrics.lines) || [];
  const synced = !!(lyrics && lyrics.synced);

  // Reset refs when the line set changes.
  useEffect(() => {
    lineRefs.current = [];
    setActiveIdx(-1);
  }, [lines]);

  const idx = useMemo(() => {
    if (!synced) return -1;
    let found = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].t != null && lines[i].t <= position) found = i;
      else break;
    }
    return found;
  }, [synced, lines, position]);

  useEffect(() => {
    if (idx === activeIdx) return;
    setActiveIdx(idx);
    const el = lineRefs.current[idx];
    const container = containerRef.current;
    const track = trackRef.current;
    if (el && container && track) {
      const anchor = container.clientHeight * 0.42;
      track.style.transform = `translateY(${-(el.offsetTop - anchor + el.clientHeight / 2)}px)`;
    }
  }, [idx, activeIdx]);

  if (!lines.length) {
    return (
      <div className="flex h-full items-center justify-center text-xl text-muted">
        No lyrics available
      </div>
    );
  }

  return (
    <div ref={containerRef} className="lyrics-mask relative h-full overflow-hidden">
      <div ref={trackRef} className="lyrics-track flex flex-col gap-5">
        {lines.map((line, i) => (
          <div
            key={i}
            ref={(el) => (lineRefs.current[i] = el)}
            className={
              "lyric-line" +
              (!synced ? " active" : i === activeIdx ? " active" : i < activeIdx ? " passed" : "")
            }
            style={!synced ? { fontSize: "1.6rem" } : undefined}
          >
            {line.text || " "}
          </div>
        ))}
      </div>
    </div>
  );
}
