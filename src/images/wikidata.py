"""Artist images resolved from Wikidata.

A single bulk SPARQL query per page maps MusicBrainz artist ids (``P434``) to their
Commons image (``P18``), backed by a per-session cache that also remembers the misses
(negative cache), so the same artist is never queried twice.

This is *optional* enrichment: the endpoint is best-effort and must never fail the
response that carries it. The client's ``get_json_or_none`` absorbs provider and transport
failures, and the short timeout keeps a slow SPARQL endpoint from dominating the call.
"""

from typing import Dict, Iterable, List

from musicare_metadata_plugin_sdk import Artist, Image

from ..http import OPTIONAL_TIMEOUT, HttpClient
from ..providers import WIKIDATA_SPARQL
from . import wikimedia


class WikidataArtistImages:
    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self._cache: Dict[str, List[Image]] = {}

    def resolve(self, mbids: Iterable[str]) -> Dict[str, List[Image]]:
        result: Dict[str, List[Image]] = {}
        missing: List[str] = []
        for mbid in mbids:
            if mbid in self._cache:
                result[mbid] = self._cache[mbid]
            elif mbid not in missing:
                missing.append(mbid)

        if missing:
            fetched = self._fetch(missing)
            for mbid in missing:
                images = fetched.get(mbid, [])
                self._cache[mbid] = images
                result[mbid] = images
        return result

    def enrich(self, artists: List[Artist]) -> List[Artist]:
        """Return the artists with their resolved images, preserving every other field."""
        images_by_id = self.resolve(dict.fromkeys(artist.id for artist in artists))
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

    def _fetch(self, mbids: List[str]) -> Dict[str, List[Image]]:
        result: Dict[str, List[Image]] = {}
        values = " ".join(f'"{mbid}"' for mbid in mbids)
        query = (
            "SELECT ?mbid ?image WHERE { VALUES ?mbid { "
            f"{values}"
            " } ?artist wdt:P434 ?mbid . ?artist wdt:P18 ?image . }"
        )
        data = self._client.get_json_or_none(
            WIKIDATA_SPARQL,
            params={"query": query, "format": "json"},
            timeout=OPTIONAL_TIMEOUT,
        )
        if not isinstance(data, dict):
            return result
        bindings = (data.get("results") or {}).get("bindings")
        if not isinstance(bindings, list):
            return result

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
        return result
