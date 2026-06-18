import { useEffect, useRef, useState } from "react";

// Subscribes to the server's /ws and returns the latest now-playing state,
// auto-reconnecting on drop. The payload shape matches StateManager.payload().
export function useNowPlaying() {
  const [state, setState] = useState(null);
  const wsRef = useRef(null);

  useEffect(() => {
    let closed = false;
    let retry;

    function connect() {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          setState(JSON.parse(ev.data));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  return state;
}

// Smoothly advance the play clock between server updates. Returns current
// position in ms, clamped to the track duration.
export function currentPosition(state) {
  if (!state || state.status !== "playing") return 0;
  const drift = (Date.now() - state.updated_at) * (state.speed_factor || 1);
  let pos = (state.position_ms || 0) + drift;
  const dur = state.track && state.track.duration_ms;
  if (dur) pos = Math.min(pos, dur);
  return Math.max(0, pos);
}
