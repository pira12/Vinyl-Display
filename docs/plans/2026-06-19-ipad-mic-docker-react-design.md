# iPad-mic recognition, Docker/Traefik deploy, React rewrite

## Context

The Pi should become a pure server that can live in any room. Recognition and
enrollment audio come from the **iPad microphone** instead of the Pi's line-in.
The user already runs **Traefik** with split-horizon DNS for `ossolab.net` on the
LAN and deploys containers with **Dokploy**, so TLS and routing are solved
upstream. Target hostname: `vinyl.ossolab.net`.

This supersedes the Pi-line-in capture model for deployment. `main.py` +
`AudioCapture` stay in the tree for local dev/`--simulate` only.

## Architecture

```
iPad (Safari, https://vinyl.ossolab.net)        Pi container (Traefik-fronted)
 getUserMedia(mic, EC/NS/AGC off) -> Web Audio
   recognize: ~10s clip -> 16k WAV --POST /api/recognize--> olaf query -> resolve -> state
   enroll:    streamed PCM chunks  --POST /api/record/*---> grow WAV -> fingerprint_side
 websocket  <----------------------------------------------  now-playing state
```

- Olaf + the fingerprint DB stay on the Pi. No in-browser matching.
- The iPad drives the recognition cadence; the server-side polling loop is gone.
- iOS needs HTTPS for the mic — satisfied by Traefik's real cert. No app-side TLS.

## Backend changes

- Remove the `sounddevice`/`AudioCapture` runtime path and the
  `RecognitionService` polling loop. Drop `sounddevice` from requirements.
- `POST /api/recognize`: mono 16 kHz/16-bit WAV in -> tmp file -> `OlafRecognizer.query`
  -> `TrackIndex.resolve` -> `StateManager.set_now_playing`/`resync`. No-ops when
  `state.listening` is false. Returns the match summary.
- Client-fed enrollment session (replaces sounddevice recording):
  - `POST /api/record/start {album_id, side}` -> open session + growing WAV on `/data`.
  - `POST /api/record/chunk` (binary PCM) -> append.
  - `POST /api/record/stop` -> finalize WAV, run `EnrollmentService.fingerprint_side`.
  - `POST /api/record/cancel` -> discard.
- App bootstrap moves into a FastAPI lifespan handler (container runs
  `uvicorn backend.server:app`); it builds state/index/backend/enrollment/settings
  from env + the `/data` volume. `numpy`/`soundfile` stay for enrollment splitting.
- Token auth unchanged; all new routes are under `/api`.

## Deployment

Single image, multi-stage:
1. Build Olaf with Zig (pinned) on `debian:trixie-slim`.
2. Build the React app with `node:22-slim` -> `dist/`.
3. Slim `python:3.13-slim` runtime: ffmpeg + libstdc++, pip fastapi/uvicorn/httpx/
   pyyaml/numpy/soundfile; copy the Olaf binary and `dist/`. FastAPI serves `dist/`
   (SPA fallback) plus `/api`, `/ws`, `/art`.

- Data volume at `/data`: Olaf DB, `index.json`, `refs/`, art, lyrics cache, `auth_token.txt`.
- Dokploy compose with Traefik labels: `Host(\`vinyl.ossolab.net\`)`,
  `entrypoints=websecure`, existing TLS resolver, `loadbalancer.server.port=8080`.
  No published ports; Traefik fronts it.

## Frontend (React + Vite + Tailwind)

- `useNowPlaying` websocket hook; `api()` client with the existing token flow.
- Views: `DisplayView` (art, meta, progress, ported lyric scroller), `CollectionView`,
  `SettingsPanel`, `ModeBar`.
- `useMicRecognition`: getUserMedia (EC/NS/AGC off) on a user tap, AudioWorklet ring
  buffer, downsample to 16 kHz, WAV-encode, POST on a fast/slow cadence from settings,
  honoring the `listening` flag and pausing when backgrounded.
- Collection as a responsive grid of square cover tiles (title/artist/year/tracks below).
- Download/enroll loader: spinner overlay on the tile while requests are in flight.
- Favicons + `apple-touch-icon` + `manifest.webmanifest` in `public/`.

## Testing

- Backend pytest (`TestClient`, mock backend): recognize round-trip updates state;
  record start/chunk/stop enrolls a side; listening-off no-op; token gating.
- Vitest: WAV encoder + downsampler + token/api client (pure units).
- Container: build + smoke `/healthz` + serve `dist/`; on-device HTTPS mic pass.

## Build phases

1. Backend re-architecture + tests (vanilla UI still runs).
2. Dockerize + Traefik/Dokploy deploy; validate iOS mic end-to-end.
3. React rewrite: Display, Collection grid, Settings, mic hooks, loader, favicons.
4. Design polish pass.

## Notes / limitations

- iOS mic needs HTTPS (have it) and a user gesture; continuous listening only while
  foregrounded with the screen awake.
- Mic has a higher noise floor than line-in; `audio.silence_rms` tunes track
  splitting, with MusicBrainz track-length fallback.
