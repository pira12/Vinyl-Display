import { useEffect, useState } from "react";

// First-run welcome shown once (localStorage flag lives in App). Walks a new
// self-hoster through the only things they need: the access token (only when the
// server has auth on), granting the microphone, and adding a first record. All
// generic — no personal URLs — so anyone can stand up their own instance.
export default function Onboarding({ onDone, onToken }) {
  const [authRequired, setAuthRequired] = useState(null); // null until known
  const [step, setStep] = useState(0);
  const [token, setToken] = useState("");
  const [mic, setMic] = useState("idle"); // idle | granted | denied

  useEffect(() => {
    fetch("/healthz")
      .then((r) => r.json())
      .then((d) => setAuthRequired(!!d.auth_required))
      .catch(() => setAuthRequired(false));
  }, []);

  // Wait until we know whether the token step is needed before rendering steps.
  if (authRequired === null) return null;

  const steps = ["welcome", ...(authRequired ? ["token"] : []), "mic", "done"];
  const kind = steps[step];
  const last = step === steps.length - 1;
  const next = () => setStep((s) => Math.min(s + 1, steps.length - 1));

  async function enableMic() {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop()); // just a permission prompt
      setMic("granted");
    } catch {
      setMic("denied");
    }
  }

  function finish() {
    if (token.trim() && onToken) onToken(token.trim());
    onDone();
  }

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4 backdrop-blur-lg">
      <div className="glass-card w-full max-w-md p-7">
        <div className="mb-5 flex items-center gap-2">
          <div className="disc h-8 w-8" />
          <span className="text-sm font-semibold uppercase tracking-[0.14em] text-muted">
            Vinyl Display
          </span>
        </div>

        {kind === "welcome" && (
          <>
            <h1 className="text-2xl font-bold tracking-tight">
              Your records, on any screen
            </h1>
            <p className="mt-3 text-muted">
              Put a record on and this shows what’s playing — now playing, up
              next, album art, and lyrics that scroll in time. Recognition is
              automatic, like Shazam. Let’s get you set up.
            </p>
          </>
        )}

        {kind === "token" && (
          <>
            <h1 className="text-2xl font-bold tracking-tight">Enter your access token</h1>
            <p className="mt-3 text-muted">
              This instance has authentication on. Find the{" "}
              <b>?token=…</b> link your server printed on start-up (run{" "}
              <code className="rounded bg-white/10 px-1">docker logs</code> on the
              host), and paste the token here.
            </p>
            <input
              className="glass-input mt-4"
              placeholder="access token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </>
        )}

        {kind === "mic" && (
          <>
            <h1 className="text-2xl font-bold tracking-tight">Allow the microphone</h1>
            <p className="mt-3 text-muted">
              The device near your speakers listens for the music. Tap below and
              allow access when your browser asks. (On iPhone/iPad this needs
              HTTPS and a tap — that’s this button.)
            </p>
            <button
              onClick={enableMic}
              className={"btn mt-4 " + (mic === "granted" ? "btn-ghost" : "btn-accent")}
            >
              {mic === "granted" ? "Microphone ready ✓" : "Enable microphone"}
            </button>
            {mic === "denied" && (
              <p className="mt-2 text-sm text-[#e88]">
                Permission was blocked — you can grant it later from your
                browser’s site settings.
              </p>
            )}
          </>
        )}

        {kind === "done" && (
          <>
            <h1 className="text-2xl font-bold tracking-tight">Add your first record</h1>
            <p className="mt-3 text-muted">
              Search an album or artist and tap Add. Its tracklist, art, and
              synced lyrics get cached so the display lights up the moment you
              play it. That’s it — enjoy.
            </p>
          </>
        )}

        <div className="mt-6 flex items-center justify-between">
          <div className="flex gap-1.5">
            {steps.map((_, i) => (
              <span
                key={i}
                className={
                  "h-1.5 w-1.5 rounded-full " +
                  (i === step ? "bg-[var(--accent)]" : "bg-white/20")
                }
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            {!last && (
              <button onClick={onDone} className="btn text-muted hover:text-fg">
                Skip
              </button>
            )}
            <button
              onClick={last ? finish : next}
              disabled={kind === "token" && !token.trim()}
              className="btn btn-accent"
            >
              {last ? "Start adding records" : "Continue"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
