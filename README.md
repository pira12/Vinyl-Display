# Vinyl Display

Recognize the record currently spinning on your turntable and show it on any
screen — **the Raspberry Pi listens and serves a web app; you open it on an
iPad (or any browser) as the display**: **now playing**, **up next**, album art,
a progress bar, and **time-synced lyrics that scroll as the song plays**.

It works entirely from the audio coming off your turntable — no manual input.
Recognition is **self-hosted and offline** (no accounts, no API keys, no
per-play cost): it fingerprints against a database of *your own* records that
you enroll once.

The web app has **two modes**, switched with a toggle at the top:

- **Display** — the full-screen now-playing + lyrics view (your iPad screen).
- **Collection** — search and add records from the web, and record their sides.

```
┌─────────────────── Raspberry Pi (listener + server) ───────────────┐
│  USB line-in ─► rolling audio buffer                                │
│         └─► recognition loop ─► Olaf ─► {track, offset_in_track}    │
│                   ├─► MusicBrainz + Cover Art  → tracklist, art      │
│                   ├─► LRCLIB                    → synced lyrics      │
│                   └─► state ── websocket ──┐                         │
└────────────────────────────────────────────┼───────────────────────┘
                                              ▼  Wi-Fi (LAN)
                                  iPad / browser → web app
                                  (Display ⇄ Collection modes)
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

- Raspberry Pi 4 / 5 (the listener + server).
- **A screen = your iPad** (or any browser on the network). An HDMI screen on the
  Pi is *optional* — the kiosk service can drive one too if you want.
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
# open http://localhost:8080  — toggle between Display and Collection at the top
```

On your iPad, open `http://<pi-ip-address>:8080` (add it to the Home Screen for
a full-screen, chrome-free display). The **Display** mode is the screen; switch
to **Collection** to add records. `?mode=add` or `/manage` opens straight into
Collection.

Find your real audio device, then put it in `config.yaml`:

```bash
./.venv/bin/python -m backend.main --list-devices
```

## Adding records — Collection mode

Switch the web app to **Collection** mode (top toggle) from your iPad or any
device on the network. There you can **search an album**, **Add** it (its
tracklist, synced lyrics, and cover art are fetched from the internet and cached
on the Pi for offline use), and see your whole collection. Adding an album needs
no audio — do it from the couch.

Each side then needs its fingerprint recorded **once, on the Pi** (that's where
the turntable is). Start the record playing from the beginning of a side and tap
**Record side A**; a banner appears, and when the side finishes you tap **Stop &
save**. The Pi fingerprints the whole side as one reference and works out each
track's start time. A side shows a green ✓ once enrolled.

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
sudo cp systemd/vinyl-display.service /etc/systemd/system/
sudo systemctl enable --now vinyl-display.service
```

`vinyl-display.service` runs the listener + web server — that's all you need when
the **iPad is the screen**. If you *also* want an HDMI screen on the Pi itself,
install the optional kiosk too:

```bash
sudo cp systemd/vinyl-kiosk.service /etc/systemd/system/
sudo systemctl enable --now vinyl-kiosk.service   # opens /?mode=display fullscreen
```

Both assume the repo at `/home/pi/vinyl-display` and user `pi` — edit the units
if yours differ.

## Configuration

See `config.example.yaml` (copied to `config.yaml` by the setup script). Key
knobs:

- `audio.device` — your USB interface (name or index from `--list-devices`).
- `audio.silence_rms` — threshold for "needle up / gap" detection.
- `recognition.interval_seconds` — slow re-sync cadence for drift.
- `recognition.fast_interval_seconds` — fast cadence used to lock on at track changes.
- `playback.speed_factor` — turntable speed correction (e.g. `33.4/33.33`).

## Security

The companion API can **start audio capture from your turntable**, so it's
treated as a control surface, not just a viewer:

- **Token auth on `/api`.** Every management/control call requires a token
  (`X-Auth-Token` header or `?token=`). On first run a random token is generated,
  saved to `auth_token.txt` next to the database (mode `600`), and logged as a
  `/manage?token=…` link. Open that once on your phone — the token is stored in
  the browser and stripped from the URL. Set a fixed `server.auth_token` in the
  config to pin it. Comparisons use `hmac.compare_digest` (constant-time).
- **The display stays open.** `/`, `/ws`, and `/art` are read-only and need no
  token, so the kiosk and the phone's now-playing bar work without one. Only the
  collection/recording API is gated.
- **Input validation.** Release IDs are validated as MusicBrainz UUIDs before
  they ever touch a file path or index key (prevents path traversal); API bodies
  are checked and fail closed with `400`. The app fetches only MusicBrainz/LRCLIB
  /Cover-Art URLs it constructs itself — no user-supplied URLs (no SSRF surface).
- **Least-privilege service.** `systemd/vinyl-display.service` runs as a normal
  user with `NoNewPrivileges`, `ProtectSystem=strict`, read-only home, an
  explicit `ReadWritePaths` allow-list, and `PrivateTmp`.
- **Network exposure.** It binds `0.0.0.0` so your phone can reach it; set
  `server.host: 127.0.0.1` to keep it Pi-only, or add a firewall rule limiting
  tcp/8080 to your LAN (`ufw allow from 192.168.0.0/16 to any port 8080`). Don't
  port-forward it to the public internet.

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
  index.html         single-page app: Display + Collection modes
  app.js             mode switch, shared websocket, lyrics sync, collection mgmt
  styles.css         display + collection styling (iPad-friendly, landscape/portrait)
systemd/             backend service (+ optional HDMI kiosk)
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
