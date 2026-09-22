"""``ISearch``: text search over MusicBrainz.

Recordings become tracks, release groups become albums, artists are enriched with their
Wikidata image. ``playlists`` stays empty: the ListenBrainz catalogue has no public text
search, and adding one is a separate feature (the host already renders the chip).
"""

from typing import Any, Dict, List

from musicare_metadata_plugin_sdk import (
    Album,
    Artist,
    ISearch,
    PaginatedResult,
    Playlist,
    SearchCategory,
    SearchResponse,
    Track,
)

from ..http import HttpClient
from ..images.wikidata import SEARCH_TIMEOUT, WikidataArtistImages
from ..mapping import build_album_from_release_group, build_artist, build_track
from ..providers import MUSICBRAINZ_API

_AGGREGATE_LIMIT = 5


def _page(data: Any, key: str, parser, offset: int, limit: int):
    """Build a page from a MusicBrainz list response, tolerating missing keys."""
    items: List[Any] = []
    total = 0
    if isinstance(data, dict):
        for raw in data.get(key) or []:
            if isinstance(raw, dict):
                items.append(parser(raw))
        count = data.get("count")
        total = count if isinstance(count, int) else len(items)
    return PaginatedResult(items=items, total=total, offset=offset, limit=limit)


class MusicBrainzSearch(ISearch):
    def __init__(self, client: HttpClient, images: WikidataArtistImages) -> None:
        self._client = client
        self._images = images

    def chips(self) -> List[SearchCategory]:
        return [SearchCategory.TRACKS, SearchCategory.ALBUMS, SearchCategory.ARTISTS]

    def _search(self, entity: str, query: str, offset: int, limit: int) -> Any:
        return self._client.get_json(
            f"{MUSICBRAINZ_API}{entity}",
            params={"query": query, "limit": limit, "offset": offset, "fmt": "json"},
        )

    def tracks(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        data = self._search("recording", query, offset, limit)
        return _page(data, "recordings", build_track, offset, limit)

    def albums(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Album]:
        data = self._search("release-group", query, offset, limit)
        return _page(data, "release-groups", build_album_from_release_group, offset, limit)

    def artists(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Artist]:
        data = self._search("artist", query, offset, limit)
        page = _page(data, "artists", build_artist, offset, limit)
        return PaginatedResult(
            items=self._images.enrich(page.items, timeout=SEARCH_TIMEOUT),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def playlists(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Playlist]:
        return PaginatedResult(items=[], total=0, offset=offset, limit=limit)

    def all(self, query: str) -> SearchResponse:
        tracks = self.tracks(query, limit=_AGGREGATE_LIMIT)
        albums = self.albums(query, limit=_AGGREGATE_LIMIT)
        artists = self.artists(query, limit=_AGGREGATE_LIMIT)
        return SearchResponse(
            albums=albums.items,
            artists=artists.items,
            playlists=[],
            tracks=tracks.items,
        )
