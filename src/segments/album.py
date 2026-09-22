"""``IAlbum``: album details and track listing.

An album is addressed either by a ``rg:<release-group-mbid>`` or by a release MBID: for a
release group the plugin picks the first release, because track listings and cover art
from the Cover Art Archive are release-scoped. ``save``/``unsave`` belong to the user
library and are implemented by the ``IUser``/``IPlaylist`` slice.
"""

from dataclasses import replace
from typing import Any, Dict, List

from musicare_metadata_plugin_sdk import (
    Album,
    IAlbum,
    NotFoundError,
    PaginatedResult,
    Track,
)

from ..http import HttpClient
from ..mapping import build_album, build_track
from ..providers import MUSICBRAINZ_API

_RELEASE_INCLUDES = "artist-credits+recordings+release-groups"


class MusicBrainzAlbum(IAlbum):
    def __init__(self, client: HttpClient) -> None:
        self._client = client

    def _resolve_release_id(self, album_id: str) -> str:
        if not album_id.startswith("rg:"):
            return album_id
        group_id = album_id[3:]
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}release",
            params={"release-group": group_id, "limit": 1, "fmt": "json"},
        )
        releases = data.get("releases") if isinstance(data, dict) else None
        if isinstance(releases, list) and releases and isinstance(releases[0], dict):
            return str(releases[0].get("id") or "")
        raise NotFoundError(f"No releases found for release group {group_id}")

    def _fetch_release(self, release_id: str) -> Dict[str, Any]:
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}release/{release_id}",
            params={"inc": _RELEASE_INCLUDES, "fmt": "json"},
        )
        if not isinstance(data, dict):
            raise NotFoundError(f"Release {release_id} not found")
        return data

    def get_album(self, id: str) -> Album:
        return build_album(self._fetch_release(self._resolve_release_id(id)))

    def tracks(self, id: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        release_id = self._resolve_release_id(id)
        album = build_album(self._fetch_release(release_id))
        data = self._client.get_json(
            f"{MUSICBRAINZ_API}recording",
            params={
                "release": release_id,
                "limit": limit,
                "offset": offset,
                "inc": "artist-credits",
                "fmt": "json",
            },
        )
        return self._build_tracks(data, album, offset, limit)

    @staticmethod
    def _build_tracks(
        data: Any, album: Album, offset: int, limit: int
    ) -> PaginatedResult[Track]:
        items: List[Track] = []
        if isinstance(data, dict):
            for recording in data.get("recordings") or []:
                if isinstance(recording, dict):
                    # A recording listed by release carries no releases block, so the
                    # album resolved from the release is the authoritative one.
                    items.append(replace(build_track(recording), album=album))
        total = data.get("recording-count") if isinstance(data, dict) else None
        if not isinstance(total, int):
            total = len(items)
        return PaginatedResult(items=items, total=total, offset=offset, limit=limit)
