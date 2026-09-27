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

from ..http import HttpClient
from ..images.wikidata import WikidataArtistImages
from ..listenbrainz import ListenBrainz
from ..mapping import (
    build_album_from_release_group,
    build_artist,
    build_popularity_track,
)
from ..providers import LISTENBRAINZ_LABS, MUSICBRAINZ_API

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

    def _fetch_artist(self, mbid: str) -> Dict[str, Any]:
        return self._client.get_json(
            f"{MUSICBRAINZ_API}artist/{mbid}", params={"inc": "genres", "fmt": "json"}
        )

    def get_artist(self, id: str) -> Artist:
        data = self._fetch_artist(id)
        images = self._images.resolve([id]).get(id, [])
        return build_artist(data, images)

    def albums(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Album]:
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}release-group",
            params={
                "artist": id,
                "type": "album",
                "limit": limit,
                "offset": offset,
                "inc": "artist-credits",
                "fmt": "json",
            },
        )
        items: List[Album] = []
        if isinstance(data, dict):
            for group in data.get("release-groups") or []:
                if isinstance(group, dict):
                    items.append(build_album_from_release_group(group))
        total = len(items)
        if isinstance(data, dict):
            raw_total = data.get("release-group-count")
            if not isinstance(raw_total, int):
                raw_total = data.get("count")
            if isinstance(raw_total, int):
                total = raw_total
        return PaginatedResult(items=items, total=total, offset=offset, limit=limit)

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
