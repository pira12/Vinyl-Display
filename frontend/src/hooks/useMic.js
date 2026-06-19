import { useCallback, useEffect, useRef, useState } from "react";
import { MicEngine } from "../audio/mic.js";
import { downsample, encodeWav16, floatToPcm16, TARGET_RATE } from "../audio/wav.js";
import { api, Unauthorized } from "../api.js";

const QUERY_SEC = 10;     // how much recent audio to send per recognition
const FAST_MS = 3000;     // cadence before a track locks
const SLOW_MS = 12000;    // cadence once locked (just drift correction)
const CHUNK_MS = 1000;    // enrollment upload cadence
const IDENTIFY_SEC = 25;          // clip length for an AcoustID auto-label
const IDENTIFY_AFTER_MISSES = 4;  // consecutive Olaf misses before trying AcoustID
const IDENTIFY_COOLDOWN_MS = 60000; // don't hammer the free AcoustID quota

// Single shared mic engine coordinating recognition and enrollment so they
// never open two mic streams at once.
export function useMic(onAuthError) {
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
  const lastIdentifyAt = useRef(0);

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
  const recognizeOnce = useCallback(async () => {
    const eng = engine.current;
    if (!eng || !eng.active) {
      setDebug((d) => ({ ...d, last: "no engine" }));
      return false;
    }
    await eng.resume(); // re-arm if iOS suspended the context
    const clip = eng.recent(QUERY_SEC);
    if (clip.length < eng.rate * 4) {
      setDebug((d) => ({ ...d, last: "buffering" }));
      return false; // need a few seconds first
    }
    const wav = encodeWav16(downsample(clip, eng.rate), TARGET_RATE);
    try {
      const res = await api.postBytes("/api/recognize", wav);
      const matched = !!(res && res.matched);
      setDebug((d) => ({ sent: d.sent + 1, last: matched ? "match" : "no match" }));
      return matched;
    } catch (e) {
      setDebug((d) => ({ sent: d.sent + 1, last: "error" }));
      handleErr(e);
      return false;
    }
  }, [handleErr]);

  // Best-effort auto-label via AcoustID after Olaf keeps missing. Rate-limited
  // by a cooldown so it stays within the free AcoustID quota.
  const identifyOnce = useCallback(async () => {
    const eng = engine.current;
    if (!eng || !eng.active) return;
    const now = Date.now();
    if (now - lastIdentifyAt.current < IDENTIFY_COOLDOWN_MS) return;
    lastIdentifyAt.current = now;
    const clip = eng.recent(IDENTIFY_SEC);
    if (clip.length < eng.rate * 12) return; // need a longer clip to identify
    const wav = encodeWav16(downsample(clip, eng.rate), TARGET_RATE);
    setIdentifying(true);
    try {
      const res = await api.postBytes("/api/identify", wav);
      if (res && res.album) setLastIdentified(res.album);
    } catch (e) {
      handleErr(e);
    } finally {
      setIdentifying(false);
    }
  }, [handleErr]);

  const scheduleRecognize = useCallback(
    (delay) => {
      clearTimeout(recTimer.current);
      recTimer.current = setTimeout(async () => {
        if (!micActiveRef.current) return;
        let matched = false;
        if (!enrollingRef.current && !document.hidden) {
          matched = await recognizeOnce();
          if (matched) {
            missCount.current = 0;
          } else if (++missCount.current >= IDENTIFY_AFTER_MISSES) {
            identifyOnce(); // fire and forget; cooldown guards the quota
          }
        }
        if (micActiveRef.current) scheduleRecognize(matched ? SLOW_MS : FAST_MS);
      }, delay);
    },
    [recognizeOnce, identifyOnce]
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
  const flushChunk = useCallback((final = false) => {
    const eng = engine.current;
    if (!eng) return Promise.resolve();
    const frames = eng.drain();
    if (!frames.length) return Promise.resolve();
    const pcm = floatToPcm16(downsample(frames, eng.rate));
    return api.postBytes("/api/record/chunk", pcm.buffer).catch((e) => {
      if (!final) handleErr(e);
    });
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
          await api.postJson("/api/record/cancel", {});
        } else {
          await flushChunk(true);
          var result = await api.postJson("/api/record/stop", {});
        }
      } catch (e) {
        handleErr(e);
      } finally {
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

  // Pause recognition uploads while backgrounded; resume on return.
  useEffect(() => {
    const onVis = async () => {
      if (!document.hidden && micActiveRef.current) {
        await acquireWakeLock(); // wake locks drop when backgrounded
        if (engine.current) await engine.current.resume();
        scheduleRecognize(FAST_MS);
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
  }, [scheduleRecognize, acquireWakeLock, releaseWakeLock]);

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
