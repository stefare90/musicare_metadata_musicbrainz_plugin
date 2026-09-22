"""Artist images resolved from Wikidata.

A single bulk SPARQL query per page maps MusicBrainz artist ids (``P434``) to their
Commons image (``P18``), backed by a per-session cache that also remembers the misses
(negative cache), so the same artist is never queried twice.

This is *optional* enrichment: the endpoint is best-effort and must never fail the
response that carries it. The client's ``get_json_or_none`` absorbs provider and transport
failures, and the short timeout keeps a slow SPARQL endpoint from dominating the call.
"""

from typing import Dict, Iterable, List

from musicare_metadata_plugin_sdk import Artist, Image, MetadataPluginError

from ..http import HttpClient
from ..providers import WIKIDATA_SPARQL
from . import wikimedia

# Wikidata answers a single-artist P434/P18 query in ~5 s and a page-sized batch in tens of
# seconds, so the enrichment is budgeted per call site: the search page must stay snappy
# (images are best-effort there), while a detail page can wait for the image it needs.
SEARCH_TIMEOUT = 3.0
DETAIL_TIMEOUT = 6.0


class WikidataArtistImages:
    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self._cache: Dict[str, List[Image]] = {}

    def resolve(
        self, mbids: Iterable[str], timeout: float = DETAIL_TIMEOUT
    ) -> Dict[str, List[Image]]:
        result: Dict[str, List[Image]] = {}
        missing: List[str] = []
        for mbid in mbids:
            if mbid in self._cache:
                result[mbid] = self._cache[mbid]
            elif mbid not in missing:
                missing.append(mbid)

        if missing:
            fetched, complete = self._fetch(missing, timeout)
            for mbid in missing:
                if mbid in fetched:
                    self._cache[mbid] = fetched[mbid]
                elif complete:
                    self._cache[mbid] = []
                result[mbid] = fetched.get(mbid, [])
        return result

    def enrich(
        self, artists: List[Artist], timeout: float = DETAIL_TIMEOUT
    ) -> List[Artist]:
        """Return the artists with their resolved images, preserving every other field."""
        images_by_id = self.resolve(
            dict.fromkeys(artist.id for artist in artists), timeout=timeout
        )
        return [
            Artist(
                id=artist.id,
                name=artist.name,
                external_uri=artist.external_uri,
                images=images_by_id.get(artist.id, []),
                genres=artist.genres,
                followers=artist.followers,
            )
            for artist in artists
        ]

    def _fetch(self, mbids: List[str], timeout: float):
        """Return ``(images_by_mbid, complete)``.

        ``complete`` is ``False`` when the provider could not be reached: the misses are
        then *not* cached, because a timeout is not an artist without an image. Otherwise
        an id absent from the response is a genuine miss and is cached as empty.
        """
        result: Dict[str, List[Image]] = {}
        values = " ".join(f'"{mbid}"' for mbid in mbids)
        query = (
            "SELECT ?mbid ?image WHERE { VALUES ?mbid { "
            f"{values}"
            " } ?artist wdt:P434 ?mbid . ?artist wdt:P18 ?image . }"
        )
        try:
            data = self._client.get_json(
                WIKIDATA_SPARQL,
                params={"query": query, "format": "json"},
                timeout=timeout,
            )
        except MetadataPluginError:
            return result, False
        if not isinstance(data, dict):
            return result, True
        bindings = (data.get("results") or {}).get("bindings")
        if not isinstance(bindings, list):
            return result, True

        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            mbid = (binding.get("mbid") or {}).get("value")
            image = (binding.get("image") or {}).get("value")
            if not isinstance(mbid, str) or not isinstance(image, str) or mbid in result:
                continue
            images = wikimedia.from_image_uri(image)
            if images:
                result[mbid] = images
        return result, True
