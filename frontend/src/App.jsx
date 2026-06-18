import { useEffect, useState } from "react";
import { loadToken } from "./api.js";
import { useNowPlaying } from "./hooks/useNowPlaying.js";
import { useMic } from "./hooks/useMic.js";
import { applyAccentFromUrl } from "./accent.js";
import ModeBar from "./components/ModeBar.jsx";
import DisplayView from "./components/DisplayView.jsx";
import CollectionView from "./components/CollectionView.jsx";
import Toast from "./components/Toast.jsx";

function initialMode() {
  const p = new URLSearchParams(window.location.search).get("mode");
  if (p === "add" || p === "collection") return "collection";
  if (p === "display") return "display";
  if (window.location.pathname === "/manage") return "collection";
  return localStorage.getItem("vinyl_mode") || "display";
}

export default function App() {
  const [mode, setMode] = useState(initialMode);
  const [toast, setToast] = useState("");
  const [authNeeded, setAuthNeeded] = useState(false);
  const state = useNowPlaying();
  const mic = useMic(() => setAuthNeeded(true));

  useEffect(() => {
    loadToken();
  }, []);
  useEffect(() => {
    localStorage.setItem("vinyl_mode", mode);
  }, [mode]);

  const artUrl = state && state.album && state.album.art_url;
  useEffect(() => {
    applyAccentFromUrl(artUrl);
  }, [artUrl]);

  useEffect(() => {
    if (mic.error) setToast(mic.error);
  }, [mic.error]);

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div
        className={"backdrop" + (artUrl ? " show" : "")}
        style={artUrl ? { backgroundImage: `url("${artUrl}")` } : undefined}
      />
      <div
        className="pointer-events-none fixed inset-0 z-[1]"
        style={{
          background:
            "radial-gradient(ellipse at 30% 40%, rgba(11,11,15,0.35), rgba(11,11,15,0.85))",
        }}
      />
      <div className="relative z-[2]">
        <ModeBar mode={mode} setMode={setMode} hideInDisplay={mode === "display"} />
        {mode === "display" ? (
          <DisplayView state={state} />
        ) : (
          <CollectionView
            state={state}
            mic={mic}
            authNeeded={authNeeded}
            setAuthNeeded={setAuthNeeded}
            toast={setToast}
          />
        )}
      </div>
      <Toast message={toast} onClear={() => setToast("")} />
    </div>
  );
}
