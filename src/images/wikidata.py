"""Artist images resolved through the Wikidata MediaWiki API.

Two calls resolve a whole page of artists:

1. a CirrusSearch query maps MusicBrainz artist ids (``P434``) to Wikidata entities, with
   the ``haswbstatement:P434=a|P434=b|…`` syntax accepting several ids at once;
2. a ``wbgetentities`` request reads the claims of those entities, where ``P434`` maps a
   claim back to its MusicBrainz id and ``P18`` is the Commons image.

The SPARQL query service was measured at 5–30 s for the same work (and timed out on
trivial queries), while this API answers in ~0.3–1 s, so the enrichment is no longer the
slowest part of a response.

Failure policy (deliberate): **"the artist has no image" and "the image could not be
fetched" are different outcomes**. An entity without ``P434``/``P18`` is a genuine miss —
an empty image list, cached so it is not queried again. A failed request *is* an error and
is raised as the retryable SDK error (``transport_error``/``rate_limited``), so the host
can retry; it is never cached.
"""

from typing import Any, Dict, Iterable, List, Optional

from musicare_metadata_plugin_sdk import Artist, Image

from ..http import HttpClient
from ..providers import WIKIDATA_API
from . import wikimedia

IMAGES_TIMEOUT = 5.0
# Bounds the query string of the search request and the id list of the claims request.
_SEARCH_CHUNK = 20
_ENTITIES_CHUNK = 50
_DEPRECATED_RANK = "deprecated"


class WikidataArtistImages:
    def __init__(self, client: HttpClient) -> None:
        self._client = client
        self._cache: Dict[str, List[Image]] = {}

    def resolve(
        self, mbids: Iterable[str], timeout: float = IMAGES_TIMEOUT
    ) -> Dict[str, List[Image]]:
        result: Dict[str, List[Image]] = {}
        missing: List[str] = []
        for mbid in mbids:
            if mbid in self._cache:
                result[mbid] = self._cache[mbid]
            elif mbid not in missing:
                missing.append(mbid)

        if missing:
            resolved = self._resolve_missing(missing, timeout)
            for mbid in missing:
                images = resolved.get(mbid, [])
                self._cache[mbid] = images
                result[mbid] = images
        return result

    def enrich(
        self, artists: List[Artist], timeout: float = IMAGES_TIMEOUT
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

    def _resolve_missing(self, mbids: List[str], timeout: float) -> Dict[str, List[Image]]:
        qids = self._search_qids(mbids, timeout)
        if not qids:
            return {}
        return self._fetch_images(qids, timeout)

    def _search_qids(self, mbids: List[str], timeout: float) -> List[str]:
        qids: List[str] = []
        for start in range(0, len(mbids), _SEARCH_CHUNK):
            chunk = mbids[start : start + _SEARCH_CHUNK]
            term = "|".join(f"P434={mbid}" for mbid in chunk)
            data = self._client.get_json(
                WIKIDATA_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": f"haswbstatement:{term}",
                    "srlimit": len(chunk),
                    "format": "json",
                },
                timeout=timeout,
            )
            search = (data.get("query") or {}).get("search") if isinstance(data, dict) else None
            for entry in search or []:
                if not isinstance(entry, dict):
                    continue
                qid = entry.get("title")
                if isinstance(qid, str) and qid and qid not in qids:
                    qids.append(qid)
        return qids

    def _fetch_images(self, qids: List[str], timeout: float) -> Dict[str, List[Image]]:
        images_by_mbid: Dict[str, List[Image]] = {}
        for start in range(0, len(qids), _ENTITIES_CHUNK):
            chunk = qids[start : start + _ENTITIES_CHUNK]
            data = self._client.get_json(
                WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "claims",
                    "format": "json",
                },
                timeout=timeout,
            )
            entities = data.get("entities") if isinstance(data, dict) else None
            if not isinstance(entities, dict):
                continue
            for entity in entities.values():
                mbid = self._claim_value(entity, "P434")
                file_name = self._claim_value(entity, "P18")
                if mbid and file_name:
                    images = wikimedia.from_file_name(file_name)
                    if images:
                        images_by_mbid[mbid] = images
        return images_by_mbid

    @staticmethod
    def _claim_value(entity: Any, prop: str) -> Optional[str]:
        if not isinstance(entity, dict):
            return None
        claims = entity.get("claims")
        if not isinstance(claims, dict):
            return None
        for claim in claims.get(prop) or []:
            if not isinstance(claim, dict) or claim.get("rank") == _DEPRECATED_RANK:
                continue
            snak = claim.get("mainsnak")
            datavalue = snak.get("datavalue") if isinstance(snak, dict) else None
            value = datavalue.get("value") if isinstance(datavalue, dict) else None
            if isinstance(value, str) and value:
                return value
        return None
