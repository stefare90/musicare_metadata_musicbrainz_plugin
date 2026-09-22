"""``IArtist``: profile, discography, top tracks and related artists.

Top tracks are assembled from the top releases' embedded recordings, deduplicated by
title and ranked by their MusicBrainz rating. Related artists come from the ListenBrainz
Labs similarity endpoint; resolving each one costs a MusicBrainz lookup, so the loop is
bounded by a time budget and returns a partial page rather than stalling the caller.
"""

import time
from typing import Any, Dict, List, Optional

from musicare_metadata_plugin_sdk import (
    Album,
    Artist,
    IArtist,
    PaginatedResult,
    Track,
)

from ..http import HttpClient
from ..images.wikidata import WikidataArtistImages
from ..mapping import build_album_from_release_group, build_artist, build_track
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


def _rating_average(recording: Dict[str, Any]) -> float:
    rating = recording.get("rating")
    if not isinstance(rating, dict):
        return 0.0
    votes = rating.get("votes-count")
    value = rating.get("value")
    votes = votes if isinstance(votes, (int, float)) else 0.0
    value = value if isinstance(value, (int, float)) else 0.0
    return value / votes if votes > 0 else 0.0


class MusicBrainzArtist(IArtist):
    def __init__(
        self,
        client: HttpClient,
        images: WikidataArtistImages,
        user: Optional[Any] = None,
    ) -> None:
        self._client = client
        self._images = images
        self._user = user

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
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}release",
            params={
                "fmt": "json",
                "artist": id,
                "limit": 5,
                "offset": 0,
                "inc": "artist-credits+recordings+ratings+isrcs+release-groups",
            },
        )
        releases = data.get("releases") if isinstance(data, dict) else None
        if not isinstance(releases, list):
            return PaginatedResult(items=[], total=0, offset=offset, limit=limit)

        recordings: List[Dict[str, Any]] = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            media = release.get("media")
            if not isinstance(media, list):
                continue
            # The album attached to each recording must not carry the whole track list.
            release_reference = dict(release, media=None)
            for medium in media:
                if not isinstance(medium, dict):
                    continue
                for track in medium.get("tracks") or []:
                    if not isinstance(track, dict):
                        continue
                    recording = track.get("recording")
                    if isinstance(recording, dict):
                        recordings.append(dict(recording, releases=[release_reference]))

        unique: List[Dict[str, Any]] = []
        seen = set()
        for recording in recordings:
            title = recording.get("title")
            if isinstance(title, str) and title not in seen:
                seen.add(title)
                unique.append(recording)
        unique.sort(key=_rating_average, reverse=True)

        items = [build_track(recording) for recording in unique]
        return PaginatedResult(
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            limit=limit,
        )

    def related(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Artist]:
        algorithm = (
            f"session_based_days_{_LABS_DAYS}_session_{_LABS_SESSION}"
            f"_contribution_{_LABS_CONTRIBUTION}_threshold_{_LABS_THRESHOLD}"
            f"_limit_{_LABS_LIMIT}_filter_{_LABS_FILTER}_skip_{_LABS_SKIP}"
        )
        candidates = self._client.get_json_or_none(
            f"{LISTENBRAINZ_LABS}/similar-artists/json",
            params={"artist_mbids": id, "algorithm": algorithm},
        )
        if not isinstance(candidates, list):
            return PaginatedResult(items=[], total=0, offset=offset, limit=limit)

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
