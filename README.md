# MusicBrainz & ListenBrainz Metadata Plugin

Official MusicAre **metadata** plugin, written in **100% Pure-Python**. It implements the
`musicare_metadata_plugin_sdk` contracts and is loaded by the MusicAre metadata runtime
(`musicare_plugin_sdk`, `pluginSdkVersion 4.0.0`). It replaces the archived Dart bytecode
plugin (`gyawun_metadata_plugin`), whose behaviour it preserves.

Providers: **MusicBrainz** (search, releases, release groups, recordings, ratings),
**ListenBrainz** (token, liked tracks, user playlists, radios, algorithmic playlists),
**Wikidata** (artist images, via the MediaWiki API) and **Wikimedia Commons** (image URLs).

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
- `pluginSdkVersion: 4.0.0`. The matching host SDK is `musicare_metadata_host_sdk`.

## Layout

```text
plugin.json          # manifest (id, packageId, type, version, pluginSdkVersion, …)
src/
├── main.py          # get_plugin() factory, called by the runtime
├── plugin.py        # wires the segments and exposes the interfaces
├── http.py          # stdlib JSON client: certifi TLS, User-Agent, Retry-After, rate limit
├── credentials.py   # the token shared between IAuth and IUser
├── listenbrainz.py  # ListenBrainz service: token, library, playlists
├── mapping.py       # MusicBrainz JSON -> SDK models
├── jspf.py          # JSPF (ListenBrainz playlist format) -> Track
├── providers.py     # endpoints and URI/cover conventions
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
  is resolved to its first release, because track listings and cover art are
  release-scoped.
- A `Track` always carries a complete `Album` and complete `Artist`s, as the contract
  requires; when MusicBrainz returns a recording without a release, the album comes from
  the surrounding context (album page, playlist entry, rating list).
- `artist.top_tracks` is assembled from the top releases, deduplicated by title and ranked
  by MusicBrainz rating, then paginated locally.
- `artist.related` ("Fans Also Like") uses the ListenBrainz Labs similarity endpoint; each
  suggestion needs a MusicBrainz lookup, so the fan-out is capped by a 15 s budget and a
  partial page is returned rather than stalling the caller.
- `search.playlists` has no public provider to call: ListenBrainz exposes no playlist text
  search. It filters the **user's saved playlists** locally, case insensitively, over both
  `name` and `description`, then paginates the filtered list (`total` is the filtered
  count). Without a token the public `listenbrainz` account is used, so its curated
  playlists (Weekly Exploration / Weekly Jams) surface; `chips()` declares the category and
  `all()` includes up to five playlists. They carry no covers (`images` empty), so the host
  shows its fallback icon. Search follows the **uniform error policy**: a failed
  ListenBrainz call is a retryable error that fails `all()` too, exactly like a failed
  MusicBrainz call on the other categories.

## Authentication

The flow is a small state machine the host drives over the `auth.*` calls:

- `start` → `NeedsForm([token])` when no token is stored, `Authenticated()` when there is
  one. It is idempotent, so it is also how `auth.status` polls.
- `complete` validates the token against ListenBrainz `validate-token`; a valid token is
  stored in the plugin data directory as `auth.json` and `Authenticated()` is returned.
  A rejected token is a `Failed(code=auth_required)` outcome; a network failure is
  `Failed(code=transport_error, retryable=True)`.
- `logout` clears the stored token; `cancel` needs no rollback.
- `is_authenticated` reads `auth.json` and refreshes the in-memory token that `IUser`
  uses (the runtime passes an `AuthContext` only to `IAuth`).

The token never reaches the host: the user types it into the host's form and it stays in
the plugin's own data directory.

**Saved albums and artists** have no first-class MusicBrainz equivalent, so the plugin
maintains two private ListenBrainz playlists (`__GYAWUN_ALBUMS__`,
`__GYAWUN_ARTISTS__`), created on demand. Item identifiers keep the old plugin's scheme so
a library saved before the migration still matches.

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
- **`User-Agent`** identifies the plugin; **`Retry-After`** is honoured on 429/503 with up
  to three attempts; MusicBrainz is throttled to one request per second.
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
