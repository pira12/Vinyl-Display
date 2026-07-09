import { useCallback, useEffect, useRef, useState } from "react";
import { MicEngine } from "../audio/mic.js";
import { downsample, encodeWav16, floatToPcm16, TARGET_RATE } from "../audio/wav.js";
import { currentPosition } from "./useNowPlaying.js";
import { api, Unauthorized } from "../api.js";

const QUERY_SEC = 10;     // how much recent audio to send per recognition
const FAST_MS = 3000;     // cadence before a track locks
const SLOW_MS = 12000;    // cadence once locked (just drift correction)
// After a track's expected end, the ring buffer still holds mostly the old
// song; wait this long past the boundary so the clip is fresh enough to match.
const BOUNDARY_LAG_MS = 5000;
// Consecutive request *errors* (server down, WiFi drop) back off instead of
// hammering; a plain "no match" keeps the fast cadence.
const ERROR_BACKOFF_MS = [3000, 6000, 12000, 30000];
const CHUNK_MS = 1000;    // enrollment upload cadence
// Bound the enrollment retry queue (~1s of PCM per entry). Past this the
// connection is truly gone and the side recording can't be saved intact.
const MAX_PENDING_CHUNKS = 300;
const IDENTIFY_SEC = 25;          // clip length for an AcoustID auto-label
const IDENTIFY_AFTER_MISSES = 4;  // consecutive misses before trying AcoustID
const IDENTIFY_COOLDOWN_MS = 60000; // don't hammer the free AcoustID quota

