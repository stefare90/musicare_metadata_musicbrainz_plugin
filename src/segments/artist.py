"""``IArtist``: profile, discography, top tracks and related artists.

Top tracks come from ListenBrainz, ranked by real listen count; the endpoint returns the
whole ranking in one call, so the plugin keeps the first ``_POPULAR_MAX`` per artist in a
small in-memory buffer and serves the contract's pagination from it. Related artists come
from the ListenBrainz Labs similarity endpoint; resolving each one costs a MusicBrainz
lookup, so the loop is bounded by a time budget and returns a partial page rather than
stalling the caller.
"""

import time
from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import (
    Album,
    Artist,
    IArtist,
    PaginatedResult,
    Track,
    TransportError,
)

from ..net import HttpClient
from ..images.wikidata import WikidataArtistImages
from ..service import ListenBrainz
from ..shared.mapping import (
    build_album_from_release_group,
    build_artist,
    build_popularity_track,
    discography_sort_key,
)
from ..shared.providers import LISTENBRAINZ_LABS, MUSICBRAINZ_API

# ListenBrainz Labs similarity algorithm, ported verbatim from the old plugin.
_LABS_DAYS = 7500
_LABS_SESSION = 300
_LABS_CONTRIBUTION = 5
_LABS_THRESHOLD = 10
_LABS_LIMIT = 100
_LABS_FILTER = True
_LABS_SKIP = 30
# Similar artists resolve one MusicBrainz lookup per item; cap that fan-out.
_RELATED_BUDGET_SECONDS = 15.0

# MusicBrainz browse caps pages at 100; the discography is fetched whole so the
# date-descending order holds across the contract's paginated slices.
_DISCOGRAPHY_FETCH = 100
# Artists whose (already sorted) discography is kept to serve pagination without refetching.
_DISCOGRAPHY_BUFFERED_ARTISTS = 8

# "Popular tracks" is the top of the ranking, not the artist's whole catalogue.
_POPULAR_MAX = 50
# Artists whose (already capped) ranking is kept to serve pagination without refetching.
_POPULAR_BUFFERED_ARTISTS = 8


