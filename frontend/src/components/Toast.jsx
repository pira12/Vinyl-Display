import { useEffect, useRef } from "react";

export default function Toast({ message, onClear }) {
  // Call the latest onClear without keying the timer on its identity, so a
  // parent re-render (frequent during playback) can't keep resetting the timer
  // and pin the banner open.
  const clearRef = useRef(onClear);
  clearRef.current = onClear;

  useEffect(() => {
    if (!message) return;
    const t = setTimeout(() => clearRef.current(), 3500);
    return () => clearTimeout(t);
  }, [message]);

  if (!message) return null;
  return (
    <div className="fixed inset-x-4 bottom-4 z-[70] rounded-xl border border-[#2f5128] bg-[#1d2a1a] p-3 text-center text-[#cfe9c2]">
      {message}
    </div>
  );
}
