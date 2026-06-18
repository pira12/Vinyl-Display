import { useCallback, useEffect, useRef, useState } from "react";
import { MicEngine } from "../audio/mic.js";
import { downsample, encodeWav16, floatToPcm16, TARGET_RATE } from "../audio/wav.js";
import { api, Unauthorized } from "../api.js";

const QUERY_SEC = 10;     // how much recent audio to send per recognition
const FAST_MS = 3000;     // cadence before a track locks
const SLOW_MS = 12000;    // cadence once locked (just drift correction)
const CHUNK_MS = 1000;    // enrollment upload cadence

// Single shared mic engine coordinating recognition and enrollment so they
// never open two mic streams at once.
export function useMic(onAuthError) {
  const engine = useRef(null);
  const recTimer = useRef(null);
  const chunkTimer = useRef(null);
  const micActiveRef = useRef(false);
  const enrollingRef = useRef(false);

  const [micActive, setMicActive] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [error, setError] = useState("");

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
    if (!eng || !eng.active) return false;
    const clip = eng.recent(QUERY_SEC);
    if (clip.length < eng.rate * 4) return false; // need a few seconds first
    const wav = encodeWav16(downsample(clip, eng.rate), TARGET_RATE);
    try {
      const res = await api.postBytes("/api/recognize", wav);
      return !!(res && res.matched);
    } catch (e) {
      handleErr(e);
      return false;
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
        }
        if (micActiveRef.current) scheduleRecognize(matched ? SLOW_MS : FAST_MS);
      }, delay);
    },
    [recognizeOnce]
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
    try {
      await api.postJson("/api/listen", { enabled: true });
    } catch (e) {
      handleErr(e);
    }
    scheduleRecognize(FAST_MS);
  }, [ensureEngine, handleErr, scheduleRecognize]);

  const stopListening = useCallback(async () => {
    micActiveRef.current = false;
    setMicActive(false);
    clearTimeout(recTimer.current);
    try {
      await api.postJson("/api/listen", { enabled: false });
    } catch {
      /* ignore */
    }
    if (!enrollingRef.current && engine.current) engine.current.stop();
  }, []);

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

  // Pause recognition uploads while backgrounded; resume on return.
  useEffect(() => {
    const onVis = () => {
      if (!document.hidden && micActiveRef.current) scheduleRecognize(FAST_MS);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      clearTimeout(recTimer.current);
      clearInterval(chunkTimer.current);
      if (engine.current) engine.current.stop();
    };
  }, [scheduleRecognize]);

  return {
    micActive,
    enrolling,
    error,
    clearError: () => setError(""),
    toggleListening,
    startEnroll,
    stopEnroll,
    cancelEnroll,
  };
}