// Single shared mic engine coordinating recognition and enrollment so they
// never open two mic streams at once. `getState` returns the latest
// now-playing state so the scheduler can wake up right after a track ends.
export function useMic(onAuthError, getState) {
  const engine = useRef(null);
  const recTimer = useRef(null);
  const chunkTimer = useRef(null);
  const micActiveRef = useRef(false);
  const enrollingRef = useRef(false);
  const wakeLock = useRef(null);

  const acquireWakeLock = useCallback(async () => {
    try {
      if ("wakeLock" in navigator && !wakeLock.current) {
        wakeLock.current = await navigator.wakeLock.request("screen");
        wakeLock.current.addEventListener?.("release", () => {
          wakeLock.current = null;
        });
      }
    } catch {
      /* wake lock unsupported or denied — non-fatal */
    }
  }, []);

  const releaseWakeLock = useCallback(() => {
    try {
      wakeLock.current && wakeLock.current.release();
    } catch {
      /* ignore */
    }
    wakeLock.current = null;
  }, []);

  const missCount = useRef(0);
  const errorStreak = useRef(0);
  const lastIdentifyAt = useRef(0);
  const identifyAvailable = useRef(true); // false once the server says so
  const pendingChunks = useRef([]);       // enrollment PCM awaiting upload

  const [micActive, setMicActive] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [identifying, setIdentifying] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState("");
  const [lastIdentified, setLastIdentified] = useState(null);
  const [debug, setDebug] = useState({ sent: 0, last: "idle" });

  const ensureEngine = useCallback(async () => {
    if (!engine.current) engine.current = new MicEngine();
    if (!engine.current.active) await engine.current.start();
  }, []);

  const handleErr = useCallback(
    (e) => {
      if (e instanceof Unauthorized) onAuthError && onAuthError();
      else setError(e.message || String(e));
    },
    [onAuthError]
  );

  // --- recognition loop ---
  // Returns "match" | "miss" | "error" so the scheduler can pick the cadence.
  const recognizeOnce = useCallback(async () => {
    const eng = engine.current;
    if (!eng || !eng.active) {
      setDebug((d) => ({ ...d, last: "no engine" }));
      return "miss";
    }
    await eng.resume(); // re-arm if iOS suspended the context
    const clip = eng.recent(QUERY_SEC);
    if (clip.length < eng.rate * 4) {
      setDebug((d) => ({ ...d, last: "buffering" }));
      return "miss"; // need a few seconds first
    }
    const wav = encodeWav16(downsample(clip, eng.rate), TARGET_RATE);
    try {
      const res = await api.postBytes("/api/recognize", wav);
      const matched = !!(res && res.matched);
      errorStreak.current = 0;
      setDebug((d) => ({ sent: d.sent + 1, last: matched ? "match" : "no match" }));
      return matched ? "match" : "miss";
    } catch (e) {
      errorStreak.current += 1;
      setDebug((d) => ({ sent: d.sent + 1, last: "error" }));
      handleErr(e);
      return "error";
    }
  }, [handleErr]);

  // Best-effort auto-label via AcoustID after recognition keeps missing.
  // Rate-limited by a cooldown, and disabled for the whole session once the
  // server reports the feature isn't configured (no key / no fpcalc).
  const identifyOnce = useCallback(async () => {
    const eng = engine.current;
    if (!eng || !eng.active || !identifyAvailable.current) return;
    const now = Date.now();
    if (now - lastIdentifyAt.current < IDENTIFY_COOLDOWN_MS) return;
    lastIdentifyAt.current = now;
    const clip = eng.recent(IDENTIFY_SEC);
    if (clip.length < eng.rate * 12) return; // need a longer clip to identify
    const wav = encodeWav16(downsample(clip, eng.rate), TARGET_RATE);
    setIdentifying(true);
    try {
      const res = await api.postBytes("/api/identify", wav);
      if (res && res.available === false) identifyAvailable.current = false;
      if (res && res.album) setLastIdentified(res.album);
    } catch (e) {
      handleErr(e);
    } finally {
      setIdentifying(false);
    }
  }, [handleErr]);

  // When to run the next recognition:
  // - request errors: exponential backoff, so a down server isn't hammered
  // - not locked on a track: fast, to catch the needle dropping
  // - locked: cruise slowly (drift correction only), but wake up just after
  //   the track is due to end so "up next" flips within seconds
  const nextDelay = useCallback(
    (outcome) => {
      if (outcome === "error") {
        const i = Math.min(errorStreak.current, ERROR_BACKOFF_MS.length) - 1;
        return ERROR_BACKOFF_MS[Math.max(0, i)];
      }
      if (outcome !== "match") return FAST_MS;
      const s = getState && getState();
      if (s && s.status === "playing" && s.track && s.track.duration_ms) {
        const remaining = s.track.duration_ms - currentPosition(s);
        return Math.max(2000, Math.min(SLOW_MS, remaining + BOUNDARY_LAG_MS));
      }
      return SLOW_MS;
    },
    [getState]
  );

  const scheduleRecognize = useCallback(
    (delay) => {
      clearTimeout(recTimer.current);
      recTimer.current = setTimeout(async () => {
        if (!micActiveRef.current) return;
        let outcome = "miss";
        if (!enrollingRef.current && !document.hidden) {
          outcome = await recognizeOnce();
          if (outcome === "match") {
            missCount.current = 0;
          } else if (outcome === "miss" &&
                     ++missCount.current >= IDENTIFY_AFTER_MISSES) {
            identifyOnce(); // fire and forget; cooldown guards the quota
          }
        }
        if (micActiveRef.current) scheduleRecognize(nextDelay(outcome));
      }, delay);
    },
    [recognizeOnce, identifyOnce, nextDelay]
  );

  const startListening = useCallback(async () => {
    setError("");
    try {
      await ensureEngine();
    } catch (e) {
      handleErr(e);
      return;
    }
    micActiveRef.current = true;
    setMicActive(true);
    acquireWakeLock();
    try {
      await api.postJson("/api/listen", { enabled: true });
    } catch (e) {
      handleErr(e);
    }
    scheduleRecognize(FAST_MS);
  }, [ensureEngine, handleErr, scheduleRecognize, acquireWakeLock]);

  const stopListening = useCallback(async () => {
    micActiveRef.current = false;
    setMicActive(false);
    clearTimeout(recTimer.current);
    releaseWakeLock();
    try {
      await api.postJson("/api/listen", { enabled: false });
    } catch {
      /* ignore */
    }
    if (!enrollingRef.current && engine.current) engine.current.stop();
  }, [releaseWakeLock]);

  const toggleListening = useCallback(() => {
    if (micActiveRef.current) stopListening();
    else startListening();
  }, [startListening, stopListening]);

  // --- enrollment ---
  // Chunks that fail to upload (a WiFi blip mid-side) are queued and retried
  // on the next tick instead of dropped — a lost chunk would leave a silent
  // gap in the fingerprint and shift every track offset after it.
  const flushChunk = useCallback(async (final = false) => {
    const eng = engine.current;
    if (!eng) return;
    const frames = eng.drain();
    if (frames.length) {
      const pcm = floatToPcm16(downsample(frames, eng.rate));
      pendingChunks.current.push(pcm.buffer);
    }
    while (pendingChunks.current.length) {
      try {
        await api.postBytes("/api/record/chunk", pendingChunks.current[0]);
        pendingChunks.current.shift();
      } catch (e) {
        if (final) throw e; // stop-and-save must not silently truncate
        if (pendingChunks.current.length >= MAX_PENDING_CHUNKS) {
          pendingChunks.current = [];
          handleErr(new Error("Recording uploads keep failing — check the connection and re-record this side."));
        }
        return; // keep the queue; retry on the next tick
      }
    }
  }, [handleErr]);

  const startEnroll = useCallback(
    async (albumId, side) => {
      setError("");
      try {
        await ensureEngine();
        await api.postJson("/api/record/start", { album_id: albumId, side });
      } catch (e) {
        handleErr(e);
        return false;
      }
      enrollingRef.current = true;
      setEnrolling(true);
      pendingChunks.current = [];
      engine.current.startAccum();
      clearInterval(chunkTimer.current);
      chunkTimer.current = setInterval(() => flushChunk(false), CHUNK_MS);
      return true;
    },
    [ensureEngine, flushChunk, handleErr]
  );

  const finishEnroll = useCallback(
    async (cancel) => {
      clearInterval(chunkTimer.current);
      enrollingRef.current = false;
      setEnrolling(false);
      const eng = engine.current;
      try {
        if (cancel) {
          pendingChunks.current = [];
          await api.postJson("/api/record/cancel", {});
        } else {
          await flushChunk(true);
          var result = await api.postJson("/api/record/stop", {});
        }
      } catch (e) {
        handleErr(e);
      } finally {
        pendingChunks.current = [];
        if (eng) eng.stopAccum();
        // If the user wasn't actively listening, release the mic.
        if (!micActiveRef.current && eng) eng.stop();
      }
      return cancel ? null : result && result.result;
    },
    [flushChunk, handleErr]
  );

  const stopEnroll = useCallback(() => finishEnroll(false), [finishEnroll]);
  const cancelEnroll = useCallback(() => finishEnroll(true), [finishEnroll]);

  // Poll the input level for the meter while the mic is active.
  useEffect(() => {
    if (!micActive) {
      setLevel(0);
      return;
    }
    const id = setInterval(() => {
      setLevel(engine.current ? engine.current.level() : 0);
    }, 150);
    return () => clearInterval(id);
  }, [micActive]);

  // Keep a ref to the latest scheduler so the mount-only effect below can call
  // it without re-subscribing (which would tear down the engine on re-render).
  const scheduleRef = useRef(scheduleRecognize);
  scheduleRef.current = scheduleRecognize;

  // Mount/unmount ONLY. Previously this depended on callbacks whose identity
  // changed every render (the level meter re-renders constantly), so the
  // cleanup ran repeatedly and stopped the mic right after it started.
  useEffect(() => {
    const onVis = async () => {
      if (!document.hidden && micActiveRef.current) {
        await acquireWakeLock(); // wake locks drop when backgrounded
        if (engine.current) await engine.current.resume();
        scheduleRef.current(FAST_MS);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      clearTimeout(recTimer.current);
      clearInterval(chunkTimer.current);
      releaseWakeLock();
      if (engine.current) engine.current.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    micActive,
    enrolling,
    identifying,
    level,
    debug,
    error,
    lastIdentified,
    clearError: () => setError(""),
    toggleListening,
    startEnroll,
    stopEnroll,
    cancelEnroll,
  };
}
