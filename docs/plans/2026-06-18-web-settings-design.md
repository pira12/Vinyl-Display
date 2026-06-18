# Web-based settings

## Goal

Let the user change configuration from the web app instead of editing
`config.yaml` by hand on a headless Pi. Scope is "everyday + recognition
tuning": audio device, MusicBrainz User-Agent, silence threshold, slow and fast
sync intervals, speed factor, lyrics on/off, min match score, and the olaf/mock
backend toggle.

Out of scope (stays in the file): server host/port, auth token/require_auth,
Olaf paths, samplerate, channels, cache/db dirs.

## Apply model

Changes apply live where possible; the few that re-open the audio stream are
saved and flagged "restart to apply".

Live-applicable, because the running code reads them at use-time or via a
mutable attribute:

- `audio.silence_rms` - read each recognizer tick and in `fingerprint_side`.
- `recognition.interval_seconds` / `fast_interval_seconds` - currently copied to
  locals at loop start; refactored to read from `self.cfg` inside the loop.
- `playback.speed_factor` - mirror into `StateManager.speed_factor`.
- `recognition.min_match_score` - mirror into the Olaf backend's `min_score`.
- `metadata.musicbrainz_useragent` - mirror into the MusicBrainz + lyrics
  clients' `user_agent`.
- `lyrics.enabled` - read at enrollment time from `cfg`.

Restart-only: `audio.device`, `recognition.backend` (both reopen capture).

## Components

### `backend/settings.py`

- `MANAGED_FIELDS` - the dotted keys the UI owns, with type/validation metadata.
- `RESTART_FIELDS = {"audio.device", "recognition.backend"}`.
- `validate(changes) -> dict` - pure; returns cleaned values or raises
  `ValueError` with a per-field message. No partial application.
- `save_config(cfg_path, changes)` - re-reads the raw YAML dict from disk,
  patches only managed keys, writes it back. Preserves unmanaged keys; note that
  PyYAML rewrites the file so inline comments are lost.
- `SettingsManager` - holds references to `cfg`, `state`, the recognition
  `backend`, the `mb` and `lyrics` clients, and `cfg_path`. Methods:
  - `snapshot()` -> `{values, devices, restart_fields}`.
  - `update(changes)` -> validates, mutates `cfg` + the live mirrors above,
    persists, returns `{applied, restart_required}`.

### `backend/audio/capture.py`

Add `list_input_devices() -> list[dict]` returning `{index, name, channels}`
for devices with input channels (structured, unlike the existing string
`list_devices()`). Degrades to `[]` if sounddevice is unavailable.

### `backend/server.py`

Two auth-gated routes (reuse the existing `/api` token middleware):

- `GET /api/settings` -> `manager.snapshot()`.
- `POST /api/settings` -> body of changed keys; validate -> apply -> persist;
  `400` with `{error}` on validation failure, else `{applied, restart_required}`.

`create_app` gains a `settings: SettingsManager | None` parameter; `main.run`
constructs it after the clients/backend/state exist and passes it in.

### `backend/recognition/recognizer.py`

Read `interval_seconds` / `fast_interval_seconds` from `self.cfg` inside the
loop instead of caching to locals.

### Frontend (`app.js`, `styles.css`)

A "Settings" panel inside Collection mode (gear link in the Collection header),
not a third top-level mode. On open, `GET /api/settings` fills the controls:

- device `<select>` from `devices` (+ "System default" = null)
- backend select (olaf/mock)
- number inputs for silence_rms, speed_factor, intervals, min score
- lyrics checkbox
- User-Agent text field
- "needs restart" hint on fields in `restart_fields`

Save POSTs only changed keys, reusing the stored token. Toast reflects
`restart_required`; inline error on `400`.

## Validation rules

- `audio.device`: null, int index, or a string matching a detected device name.
- `audio.silence_rms`: float in [0, 1].
- `recognition.backend`: "olaf" | "mock".
- `interval_seconds`, `fast_interval_seconds`: positive floats; fast <= slow.
- `recognition.min_match_score`: positive int.
- `playback.speed_factor`: float in [0.8, 1.2].
- `lyrics.enabled`: bool.
- `metadata.musicbrainz_useragent`: non-empty string.

## Testing

- `validate` accepts good values and rejects each bad case.
- `save_config` round-trip: patch a key, reload, unmanaged keys preserved.
- `SettingsManager.update` mirrors speed_factor into state and min_score into a
  fake backend, and reports restart_required for device changes.
- Route test: `GET/POST /api/settings` gated by token; bad body -> 400.

## Security

All settings endpoints are under `/api`, so the existing token middleware gates
them. No new auth surface. Networking/auth fields are deliberately not editable
from the UI.
