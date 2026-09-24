"""``ISearch``: text search over MusicBrainz.

Recordings become tracks, release groups become albums, artists are enriched with their
Wikidata image. ListenBrainz has no public playlist search, so ``playlists`` filters the
user's own saved playlists locally; without a token the public ``listenbrainz`` account is
used, which surfaces its curated playlists.
"""

from typing import Any, List

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
from ..images.wikidata import WikidataArtistImages
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
    def __init__(self, client: HttpClient, images: WikidataArtistImages, user: Any) -> None:
        self._client = client
        self._images = images
        self._user = user

    def chips(self) -> List[SearchCategory]:
        return [
            SearchCategory.TRACKS,
            SearchCategory.ALBUMS,
            SearchCategory.ARTISTS,
            SearchCategory.PLAYLISTS,
        ]

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
            items=self._images.enrich(page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def playlists(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Playlist]:
        needle = query.casefold()
        items = [
            playlist
            for playlist in self._user.saved_playlist_items()
            if needle in playlist.name.casefold()
            or needle in playlist.description.casefold()
        ]
        return PaginatedResult(
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            limit=limit,
        )

    def all(self, query: str) -> SearchResponse:
        tracks = self.tracks(query, limit=_AGGREGATE_LIMIT)
        albums = self.albums(query, limit=_AGGREGATE_LIMIT)
        artists = self.artists(query, limit=_AGGREGATE_LIMIT)
        playlists = self.playlists(query, limit=_AGGREGATE_LIMIT)
        return SearchResponse(
            albums=albums.items,
            artists=artists.items,
            playlists=playlists.items,
            tracks=tracks.items,
        )
