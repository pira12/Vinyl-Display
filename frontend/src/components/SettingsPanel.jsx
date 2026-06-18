import { useEffect, useState } from "react";
import { useSettings } from "../hooks/useSettings.js";

const NUMS = [
  ["audio.silence_rms", "Silence threshold (RMS)", { min: 0, max: 1, step: 0.005 }],
  ["playback.speed_factor", "Speed factor", { min: 0.8, max: 1.2, step: 0.001 }],
  ["recognition.interval_seconds", "Slow interval (s)", { min: 1, step: 1 }],
  ["recognition.fast_interval_seconds", "Fast interval (s)", { min: 0.5, step: 0.5 }],
  ["recognition.min_match_score", "Min match score", { min: 1, step: 1 }],
];

export default function SettingsPanel({ onToast }) {
  const { snapshot, load, save } = useSettings();
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (snapshot) setForm({ ...snapshot.values });
  }, [snapshot]);

  if (!snapshot || !form) {
    return (
      <div className="mb-5 rounded-2xl border border-[#2a2a33] bg-panel p-4 text-muted">
        Loading settings…
      </div>
    );
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const restart = new Set(snapshot.restart_fields || []);
  const fieldCls =
    "w-full rounded-lg border border-[#2a2a33] bg-panel p-2.5 text-fg";

  async function onSave() {
    const changes = {};
    for (const k of Object.keys(form)) {
      if (JSON.stringify(form[k]) !== JSON.stringify(snapshot.values[k])) {
        changes[k] = form[k];
      }
    }
    if (!Object.keys(changes).length) {
      onToast("No changes to save.");
      return;
    }
    setSaving(true);
    try {
      const res = await save(changes);
      onToast(
        res.restart_required && res.restart_required.length
          ? "Saved. Restart to apply: " + res.restart_required.join(", ")
          : "Saved and applied."
      );
    } catch (e) {
      onToast((e.fields ? Object.keys(e.fields).join(", ") + ": " : "") + e.message);
    } finally {
      setSaving(false);
    }
  }

  const deviceVal = form["audio.device"];
  return (
    <div className="mb-5 rounded-2xl border border-[#2a2a33] bg-panel p-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-sm text-muted">
          Audio device
          <select
            className={fieldCls}
            value={deviceVal === null || deviceVal === undefined ? "" : String(deviceVal)}
            onChange={(e) =>
              set("audio.device", e.target.value === "" ? null : numericOrString(e.target.value))
            }
          >
            <option value="">System default</option>
            {(snapshot.devices || []).map((d) => (
              <option key={d.index} value={String(d.index)}>
                {d.index}: {d.name}
              </option>
            ))}
          </select>
          {restart.has("audio.device") && (
            <span className="text-xs text-muted">Takes effect after a restart.</span>
          )}
        </label>

        <label className="flex flex-col gap-1.5 text-sm text-muted">
          Recognition backend
          <select
            className={fieldCls}
            value={form["recognition.backend"]}
            onChange={(e) => set("recognition.backend", e.target.value)}
          >
            <option value="olaf">olaf (real)</option>
            <option value="mock">mock (demo)</option>
          </select>
          {restart.has("recognition.backend") && (
            <span className="text-xs text-muted">Takes effect after a restart.</span>
          )}
        </label>

        {NUMS.map(([key, label, attrs]) => (
          <label key={key} className="flex flex-col gap-1.5 text-sm text-muted">
            {label}
            <input
              type="number"
              className={fieldCls}
              value={form[key]}
              {...attrs}
              onChange={(e) => set(key, e.target.value === "" ? "" : Number(e.target.value))}
            />
          </label>
        ))}

        <label className="flex items-center gap-2 text-sm text-fg">
          <input
            type="checkbox"
            checked={!!form["lyrics.enabled"]}
            onChange={(e) => set("lyrics.enabled", e.target.checked)}
          />
          Fetch synced lyrics
        </label>

        <label className="col-span-full flex flex-col gap-1.5 text-sm text-muted">
          MusicBrainz User-Agent
          <input
            type="text"
            className={fieldCls}
            value={form["metadata.musicbrainz_useragent"] || ""}
            onChange={(e) => set("metadata.musicbrainz_useragent", e.target.value)}
          />
        </label>
      </div>
      <div className="mt-4">
        <button
          onClick={onSave}
          disabled={saving}
          className="rounded-lg px-4 py-2 font-semibold text-[#181400] disabled:opacity-40"
          style={{ background: "var(--accent)" }}
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
      </div>
    </div>
  );
}

function numericOrString(v) {
  return /^\d+$/.test(v) ? parseInt(v, 10) : v;
}
