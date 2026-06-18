import { useEffect } from "react";

export default function Toast({ message, onClear }) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onClear, 3500);
    return () => clearTimeout(t);
  }, [message, onClear]);

  if (!message) return null;
  return (
    <div className="fixed inset-x-4 bottom-4 z-[70] rounded-xl border border-[#2f5128] bg-[#1d2a1a] p-3 text-center text-[#cfe9c2]">
      {message}
    </div>
  );
}
