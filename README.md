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
# open http://localhost:8080          (the kiosk display)
# open http://localhost:8080/manage   (the phone companion app)
```

Find your real audio device, then put it in `config.yaml`:

```bash
./.venv/bin/python -m backend.main --list-devices
```

## Adding records — from your phone

The backend also serves a **companion web app** for managing your collection.
On any phone/laptop on the same network, open:

```
http://<pi-ip-address>:8080/manage
```

There you can **search an album**, **Add** it (its tracklist, synced lyrics, and
cover art are fetched from the internet and cached on the Pi for offline use),
and see your whole collection. Adding an album needs no audio — do it from the
couch.

Each side then needs its fingerprint recorded **once, on the Pi** (that's where
the turntable is). In the companion app, start the record playing from the
beginning of a side and tap **Record side A**; a banner appears, and when the
side finishes you tap **Stop & save**. The Pi fingerprints the whole side as one
reference and works out each track's start time. A side shows a green ✓ once
enrolled.

> Recording the reference from your *own* turntable is what makes matching so
> robust to vinyl noise and pitch.

### Or from the command line

```bash
./.venv/bin/python -m backend.enroll search "blood on the tracks dylan"  # find the MBID
./.venv/bin/python -m backend.enroll album --release <RELEASE_MBID>      # cache metadata
./.venv/bin/python -m backend.enroll record --out sideA.wav --minutes 25 # record a side
./.venv/bin/python -m backend.enroll add sideA.wav --release <RELEASE_MBID> --side A
```

Each side is fingerprinted as one continuous reference; track start-times come
from the silent bands between songs, falling back to MusicBrainz track lengths
if a record segues without gaps.

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
- `recognition.interval_seconds` — slow re-sync cadence for drift.
- `recognition.fast_interval_seconds` — fast cadence used to lock on at track changes.
- `playback.speed_factor` — turntable speed correction (e.g. `33.4/33.33`).

## Project layout

```
backend/
  main.py            entry point (server + recognition loop on one event loop)
  config.py          YAML config -> typed dataclasses
  audio/
    capture.py       rolling ring buffer, RMS level, full-side recording
    split.py         track-boundary detection by silent bands
  recognition/
    recognizer.py    the loop: silence -> recognize -> publish or resync (offline)
    olaf.py          shells out to the Olaf CLI
    mock.py          fake record (with cached lyrics) for --simulate
    models.py        albums + sides index; resolve(side, offset) -> track+position
  metadata/
    musicbrainz.py   release search, tracklist + cover art (cached, rate-limited)
    lyrics.py        LRCLIB synced-lyrics fetch + LRC parsing
  enrollment.py      shared service: search/add albums, record + fingerprint sides
  enroll.py          CLI wrapper around the enrollment service
  state.py           now-playing state + websocket fan-out
  server.py          kiosk page, /ws, /art, and the companion /api + /manage
frontend/
  index.html/app.js  kiosk UI (interpolated clock, art backdrop, synced lyrics)
  manage.html/.js    phone companion app
systemd/             backend + kiosk service units
```

## How recognition works (the clever bit)

Each **side** is one fingerprint reference. A single recognition returns the
side plus your **offset within it**, and the index maps that offset to *which
track* and *how far into it* you are — so there's no fragile per-track splitting
at runtime, and the position drives the progress bar and synced-lyrics scroll.
The loop locks on fast at track changes, then relaxes to a slow cadence that
only corrects drift. Lyrics and art are read from the local cache, so the
**runtime needs no internet at all**.

## Notes & limitations

- **Offline-first:** if MusicBrainz/LRCLIB are unreachable, it falls back to a
  tracklist built from your local index and simply omits lyrics/art.
- **Olaf output parsing:** Olaf's CLI columns have shifted between versions. If
  recognition matches but shows the wrong track/offset, adjust the `COL_*`
  constants in `backend/recognition/olaf.py` (documented there).
- **Lyric timing** is anchored to the official release; the periodic re-sync and
  `speed_factor` keep drift to a sub-second wobble.
