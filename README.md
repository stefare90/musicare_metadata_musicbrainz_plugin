# MusicBrainz & ListenBrainz Metadata Plugin

Official MusicAre **metadata** plugin, written in **100% Pure-Python**. It implements the
`musicare_metadata_plugin_sdk` contracts and is loaded by the MusicAre metadata runtime
(`musicare_plugin_sdk`, `pluginSdkVersion 5.0.0`). It replaces the archived Dart bytecode
plugin (`gyawun_metadata_plugin`), whose behaviour it preserves.

Providers: **MusicBrainz** (search, releases, release groups, recordings),
**ListenBrainz** (token, liked tracks, user playlists, radios, algorithmic playlists,
artist popularity), **Wikidata** (artist images, via the MediaWiki API) and
**Wikimedia Commons** (image URLs).

## Why it exists

The old plugin ran as `.evc` bytecode on `dart_eval 0.8.5`. That runtime cannot catch an
**asynchronous** failure, so a network error could not be routed into the plugin's error
handling — a runtime constraint, not a coding slip. Running the same logic as ordinary
Python removes that class of bug, drops the hand-patched binding layer and the three-repo
build chain.

## Requirements

- **100% Pure-Python**: no native extension (`.so`, `.pyd`, `.dylib`, `.dll`) and no
  dependency with compiled parts. The only runtime dependency is **`certifi`** — pure
  Python, vendored into `plugin.zip`: the CPython embedded in the Android app ships no CA
  store, so without it HTTPS fails with `CERTIFICATE_VERIFY_FAILED`. It is the same reason
  the YouTube audio plugin vendors it.
- `pluginSdkVersion: 5.0.0`. The matching host SDK is `musicare_metadata_host_sdk`.
  **The plugin requires a host SDK `5.0.0`**: an app below that version rejects it (the
  user sees the update page), because `IPlaylist.add_tracks` now returns an
  `AddTracksResult` instead of `void` (a breaking contract change). Older hosts that only
  add tracks and ignore the outcome still work at the wire level, but a host built on a
  previous contract version would refuse to load this plugin.

## Layout

```text
plugin.json          # manifest (id, packageId, type, version, pluginSdkVersion, …)
src/
├── main.py          # get_plugin() factory, called by the runtime
├── plugin.py        # wires the segments and exposes the interfaces
├── net/             # HTTP transport: stdlib client (certifi TLS, User-Agent,
                     # Retry-After, X-RateLimit-*, throttles, keep-alive)
├── service/         # ListenBrainz service: token, library, playlists
├── shared/          # helpers every segment uses: payload mapping (mapping.py),
                     # playlist documents (jspf.py), endpoints and URI/cover
                     # conventions (providers.py)
├── images/          # Wikidata MediaWiki API (P434/P18) and Wikimedia Commons URLs
└── segments/        # one module per interface: core, search, album, artist,
                     # track, playlist, user, auth, browse
test/                # pytest suite (offline by default, live tests behind -m live)
```

## Contract coverage

| Interface | Method | Status |
| --- | --- | --- |
| `ICore` | `support` | implemented (repo issues URL) |
| `ICore` | `check_update`, `scrobble` | **unsupported** (as in the old plugin) |
| `ISearch` | `chips`, `all`, `tracks`, `albums`, `artists` | implemented against MusicBrainz |
| `ISearch` | `playlists` | implemented: local filter over the user's saved ListenBrainz playlists |
| `IAlbum` | `get_album`, `tracks`, `save`, `unsave` | implemented |
| `IArtist` | `get_artist`, `top_tracks`, `albums`, `related`, `save`, `unsave` | implemented |
| `ITrack` | `get_track`, `radio`, `save`, `unsave` | implemented |
| `IPlaylist` | `get_playlist`, `tracks`, `create_playlist`, `update_playlist`, `delete_playlist`, `add_tracks`, `remove_tracks`, `save`, `unsave` | implemented |
| `IUser` | `me`, `saved_tracks`, `saved_albums`, `saved_artists`, `saved_playlists` | implemented |
| `IAuth` | `start`, `complete`, `cancel`, `logout`, `is_authenticated` | implemented (token form) |
| `IBrowse` | `sections`, `section_items` | implemented |

A method left unimplemented is reported as `unsupported` by the runtime, so the two
`ICore` gaps are a deliberate, working state.

### Behaviour notes

