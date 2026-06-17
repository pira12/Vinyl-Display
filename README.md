# Vinyl Display

Recognize the record currently spinning on your turntable and show it on a
screen attached to a Raspberry Pi: **now playing**, **up next**, album art, a
progress bar, and **time-synced lyrics that scroll as the song plays**.

It works entirely from the audio coming off your turntable — no manual input,
no tapping a phone. Recognition is **self-hosted and offline** (no accounts, no
API keys, no per-play cost): it fingerprints against a database of *your own*
records that you enroll once.

```
┌─────────────────────────── Raspberry Pi ───────────────────────────┐
│  USB line-in ─► rolling audio buffer                                │
│         └─► recognition loop ─► Olaf ─► {track, offset_in_track}    │
│                   ├─► MusicBrainz + Cover Art  → tracklist, art      │
│                   ├─► LRCLIB                    → synced lyrics      │
│                   └─► state ── websocket ──► Chromium kiosk UI       │
└─────────────────────────────────────────────────────────────────────┘
```

## How it works

| Concern | Tool | Account? |
|---|---|---|
| Recognition + position-in-track | [Olaf](https://github.com/JorenSix/Olaf) (self-hosted) | none |
| Tracklist / "up next" / album art | MusicBrainz + Cover Art Archive | none |
| Time-synced lyrics | [LRCLIB](https://lrclib.net) | none |

The recognizer returns *where in the track* you are. We seed a local clock with
that offset and let the frontend advance it for smooth progress and lyric
scrolling, re-syncing every ~20s to absorb turntable speed drift.

## Hardware

- Raspberry Pi 4 / 5 + a screen (HDMI or the official touchscreen).
- A USB audio interface with **line-in and pass-through**, e.g. Behringer UCA202,
  so the record still plays to your speakers while the Pi listens.
- A phono preamp **only if your turntable doesn't have one built in**.

```
Turntable ─► [phono preamp if needed] ─► UCA202 in
                                          ├─ pass-through out ─► amp / speakers
                                          └─ USB ─► Raspberry Pi
```

## Quick start

```bash
git clone <this repo> vinyl-display && cd vinyl-display
./scripts/setup_pi.sh           # installs deps, builds Olaf, makes a venv
```

Try it immediately **without any hardware** (mock recognizer plays a fake
record so you can see the whole UI):

```bash
./.venv/bin/python -m backend.main --simulate
# open http://localhost:8080
```

Find your real audio device, then put it in `config.yaml`:

```bash
./.venv/bin/python -m backend.main --list-devices
```

## Enrolling your records (one time each)

Recognition matches against your own collection, so each record is enrolled
once. Recording the reference from the *same* turntable makes matching far more
robust to vinyl noise and pitch.

```bash
# 1. Record a side off the line-in (Ctrl-C to stop when it ends):
./.venv/bin/python -m backend.enroll record --out sideA.wav --minutes 25

# 2. Split into tracks, fingerprint, and tag from a MusicBrainz release:
./.venv/bin/python -m backend.enroll add sideA.wav --release <RELEASE_MBID> --side A
```

Find the `RELEASE_MBID` by searching the album on
[musicbrainz.org](https://musicbrainz.org) — it's the UUID in the release URL.
The auto-splitter separates tracks by the silent bands between them; if it
miscounts, pass `--tracks N` and tune `audio.silence_rms` in the config.

## Running on boot

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl enable --now vinyl-display.service vinyl-kiosk.service
```

`vinyl-display.service` runs the backend; `vinyl-kiosk.service` waits for it and
opens the UI fullscreen in Chromium. Both assume the repo at
`/home/pi/vinyl-display` and user `pi` — edit the units if yours differ.

## Configuration

See `config.example.yaml` (copied to `config.yaml` by the setup script). Key
knobs:

- `audio.device` — your USB interface (name or index from `--list-devices`).
- `audio.silence_rms` — threshold for "needle up / gap" detection.
- `recognition.interval_seconds` — how often it re-recognizes / re-syncs.
- `playback.speed_factor` — turntable speed correction (e.g. `33.4/33.33`).

## Project layout

```
backend/
  main.py            entry point (server + recognition loop on one event loop)
  config.py          YAML config -> typed dataclasses
  audio/capture.py   rolling ring buffer + RMS level from the USB input
  recognition/
    recognizer.py    the loop: silence -> recognize -> metadata/lyrics or resync
    olaf.py          shells out to the Olaf CLI
    mock.py          fake record for --simulate
    models.py        Match, TrackRef, and the on-disk TrackIndex
  metadata/
    musicbrainz.py   tracklist + cover art (cached, rate-limited)
    lyrics.py        LRCLIB synced-lyrics fetch + LRC parsing
  state.py           now-playing state + websocket fan-out
  enroll.py          record / split / fingerprint your records
frontend/            kiosk UI (vanilla JS, interpolated clock for smooth sync)
systemd/             backend + kiosk service units
```

## Notes & limitations

- **Offline-first:** if MusicBrainz/LRCLIB are unreachable, it falls back to a
  tracklist built from your local index and simply omits lyrics/art.
- **Olaf output parsing:** Olaf's CLI columns have shifted between versions. If
  recognition matches but shows the wrong track/offset, adjust the `COL_*`
  constants in `backend/recognition/olaf.py` (documented there).
- **Lyric timing** is anchored to the official release; the periodic re-sync and
  `speed_factor` keep drift to a sub-second wobble.
