"""``ISearch``: text search over MusicBrainz.

Recordings become tracks, release groups become albums, artists are enriched with their
Wikidata image. ListenBrainz has no public playlist search, so ``playlists`` filters the
user's own saved playlists locally; without a token the public ``listenbrainz`` account is
used, which surfaces its curated playlists.
"""

from typing import Any, List
import re

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

from ..net import HttpClient
from ..images.wikidata import WikidataArtistImages
from ..shared.mapping import build_album_from_release_group, build_artist, build_track
from ..shared.providers import MUSICBRAINZ_API

_AGGREGATE_LIMIT = 5
_ALL_TRACKS_LIMIT = 10
_POOL_SIZE = 100
_POOL_MAX_QUERIES = 16
_TITLE_BOOST = 4

_LUCENE_SPECIALS = set('+-=><!(){}[]^"~*?:\\/&|')


def _escape_lucene(term: str) -> str:
    return "".join(f"\\{char}" if char in _LUCENE_SPECIALS else char for char in term)


def _track_query(query: str) -> str:
    terms = [_escape_lucene(term) for term in query.split()]
    if not terms:
        return query
    if len(terms) == 1:
        return f"recording:({terms[0]})^{_TITLE_BOOST} OR artist:({terms[0]})"
    anded = " AND ".join(terms)
    return f"recording:({anded})^{_TITLE_BOOST} OR ({anded})"


_WORDS = re.compile(r"\w+")


def _tier(title: str, artists: List[str], query: str) -> int:
    normalized_title = " ".join(title.casefold().split())
    normalized_query = " ".join(query.casefold().split())
    if normalized_title == normalized_query:
        return 0
    if normalized_query and normalized_query in normalized_title:
        return 1
    query_words = set(_WORDS.findall(query.casefold()))
    if not query_words:
        return 3
    title_words = set(_WORDS.findall(title.casefold()))
    artist_words = set()
    for artist in artists:
        artist_words.update(_WORDS.findall(artist.casefold()))
    if title_words & query_words and query_words <= title_words | artist_words:
        return 2
    return 3


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
        self._pools = {}

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

    def _reranked(self, data: Any, query: str, offset: int, limit: int):
        page = _page(data, "recordings", build_track, offset, limit)
        items = sorted(
            page.items,
            key=lambda track: _tier(track.name, [artist.name for artist in track.artists], query),
        )
        return PaginatedResult(items=items, total=page.total, offset=offset, limit=limit)

    def _fetch(self, query: str, offset: int, limit: int):
        weighted = _track_query(query)
        data = self._search("recording", weighted, offset, limit)
        if isinstance(data, dict) and data.get("count") == 0 and len(query.split()) > 1:
            return self._search("recording", query, offset, limit), query
        return data, weighted

    def _build_pool(self, query: str):
        data, _ = self._fetch(query, 0, _POOL_SIZE)
        page = self._reranked(data, query, 0, _POOL_SIZE)
        if len(self._pools) >= _POOL_MAX_QUERIES and query not in self._pools:
            self._pools.pop(next(iter(self._pools)))
        self._pools[query] = [[*page.items], page.total]
        return self._pools[query]

    def tracks(self, query: str, offset: int = 0, limit: int = 20) -> PaginatedResult[Track]:
        pool = self._pools.get(query)
        if pool is None and offset == 0:
            pool = self._build_pool(query)
        if pool is not None and (
            offset + limit <= len(pool[0]) or len(pool[0]) >= pool[1] or not pool[0]
        ):
            items, total = pool
            return PaginatedResult(
                items=items[offset : offset + limit], total=total, offset=offset, limit=limit
            )
        data, _ = self._fetch(query, offset, limit)
        return self._reranked(data, query, offset, limit)

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
        tracks = self.tracks(query, limit=_POOL_SIZE)
        albums = self.albums(query, limit=_AGGREGATE_LIMIT)
        artists = self.artists(query, limit=_AGGREGATE_LIMIT)
        playlists = self.playlists(query, limit=_AGGREGATE_LIMIT)
        return SearchResponse(
            albums=albums.items,
            artists=artists.items,
            playlists=playlists.items,
            tracks=tracks.items[:_ALL_TRACKS_LIMIT],
        )