- Albums are addressed as `rg:<release-group-mbid>` or `<release-mbid>`. A release group
  is resolved to its first release for the track listing; cover art is taken at the
  release-group level (see below).
- A `Track` always carries a complete `Album` and complete `Artist`s, as the contract
  requires; when MusicBrainz returns a recording without a release, the album comes from
  the surrounding context (album page, playlist entry).
- When a recording exists in several publications, the album is chosen **deterministically**
  instead of by MusicBrainz's response order, and **at the album level**: a release group
  whose primary type is `Album` (with no secondary type such as Compilation/Live/Remix) is
  preferred over a single, an EP or a compilation, so the track points at its own album even
  when that album came out later than a single. Among the remaining candidates the criteria
  are ascending date (a missing month/day counts as the end of its period, the way
  MusicBrainz orders them), non-studio editions deprioritised and official prints preferred;
  the release id breaks ties. This matters because the bulk `recording?query=…` search used
  by `saved_tracks` returns `releases` in a **different** order than the direct lookup, so
  taking `releases[0]` could land the album on a "Summer Hits" compilation — measured on
  *Fenomeno*: before the fix the liked track pointed at the **single**'s release group while
  the album page showed the album. A release hint carried by the entry wins over the sort (a
  liked feedback's `track_metadata.mbid_mapping.caa_release_mbid`, when ListenBrainz stored
  one; the app's own feedback usually leaves `track_metadata` empty). `saved_albums` is
  unaffected: it resolves the saved release-group id directly.
- **Covers are taken at the release-group level** (`coverartarchive.org/release-group/<id>/front-<size>`):
  the one canonical front image per album. `build_album` uses the group cover whenever the
  release payload carries a release group and falls back to the release cover only when the
  group is absent. A release is one edition, so this is what makes the album page, the saved
  tracks, the tracks built from a recording and the playlists written by the plugin show the
  **same** picture. The Cover Art Archive exposes a group's front image as long as any of its
  releases has one; whether the group has one is not in the MusicBrainz payload, so "group
  present → group cover" is the only deterministic rule (no extra request). A track that
  appears **only** on a single/EP/compilation gets that publication's release group: there is
  no album to prefer, so its cover and name are the publication's.
- `artist.top_tracks` ("Popular tracks") is the artist's ranking by **real listen count**
  from ListenBrainz `popularity/top-recordings-for-artist`, which requires the token. Rows
  are deduplicated by recording MBID (ListenBrainz returns duplicate rows — 37 on
  Radiohead, some with a bogus count of 1), ranked by `total_listen_count` with the MBID
  as a stable tie-break, and capped at **50**: the section is the top of the ranking, not
  the whole catalogue. Covers come straight from the entry's
  `caa_release_mbid`/`release_mbid`, so no extra lookup is needed. The endpoint returns
  the whole list and ignores `offset`/`limit`, so the capped ranking is buffered in memory
  per artist (bounded to the last 8) and the contract's pagination is served from it.
  Signed out it raises `auth_required` (the ranking only exists with a token); a reachable
  service that knows no listens answers `[]`, a legitimate empty. An unreachable service
  is a retryable error.
- `artist.related` ("Fans Also Like") uses the ListenBrainz Labs similarity endpoint; each
  suggestion needs a MusicBrainz lookup, so the fan-out is capped by a 15 s budget and a
  partial page is returned rather than stalling the caller. It is a **visible section**, so
  a failed Labs call is raised as a **retryable error** (the host shows the retry box); a
  reachable service that knows no similar artist answers `[]`, which stays a legitimate
  empty result (the section is hidden, not broken).
- Radios — `track.radio` and the synthetic `radio:artist:*` / `radio:tag:*` playlist ids
  (the Home **Mood Playlists**) — are **authenticated**: ListenBrainz `lb-radio` requires
  the token. Signed out, the plugin raises `auth_required` before any request, so the host
  shows the sign-in invitation instead of a raw 401. Their tracks are JSPF entries that
  reference the album only through `release_identifier` (a MusicBrainz release URL), which
  the parser reads, so covers come from the Cover Art Archive (about two thirds of
  `lb-radio` releases have one). The radio playlist itself carries no artwork (`images`
  empty) — a radio has none of its own; the host may build a mosaic from the tracks.
  JSPF entries carry the credited names packed in `creator` (`"A feat. B"`) next to
  `artist_identifiers`: entries with several identifiers resolve each name from its
  MBID with one batched MusicBrainz search per playlist page (cached for the session),
  so the split works in every language; the local conjunction split stays as the
  fallback when the lookup fails or is partial, and a single identifier keeps the
  full name (a duo credited as one artist stays one entry).
- `search.playlists` has no public provider to call: ListenBrainz exposes no playlist text
  search. It filters the **user's saved playlists** locally, case insensitively, over both
  `name` and `description`, then paginates the filtered list (`total` is the filtered
  count). Without a token the public `listenbrainz` account is used, so its curated
  playlists (Weekly Exploration / Weekly Jams) surface; `chips()` declares the category and
  `all()` includes up to five playlists. They carry no covers (`images` empty), so the host
  shows its fallback icon. Search follows the **uniform error policy**: a failed
  ListenBrainz call is a retryable error that fails `all()` too, exactly like a failed
  MusicBrainz call on the other categories.
- The **saved library** is three private ListenBrainz playlists (`__GYAWUN_ALBUMS__`,
  `__GYAWUN_ARTISTS__`, `__GYAWUN_PLAYLISTS__`) described under *Authentication*. The plugin
  treats them as the saved set: `save_album`/`save_artist`/`save_playlist` are **idempotent**
  (saving an id already there is a no-op, so a repeated tap cannot append a second row),
  `save_playlist` stores a **reference** to the other user's playlist instead of copying it
  (the id stays stable and the heart tracks the original), and `unsave_*` of an id that is not
  there is a **silent no-op**. `saved_playlists` is the user's own playlists plus those
  references resolved to the real documents, skipping a reference whose playlist has since
  disappeared. `playlist.save`/`unsave` go through this library; `playlist.delete_playlist`
  is a **real delete** (`playlist/<id>/delete`), independent of `unsave`. A **synthetic radio**
  (`radio:*`) cannot be saved — it is generated on every read, has no playlist document, and
  ListenBrainz rejects an item whose identifier is not a recording MBID — so saving one raises
  `unsupported`. Reads return the playlists as they are: duplicate rows a pre-idempotent
  library already has are **not** collapsed and are a manual cleanup, not something the read
  path hides. `is_public` (the host's visibility badge) is read from the JSPF `extension` on
  **every** path that builds a `Playlist` from a raw ListenBrainz document — the library list
  and *Created For You* included, not just `get_playlist`; a missing `extension` or a missing
  `public` key reads as private, which is ListenBrainz's default.
- `playlist.add_tracks` is **idempotent** and **reports what it changed**: it returns an
  `AddTracksResult { added, already_present }`. An id already in the playlist (or repeated
  within the batch) is not added again and is listed in `already_present`; `added` counts the
  new tracks actually sent to ListenBrainz after their JSPF entry is resolved, so adding a
  track that is already there is a successful no-op with `added: 0` and **no request**. The
  new tracks keep their order and the requested `position`. The outcome is returned only after
  the write succeeds, and a failure is a call-level error (one of the typed errors), never a
  partial result — the call is idempotent, so retrying it is safe. The entries the plugin
  writes carry the album's `release_group_mbid`, so a playlist built from the app shows the
  album cover; an entry ListenBrainz already holds keeps its own release reference. The
  trade-off is that a deliberate repetition of the same recording in the same playlist cannot
  be expressed through this API.

## Authentication

The flow is a small state machine the host drives over the `auth.*` calls:

- `start` → `NeedsForm([token])` with form copy when no token is stored, `Authenticated()`
  when there is one. `title` is "Connect to ListenBrainz", `message` is "Paste the
  personal token from your ListenBrainz profile." and the field's `help_url` is
  https://listenbrainz.org/profile/. This is **provider copy shown verbatim**: the host
  does not translate it. The call is idempotent, so it is also how `auth.status` polls.
- `complete` validates the token against ListenBrainz `validate-token`; a valid token is
  stored in the plugin data directory as `auth.json` and `Authenticated()` is returned.
  A rejected token is a `Failed(code=auth_required)` outcome; a network failure is
  `Failed(code=transport_error, retryable=True)`.
- `logout` clears the stored token; `cancel` needs no rollback.
- `is_authenticated` reads `auth.json` and refreshes the in-memory token that `IUser`
  uses (the runtime passes an `AuthContext` only to `IAuth`).

The token never reaches the host: the user types it into the host's form and it stays in
the plugin's own data directory. The account **name is cached, but the cache is keyed by
the token**: logging out and in as another user invalidates it automatically, so `me()`
and the `saved_*` calls cannot serve the previous account, while an unchanged token keeps
serving the cache with no `validate-token` call per request.

**Saved albums, artists and playlists** have no first-class MusicBrainz equivalent, so the
plugin maintains three private ListenBrainz playlists (`__GYAWUN_ALBUMS__`,
`__GYAWUN_ARTISTS__`, `__GYAWUN_PLAYLISTS__`), created on demand. Item identifiers keep the
old plugin's scheme so a library saved before the migration still matches; playlists are
stored by reference (the other user's id), not copied.

OAuth2 + PKCE (loopback redirect) is planned for providers that require it; this version
only prompts for the token.

## Error policy and provider etiquette

- **Mandatory calls** fail with the correct typed error: `not_found`, `rate_limited`,
  `transport_error`, `auth_required`, `invalid_argument`.
- **Image enrichment distinguishes "absent" from "unavailable".** An artist without a
  Wikidata entity or without a Commons image has an **empty** `images` list — that is
  data, not a failure. A request that could not be *made* (timeout, 429, 5xx) is raised as
  the matching retryable SDK error so the host can retry; it is never cached, so a retry
  really re-queries. The rule is the same for every call site (search, detail, related,
  saved), so the behaviour is uniform and the app stays agnostic.
- **Degradation is decided by *where the data appears*, not by "is the call optional".**
  Enrichment *inside* a response that exists anyway (images, accessory fields) may degrade
  silently. The **primary content of a section the user sees** (e.g. "Fans Also Like")
  must fail with a typed error — `retryable` on a network cause — never an empty result:
  a reachable provider that has no data is a legitimate empty, an unreachable or anomalous
  one is an error. This is why `related()` propagates instead of using a best-effort
  helper.
- **Server etiquette is measured, not assumed.** One request per second per host
  (`musicbrainz.org`, `api`+`labs.api.listenbrainz.org`, `coverartarchive.org`);
  `X-RateLimit-Reset-In`/`Retry-After` drive the backoff, an exhausted budget
  (`Remaining: 0`) defers the next call, and a delay the budget cannot afford
  surfaces immediately as `rate_limited`. Connections are reused per host
  (stdlib keep-alive, ~0.1 s saved per request on desktop); the `User-Agent`
  carries the shipped version. Measured 09/10/2026: MusicBrainz 503s come from
  unthrottled bursts (never observed while throttled), ListenBrainz timeouts
  with zero bytes are server-side (identical over a fresh connection), and the
  budget headers above are sent by the providers on every response.
- **Artist images come from the Wikidata MediaWiki API**, not from SPARQL: two calls
  resolve a whole page (`haswbstatement:P434=…|…` maps ids to entities, `wbgetentities`
  reads `P18`), measured at ~1 s against 5–30 s for the equivalent SPARQL query — which
  timed out even on trivial queries. Resolved images and genuine misses are cached per
  session (`IMAGES_TIMEOUT = 5 s`).

## Testing

```bash
# 1. environment (Python 3.10+; the system python has no pip/pytest)
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 2. offline suite: HTTP is mocked, no network
.venv/bin/python -m pytest test/ -q

# 3. live suite: real MusicBrainz / ListenBrainz / Wikidata
.venv/bin/python -m pytest test/ -q -m live
```

## Packaging and end-to-end verification

```bash
# build plugin.zip (validates the manifest, audits Pure-Python, writes the archive)
.venv/bin/musicare-build

# certify against the platform harness (Linux, or an Android device id).
# `weekly` matches the curated anonymous playlists; `radiohead` returns none for them,
# and the harness treats an empty expected method as a failure.
../musicare_plugin_sdk/dart/harness/test_metadata_plugin.sh linux \
  "$(realpath plugin.zip)" "weekly" \
  "core.support,search.chips,search.tracks,search.albums,search.artists,search.playlists,album.getAlbum,artist.getArtist,track.getTrack"
```

`musicare-build` comes from `musicare-plugin-builder` (a dev dependency; the metadata SDK
itself is provided by the host staging and is never vendored). It vendors `certifi` into
`plugin.zip`; `plugin.zip` is git-ignored and published as a GitHub release asset.