def _top_recordings(entries: List[Any]) -> List[Dict[str, Any]]:
    """Deduplicate by recording MBID, rank by listen count and keep the top ``_POPULAR_MAX``.

    ListenBrainz sends the rows already sorted, but it also contains duplicate rows for the
    same recording (some carrying a bogus count of 1); ranking explicitly keeps the best one
    and makes the result stable via the MBID tie-break.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        mbid = entry.get("recording_mbid")
        if not mbid:
            continue
        count = entry.get("total_listen_count")
        count = count if isinstance(count, int) else 0
        current = best.get(str(mbid))
        if current is None or count > current["total_listen_count"]:
            best[str(mbid)] = dict(entry, total_listen_count=count)
    ranked = sorted(
        best.items(), key=lambda item: (-item[1]["total_listen_count"], item[0])
    )
    return [entry for _, entry in ranked[:_POPULAR_MAX]]


class MusicBrainzArtist(IArtist):
    def __init__(
        self,
        client: HttpClient,
        images: WikidataArtistImages,
        lb: ListenBrainz,
        user: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._images = images
        self._lb = lb
        self._user = user
        self._popular: Dict[str, List[Track]] = {}
        self._popular_order: List[str] = []
        self._discography: Dict[str, List[Album]] = {}
        self._discography_order: List[str] = []

    def _fetch_artist(self, mbid: str) -> Dict[str, Any]:
        return self._client.get_json(
            f"{MUSICBRAINZ_API}artist/{mbid}", params={"inc": "genres", "fmt": "json"}
        )

    def get_artist(self, id: str) -> Artist:
        data = self._fetch_artist(id)
        images = self._images.resolve([id]).get(id, [])
        return build_artist(data, images)

    def albums(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Album]:
        albums = self._discography.get(id)
        if albums is None:
            albums = self._discography_albums(id)
            self._discography[id] = albums
            self._discography_order.append(id)
            while len(self._discography_order) > _DISCOGRAPHY_BUFFERED_ARTISTS:
                self._discography.pop(self._discography_order.pop(0), None)
        return PaginatedResult(
            items=albums[offset : offset + limit],
            total=len(albums),
            offset=offset,
            limit=limit,
        )

    def _discography_albums(self, id: str) -> List[Album]:
        # The browse endpoint answers in provider order and takes no ordering parameter,
        # so every page is fetched and the whole discography is sorted newest-first once;
        # slicing a per-page sort instead would shuffle releases across page boundaries.
        groups: List[Dict[str, Any]] = []
        expected: Optional[int] = None
        fetched = 0
        while True:
            data = self._client.get_json(
                f"{MUSICBRAINZ_API}release-group",
                params={
                    "artist": id,
                    "type": "album",
                    "limit": _DISCOGRAPHY_FETCH,
                    "offset": fetched,
                    "inc": "artist-credits",
                    "fmt": "json",
                },
            )
            page = []
            if isinstance(data, dict):
                if expected is None:
                    raw_total = data.get("release-group-count")
                    if not isinstance(raw_total, int):
                        raw_total = data.get("count")
                    if isinstance(raw_total, int):
                        expected = raw_total
                for group in data.get("release-groups") or []:
                    if isinstance(group, dict):
                        page.append(group)
            if not page:
                break
            groups.extend(page)
            fetched += len(page)
            if expected is not None and fetched >= expected:
                break
        groups.sort(key=discography_sort_key)
        return [build_album_from_release_group(group) for group in groups]

    def top_tracks(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        tracks = self._popular_tracks(id)
        return PaginatedResult(
            items=tracks[offset : offset + limit],
            total=len(tracks),
            offset=offset,
            limit=limit,
        )

    def _popular_tracks(self, id: str) -> List[Track]:
        # Checked before the buffer: after a logout a cached ranking must not be served.
        self._lb.require_auth()
        cached = self._popular.get(id)
        if cached is not None:
            return cached
        data = self._lb.top_recordings_for_artist(id)
        if not isinstance(data, list):
            raise TransportError("ListenBrainz returned an unexpected popularity response")
        tracks = [build_popularity_track(entry) for entry in _top_recordings(data)]
        self._popular[id] = tracks
        self._popular_order.append(id)
        while len(self._popular_order) > _POPULAR_BUFFERED_ARTISTS:
            self._popular.pop(self._popular_order.pop(0), None)
        return tracks

    def related(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Artist]:
        algorithm = (
            f"session_based_days_{_LABS_DAYS}_session_{_LABS_SESSION}"
            f"_contribution_{_LABS_CONTRIBUTION}_threshold_{_LABS_THRESHOLD}"
            f"_limit_{_LABS_LIMIT}_filter_{_LABS_FILTER}_skip_{_LABS_SKIP}"
        )
        # "Fans Also Like" is a whole section the user sees: a failed call must surface as a
        # retryable error, not as an empty section. A reachable service that knows no similar
        # artist answers `[]`, which stays a legitimate empty result (it is hidden, not failed).
        candidates = self._client.get_json(
            f"{LISTENBRAINZ_LABS}/similar-artists/json",
            params={"artist_mbids": id, "algorithm": algorithm},
        )
        if not isinstance(candidates, list):
            raise TransportError("ListenBrainz Labs returned an unexpected response")

        deadline = time.monotonic() + _RELATED_BUDGET_SECONDS
        profiles: List[Dict[str, Any]] = []
        mbids: List[str] = []
        for index in range(offset, min(offset + limit, len(candidates))):
            entry = candidates[index]
            mbid = entry.get("artist_mbid") if isinstance(entry, dict) else None
            if not mbid:
                continue
            if time.monotonic() > deadline:
                break
            profiles.append(self._fetch_artist(str(mbid)))
            mbids.append(str(mbid))

        images = self._images.resolve(mbids)
        items = [
            build_artist(profile, images.get(str(profile.get("id")), []))
            for profile in profiles
        ]
        return PaginatedResult(
            items=items, total=len(candidates), offset=offset, limit=limit
        )

    def save(self, ids: List[str]) -> None:
        for artist_id in ids:
            self._user.save_artist(artist_id)

    def unsave(self, ids: List[str]) -> None:
        for artist_id in ids:
            self._user.unsave_artist(artist_id)
