# Vinyl Display

Recognize the record currently spinning on your turntable and show it on any
screen: now playing, up next, album art, a progress bar, and time-synced lyrics
that scroll as the song plays.

An iPad (or any browser) listens through its microphone and shows the display.
A small server does the fingerprint matching and serves the web app, so it can
run anywhere on your network, including as a container. Recognition is
self-hosted and offline once a record is enrolled (no accounts, no API keys, no
per-play cost): it fingerprints against a database of your own records.

The web app has two modes, switched with a toggle at the top:

- Display: the full-screen now-playing and lyrics view (your iPad screen).
- Collection: search and add records, record their sides, and change settings.

```
iPad (Safari, https://vinyl.ossolab.net)            Server (container, any host)
  mic ─► Web Audio ─► 10s WAV clip ──POST /api/recognize──► Olaf ─► {track, offset}
                                                              ├─► MusicBrainz + Art
                                                              ├─► LRCLIB (lyrics)
  websocket ◄───────────────── now-playing state ◄──────────┘
```

## How it works

| Concern | Tool | Account? |
|---|---|---|
| Recognition + position-in-track | [Olaf](https://github.com/JorenSix/Olaf) (self-hosted) | none |
| Tracklist / "up next" / album art | MusicBrainz + Cover Art Archive | none |
| Time-synced lyrics | [LRCLIB](https://lrclib.net) | none |

The iPad captures about ten seconds of microphone audio, downsamples it, and
posts it to the server, which runs an Olaf query and returns where in the track
you are. The frontend seeds a local clock with that offset and advances it for
smooth progress and lyric scrolling, re-syncing every several seconds to absorb
turntable speed drift. Microphone processing (echo cancellation, noise
suppression, auto-gain) is turned off so the music isn't mangled before
fingerprinting.

Both recognition and enrollment use the same iPad microphone, so matching is
self-consistent and robust.

## Requirements

- A host to run the server. A Raspberry Pi 4/5 works; so does any machine that
  runs Docker. It does not need to be near the turntable.
- An iPad or phone near the turntable/speakers to listen and display.
- HTTPS. iOS only grants microphone access over HTTPS (or localhost), so the
  server must be reached over `https://`. This setup uses Traefik to terminate
  TLS for `vinyl.ossolab.net`.

## Deploy (Docker + Dokploy + Traefik)

The server is one container that bundles the Olaf binary, the API, and the built
React app. TLS and routing are handled by Traefik.

```bash
git clone <this repo> vinyl-display && cd vinyl-display
# Deploy with Dokploy pointed at this repo, or directly:
docker compose up -d --build
```

- `docker-compose.yml` carries the Traefik labels for `vinyl.ossolab.net` on
  port 8080. Adjust the `certresolver` name and the external network to match
  your Traefik/Dokploy setup.
- State lives on the `vinyl-data` volume (Olaf DB, index, references, art cache,
  lyrics cache, and the auth token), so it survives redeploys.
- On first start the server logs a `?token=…` link. Open it once on the iPad to
  unlock Collection mode (see Security).

The first build is slow: it compiles Olaf with Zig and builds the React app.

## Using it

Open `https://vinyl.ossolab.net` on the iPad (add it to the Home Screen for a
full-screen display). In Collection mode, tap **Start listening** and grant the
microphone when asked. Continuous listening runs while the app is in the
foreground with the screen awake.

### Adding and enrolling records

In Collection mode, search an album and **Add** it; its tracklist, synced
lyrics, and cover art are fetched and cached on the server for offline use. Then
play a side from the beginning and tap **Record side A**; the iPad streams the
side to the server, which fingerprints it and works out each track's start time.
A side shows a green check once enrolled. Recording a side this way needs no
prior setup beyond microphone access.

A room microphone has a higher noise floor than a line input, so the silent
gaps between tracks may be harder to detect. The `audio.silence_rms` setting
tunes this, and it falls back to MusicBrainz track lengths when gaps aren't
found.

### Settings, in the web app

Collection mode has a **Settings** panel: audio device (for local line-in dev),
MusicBrainz User-Agent, silence threshold, sync intervals, speed factor, lyrics
on/off, min match score, and the olaf/mock backend. Most changes apply
immediately; device and backend changes apply on the next restart. Saving
rewrites the config file, so its comments are not preserved.

### Start/stop listening

The **Start/Stop listening** button pauses recognition so it isn't running
non-stop. While paused the display shows "Paused" and the server does no
matching.

## Local development

Run the backend and the Vite dev server separately. Microphone capture needs
HTTPS, so for mic testing use the deployed HTTPS host; the rest of the UI works
over localhost.

```bash
./scripts/setup_pi.sh                       # system deps, Olaf, a venv
./.venv/bin/python -m backend.main --simulate   # mock recognizer, no hardware
# in another shell:
cd frontend && npm install && npm run dev   # proxies /api, /ws, /art to :8080
```

`backend/main.py` also supports the original Raspberry Pi line-in path (a USB
audio interface feeding the Pi) for local use; the container path uses the iPad
microphone instead.

## Security

The API can start audio capture and change settings, so it is treated as a
control surface:

- Token auth on `/api`. Every management or control call requires a token
  (`X-Auth-Token` header or `?token=`). On first run a random token is generated,
  saved next to the database (mode `600`), and logged as a `?token=…` link. Open
  it once on the iPad; the token is stored in the browser and stripped from the
  URL. Comparisons use `hmac.compare_digest`.
- The display stays open. `/`, `/ws`, and `/art` are read-only and need no
  token. Only the collection, recording, recognition, and settings API is gated.
- Input validation. Release IDs are validated as MusicBrainz UUIDs before they
  touch a file path or index key; API bodies fail closed with `400`. The app
  fetches only MusicBrainz, LRCLIB, and Cover Art URLs it builds itself.
- TLS is terminated by Traefik; the container speaks plain HTTP internally and
  publishes no ports of its own.

## Project layout

```
backend/
  asgi.py            container entrypoint: builds a pure server from DATA_DIR
  main.py            local-dev entry (line-in / --simulate, runs the loop)
  config.py          YAML config -> typed dataclasses
  settings.py        web-editable settings: validate, persist, apply live
  audio/             rolling buffer + silence splitting (line-in dev only)
  recognition/
    recognizer.py    apply_match()/publish_resolved(): resolve + publish state
    olaf.py          shells out to the Olaf CLI
    mock.py          fake record for --simulate
    models.py        albums + sides index; resolve(side, offset) -> track
  metadata/          MusicBrainz + LRCLIB clients (cached)
  enrollment.py      add albums; client-fed mic enrollment + fingerprinting
  state.py           now-playing state + websocket fan-out
  server.py          API, websocket, /art, and serving the built SPA
frontend/            React + Vite + Tailwind app
  src/hooks/useMic.js   shared mic engine: recognition + enrollment capture
  src/components/       Display, Collection (vinyl grid), Settings, mode bar
Dockerfile           Olaf (Zig) + React build + slim Python runtime
docker-compose.yml   Traefik labels for vinyl.ossolab.net
```

## Notes and limitations

- iOS needs HTTPS and a user tap to start the microphone; continuous listening
  only runs while the app is foregrounded with the screen awake.
- Offline-first runtime: lyrics and art are cached at enrollment, so matching
  needs no internet. If MusicBrainz or LRCLIB are unreachable while adding an
  album, it falls back to a local tracklist and omits lyrics/art.
- Olaf's CLI columns have shifted between versions. If recognition matches but
  shows the wrong track or offset, adjust the `COL_*` constants in
  `backend/recognition/olaf.py`.
