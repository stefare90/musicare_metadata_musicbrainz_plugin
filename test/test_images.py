"""Wikimedia URL building and the Wikidata image enrichment (no network)."""

import pytest

from musicare_metadata_plugin_sdk import Artist, TransportError

from src.images import wikimedia
from src.images.wikidata import WikidataArtistImages

from ._stubs import StubClient


def _claim(value):
    return {"rank": "normal", "mainsnak": {"datavalue": {"value": value}}}


class FakeWikidata:
    """A tiny Wikidata: ``mbid -> (qid or None, file name or None)``."""

    def __init__(self, entities):
        self.entities = entities
        self.calls = []
        self.fail_action = None

    def handler(self, url, params):
        self.calls.append(params)
        action = params.get("action")
        if action == "query":
            term = params["srsearch"].split(":", 1)[1]
            results = [
                {"title": self.entities[mbid][0]}
                for mbid in (part.split("=", 1)[1] for part in term.split("|"))
                if self.entities.get(mbid, (None, None))[0]
            ]
            return {"query": {"search": results}}
        if action == "wbgetentities":
            if self.fail_action == action:
                raise TransportError("wikidata is unavailable")
            entities = {}
            for qid in params["ids"].split("|"):
                for mbid, (entity_qid, file_name) in self.entities.items():
                    if entity_qid != qid:
                        continue
                    claims = {"P434": [_claim(mbid)]}
                    if file_name is not None:
                        claims["P18"] = [_claim(file_name)]
                    entities[qid] = {"claims": claims}
                    break
            return {"entities": entities}
        raise AssertionError(f"unexpected action: {action}")


def _stack(entities):
    fake = FakeWikidata(entities)
    return WikidataArtistImages(StubClient(fake.handler)), fake


def test_from_file_name_builds_the_four_sizes():
    images = wikimedia.from_file_name("Radiohead live.jpg")

    assert [image.width for image in images] == [56, 250, 500, 1000]
    assert images[0].url == (
        "https://commons.wikimedia.org/wiki/Special:FilePath/Radiohead%20live.jpg?width=56"
    )


def test_from_file_name_of_nothing_is_empty():
    assert wikimedia.from_file_name("") == []


def test_resolve_maps_a_musicbrainz_id_to_its_commons_images():
    images, _ = _stack({"a1": ("Q44190", "Radiohead.jpg")})

    resolved = images.resolve(["a1"])

    assert [image.width for image in resolved["a1"]] == [56, 250, 500, 1000]
    assert resolved["a1"][0].url.startswith("https://commons.wikimedia.org/")


def test_an_artist_without_an_image_is_an_empty_list_not_an_error():
    images, fake = _stack({"a1": ("Q1", None)})

    assert images.resolve(["a1"]) == {"a1": []}
    # A genuine miss is cached: the second resolve must not call Wikidata again.
    images.resolve(["a1"])
    assert len(fake.calls) == 2


def test_a_musicbrainz_id_without_a_wikidata_entity_is_a_cached_miss():
    images, fake = _stack({})

    assert images.resolve(["unknown"]) == {"unknown": []}
    assert len(fake.calls) == 1


def test_a_failed_request_is_raised_and_not_cached():
    images, fake = _stack({"a1": ("Q1", "A.jpg")})
    fake.fail_action = "wbgetentities"

    with pytest.raises(TransportError):
        images.resolve(["a1"])

    fake.fail_action = None
    assert images.resolve(["a1"])["a1"][0].width == 56


def test_enrich_preserves_every_other_field():
    images, _ = _stack({"a1": ("Q1", "A.jpg")})
    original = Artist(
        id="a1",
        name="Radiohead",
        external_uri="https://musicbrainz.org/artist/a1",
        genres=["alternative rock"],
        followers=42,
    )

    enriched = images.enrich([original])

    assert enriched[0].name == "Radiohead"
    assert enriched[0].genres == ["alternative rock"]
    assert enriched[0].followers == 42
    assert len(enriched[0].images) == 4


def test_both_requests_are_chunked():
    entities = {f"m{i}": (f"Q{i}", f"F{i}.jpg") for i in range(60)}
    images, fake = _stack(entities)

    resolved = images.resolve(list(entities))

    assert len(resolved) == 60
    assert all(resolved[mbid] for mbid in entities)
    searches = [call for call in fake.calls if call["action"] == "query"]
    entities_calls = [call for call in fake.calls if call["action"] == "wbgetentities"]
    assert len(searches) == 3  # 60 ids / 20 per query
    assert len(entities_calls) == 2  # 60 qids / 50 per request


def test_a_deprecated_claim_is_ignored():
    image = WikidataArtistImages._claim_value(
        {"claims": {"P18": [{"rank": "deprecated", "mainsnak": {"datavalue": {"value": "X"}}}]}},
        "P18",
    )

    assert image is None
