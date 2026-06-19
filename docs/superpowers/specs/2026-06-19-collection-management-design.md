# Collection Management + Search Overhaul — Design

Date: 2026-06-19
Status: Approved

## Goal

Fill the most glaring functional gaps in Vinyl Display's Collection mode: you can
add albums but never delete, fix, or re-learn them, search is noisy, and there is
no way to browse your own records. This round delivers full collection management
and a much better add-an-album search.

## Scope

In:
1. Better album search — server-side dedup by release-group + quality ranking.
2. Live (debounced) search with a Clear button and stale-response guarding.
3. Deterministic auto-refresh after add (optimistic insert + refresh).
4. Delete album (index entry, its sides, ref WAVs, cached art, best-effort Olaf delete).
5. Re-record / overwrite a side without stacking duplicate fingerprints.
6. Edit album metadata (title / artist / year).
7. Album detail overlay (tracklist, per-side status, edit, re-record, delete).
8. Collection sort + filter (frontend only).
9. Tests for the new backend behaviour.

Out (deferred): idle screensaver, manual now-playing override, play history /
scrobbling, PWA / QR pairing.

## Design decisions

### Search dedup (backend)
`MusicBrainzClient.search_releases` currently returns raw `/release` hits, so the
same album appears many times (different pressings/countries). Change it to:
- request a larger page (limit ~25) and include `release-group` info,
- collapse to one row per `release-group.id`,
- prefer `status == "Official"`, then has-cover-art, then has a date,
- return ~12 unique albums, preserving MusicBrainz relevance order.

This stays a single request; adding still uses the chosen release MBID directly.

### Album detail (frontend)
The app has no router (mode-based UI), so detail is a **full-screen overlay**
inside `CollectionView`, opened by tapping a card. It shows art, inline-editable
title/artist/year, the full tracklist with lyric indicators, per-side enrolled
status with a Re-record action, and Delete. State lives in `CollectionView`.

### Delete safety
Removing a side from the index means `TrackIndex.resolve` returns `None` for that
key, so recognition can never surface a deleted record even if Olaf still holds
the fingerprint. Olaf deletion is therefore best-effort; index removal is the
source of truth.

### Re-record without duplicates
`fingerprint_side` writes a deterministic key (`<slug>-side-<x>`). Re-recording
overwrites the index `Side` but `olaf store` would stack a second fingerprint on
the same audio. Fix: if the ref WAV for that key already exists, `olaf delete` it
before re-storing.

## Backend changes

- `recognition/olaf.py`: add `OlafRecognizer.delete(wav_path)` shelling out to
  `olaf delete <wav>` (best-effort; log + swallow failures so a missing/older
  Olaf build never blocks index mutation).
- `enrollment.py`:
  - `delete_album(album_id)`: validate MBID; drop album from `index.albums`;
    drop every side with `album_id == id` from `index.sides`, deleting each
    `refs/<key>.wav` (best-effort `backend.delete`); delete `art/<id>.jpg`;
    `index.save()`.
  - `update_album(album_id, fields)`: update allowed fields (title/artist/year)
    on the `Album`, `index.save()`, return `album_summary`.
  - `fingerprint_side`: before `store`, if `ref_wav` exists call
    `backend.delete(ref_wav)` (guarded — backend may lack `delete`).
- `server.py`:
  - `DELETE /api/albums/{album_id}` → `enrollment.delete_album`.
  - `PATCH /api/albums/{album_id}` → `enrollment.update_album` (JSON body with
    any of title/artist/year). Validate album exists → 404 otherwise.
  - Both behind the existing token middleware (already covers `/api/*`).
- `metadata/musicbrainz.py`: dedup logic in `search_releases` as above.

## Frontend changes

- `api.js`: add `del(p)` and `patch(p, json)` helpers.
- `CollectionView.jsx`:
  - debounced live search (300 ms) replacing click-to-search; request-token guard
    so a slow earlier response can't overwrite a newer one; Clear (✕) button.
  - optimistic insert of the added album into `albums` plus `refresh()`.
  - collection toolbar: filter input (title/artist substring) + sort select
    (Recently added [default], Artist A–Z, Title A–Z, Enrolled first).
  - card tap opens the detail overlay.
- `AlbumCard.jsx`: make the card body tappable to open detail; keep per-side
  Learn buttons. Add an "Added ✓" affordance for freshly added search rows.
- New `AlbumDetail.jsx`: overlay with editable fields, tracklist, per-side
  Re-record, Delete (with confirm), Close.

### Recently-added ordering
`index.albums` is an insertion-ordered dict; `collection()` preserves it. Newest
is last, so the frontend reverses for the "Recently added" sort. No schema change.

## Testing

Extend `tests/`:
- delete removes album + its sides + ref files; resolve returns None after.
- re-record replaces the side (no duplicate Side; old ref deleted before store).
- update_album changes only allowed fields.
- search_releases collapses multiple releases sharing a release-group to one,
  preferring Official + has-art (use a fake `_get`).

## Risks / notes

- Older Olaf builds may not support `delete`; all calls are best-effort and the
  index remains authoritative, so functionality degrades gracefully (a stale
  fingerprint that resolves to nothing).
- Live search increases MusicBrainz request volume; the existing 1 req/sec lock
  in `MusicBrainzClient` plus 300 ms debounce keeps us within courtesy limits.
