# App redesign, onboarding, and search — design

Date: 2026-07-10

## Goal

Make the whole app feel like the **Display** view (cinematic dark glass), make it
**self-serviceable for other self-hosters** (friendly first-run onboarding, auth
off by default on a LAN, generic copy), and **fix album search** so an artist name
or a common album title returns the right results.

Four coordinated changes; deployment stays Docker/Dokploy.

## 1. Search quality (backend)

**Problem.** `MusicBrainzClient.search_albums` queries release-groups by *title*
only (`{"query": <text>}`, which Lucene routes to the `releasegroup` field). So
an **artist** query ("the weeknd") matches no album titles and returns junk, and
a bare title ("utopia") ranks every same-named album by raw MB score.

**Fix (implemented).** MusicBrainz has no popularity signal, so three
strategies, in order:

1. **Famous title by pressing count.** Search the title field, filter to studio
   albums, rank by release `count` (a popularity proxy — "Thriller" has 85
   pressings, a same-named bar band has 1). If the top exact-title match clears
   a threshold (25), it wins. Settles words that are both album and band name
   ("1989", "rumours", "nevermind").
2. **Artist catalog.** If the query is a confident artist-name match with a real
   discography (≥3 studio albums), return that artist's catalog newest-first
   ("the weeknd", "bad bunny") — text score alone buries their albums under
   obscure title matches ("The Perfect Weeknd").
3. **Fallback.** Count-ranked title matches, else a title-or-artist text search.

No API/shape change for the frontend.

## 2. Auth optional, off by default (backend)

- `ServerConfig.require_auth` default `True` → **`False`**.
- `asgi.py` reads a `REQUIRE_AUTH` env flag (truthy → `cfg.server.require_auth =
  True`), alongside the existing `AUTH_TOKEN` override.
- When auth is off, `_resolve_token` already returns `None` and the `/api` gate
  is inert — the app just works on a LAN.
- `/healthz` (public, ungated) gains `"auth_required": bool` so the frontend and
  onboarding know whether to show the token step.

Security note: default-open is deliberate for a personal LAN display; the README
and onboarding tell people to set `REQUIRE_AUTH=1` when exposing it to the
internet.

## 3. Visual restyle → Display's glass aesthetic (frontend)

Keep the existing tokens (`bg #0b0b0f`, `fg #f4f1ea`, `muted`, gold `accent`,
`panel`) and the shared blurred backdrop. Introduce a small set of component
classes in `index.css` (`@layer components`) so every surface is consistent:

- `.glass-card` — `rounded-2xl border-white/10 bg-white/[0.04] backdrop-blur-md`
- `.glass-input` — glassy input, `focus:border-white/25`, no hard `#2a2a33`
- `.btn`, `.btn-accent` (gold), `.btn-ghost` (white/5)

Restyle **CollectionView, SettingsPanel, AlbumDetail, AlbumCard, MicStatus, the
search rows, the auth panel, and ModeBar** to use these instead of boxy `panel`
cards and hard borders. More whitespace, larger headings, pill buttons, the
single gold accent. No new colors, no layout churn beyond spacing.

## 4. Onboarding for self-hosters (frontend)

New `Onboarding.jsx` — a dismissible, glassy full-screen overlay shown once
(localStorage `vinyl_onboarded`), with a step flow:

1. **Welcome** — what the app is, one line.
2. **Access** — only if `/healthz.auth_required`: how to get the token
   (`docker logs`), a paste field; otherwise skipped with a "you're on a trusted
   network" note.
3. **Microphone** — button to request mic permission (explains iOS HTTPS/tap).
4. **Add your first record** — hands off to the search box.

Generic copy throughout (no personal URLs). Friendlier empty states in the
collection grid ("No records yet — search above to add your first").

## Testing

- Backend: extend `tests/` for the new search query string and for
  `require_auth` default-off / `REQUIRE_AUTH` on, and `/healthz` shape.
- Frontend: `npm run build` clean.
- Manual: search "the weeknd" and "utopia" return the right albums; first load
  shows onboarding; LAN load needs no token; Collection matches Display's look.

## Out of scope

Machine-translation of lyrics (parked), one-click deploy tooling, multi-user
accounts.
