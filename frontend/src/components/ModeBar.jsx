import { useEffect, useRef, useState } from "react";

// Centered pill switch. In display mode it auto-hides after inactivity.
export default function ModeBar({ mode, setMode, hideInDisplay }) {
  const [hidden, setHidden] = useState(false);
  const timer = useRef(null);

  useEffect(() => {
    if (!hideInDisplay) {
      setHidden(false);
      return;
    }
    const show = () => {
      setHidden(false);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setHidden(true), 4000);
    };
    show();
    const evs = ["pointerdown", "mousemove", "keydown"];
    evs.forEach((e) => document.addEventListener(e, show, { passive: true }));
    return () => {
      clearTimeout(timer.current);
      evs.forEach((e) => document.removeEventListener(e, show));
    };
  }, [hideInDisplay]);

  const btn = (target, label) => (
    <button
      onClick={() => setMode(target)}
      className={
        "rounded-full px-4 py-2 text-sm font-semibold transition-colors " +
        (mode === target ? "text-[#181400]" : "text-fg")
      }
      style={mode === target ? { background: "var(--accent)" } : undefined}
    >
      {label}
    </button>
  );

  return (
    <nav
      className={
        "fixed left-1/2 z-50 flex -translate-x-1/2 gap-1 rounded-full bg-black/45 p-1 backdrop-blur transition-opacity " +
        (hidden ? "pointer-events-none opacity-0" : "opacity-100")
      }
      style={{ top: "max(12px, env(safe-area-inset-top))" }}
    >
      {btn("display", "Display")}
      {btn("collection", "Collection")}
    </nav>
  );
}
