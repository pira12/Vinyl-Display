# Vinyl Display

Recognize the record currently spinning on your turntable and show it on any
screen: now playing, up next, album art, a progress bar, and time-synced lyrics
that scroll as the song plays.

An iPad (or any browser) listens through its microphone and shows the display.
A small server does the matching and serves the web app, so it can run anywhere
on your network, including as a container. Recognition works like Shazam — put
on any record and it's identified automatically, with nothing to set up, no
account, and no API key. Add your records to the collection (a quick search,
one tap) and the display also gets the album's tracklist, "up next", and
lyrics + art cached for that record.

The web app has two modes, switched with a toggle at the top:

- Display: the full-screen now-playing and lyrics view (your iPad screen).
- Collection: search and add records, and change settings.

```
iPad (Safari, https://vinyl.ossolab.net)            Server (container, any host)
  mic ─► Web Audio ─► 10s WAV clip ──POST /api/recognize──► Shazam ─► {track, offset}
                                                              ├─► your collection
                                                              │    (tracklist, art,
                                                              │     cached lyrics)
                                                              ├─► LRCLIB (lyrics)
  websocket ◄───────────────── now-playing state ◄───────────┘
```

## How it works

| Concern | Tool | Account? |
|---|---|---|
| Recognition + position-in-track | Shazam (via [shazamio](https://github.com/shazamio/ShazamIO)) | none |
| Tracklist / "up next" / album art | MusicBrainz + Cover Art Archive | none |
| Time-synced lyrics | [LRCLIB](https://lrclib.net) | none |

The iPad captures about ten seconds of microphone audio, downsamples it, and
posts it to the server, which identifies the track and where in it you are. The
result is matched against your collection: if the album is there, the display
shows the full tracklist, up next, and the lyrics/art cached when you added it;
if not, the track still shows, with art from Shazam and lyrics fetched on the
fly. The frontend seeds a local clock with the reported offset and advances it
for smooth progress and lyric scrolling, re-syncing every several seconds to
absorb turntable speed drift. Microphone processing (echo cancellation, noise
suppression, auto-gain) is turned off so the music isn't mangled before
matching.

### The offline alternative: Olaf

The original self-hosted pipeline is still available (`recognition.backend:
olaf` in Settings): it fingerprints your own records with
[Olaf](https://github.com/JorenSix/Olaf) and matches fully offline, but each
side has to be recorded once through the iPad mic before it can be recognized.
Pick it if you'd rather not depend on an online service; the recording UI
reappears automatically in that mode.

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
foreground with the screen awake. That's it — put on a record and it shows up.

### Adding records to the collection

In Collection mode, search an album and **Add** it. Search returns one row per
album (not per pressing), and adding picks the best release automatically —
official and on vinyl where possible, so track positions come out as A1/B2.
The album's tracklist, synced lyrics, and cover art are fetched once and cached
on the server; from then on a recognized track from that album gets the full
display: tracklist, up next, and offline lyrics/art.

### Recording sides (olaf backend only)

With the offline `olaf` backend, each side must also be recorded once: play a
side from the beginning and tap **Record side A**; the iPad streams it to the
server, which fingerprints it and works out each track's start time. A room
microphone has a higher noise floor than a line input, so the silent gaps
between tracks may be harder to detect. The `audio.silence_rms` setting tunes
this, and it falls back to MusicBrainz track lengths when gaps aren't found.
On the default shazam backend this whole step doesn't exist.

### Settings, in the web app

Collection mode has a **Settings** panel: audio device (for local line-in dev),
MusicBrainz User-Agent, silence threshold, sync intervals, speed factor, lyrics
on/off, min match score (olaf), and the recognition backend (shazam / olaf /
mock). Most changes apply immediately; device and backend changes apply on the
next restart. Saving rewrites the config file, so its comments are not
preserved.

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
  fetches only MusicBrainz, LRCLIB, Cover Art, and Shazam URLs it builds
  itself.
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
    shazam.py        default backend: identify via Shazam, map to collection
    olaf.py          offline backend: shells out to the Olaf CLI
    mock.py          fake record for --simulate
    models.py        albums index + fuzzy find_track(); sides for olaf
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
- The shazam backend rides an unofficial (but widely used) API and needs
  internet at play time. If that ever bothers you, the olaf backend matches
  fully offline — at the cost of recording each side once.
- Lyrics and art for collection albums are cached when the album is added. If
  MusicBrainz or LRCLIB are unreachable at that moment, it falls back to a
  local tracklist and omits lyrics/art.
- Olaf's CLI columns have shifted between versions. If olaf recognition matches
  but shows the wrong track or offset, adjust the `COL_*` constants in
  `backend/recognition/olaf.py`.
