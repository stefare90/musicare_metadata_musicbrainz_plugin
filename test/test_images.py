"""Wikimedia URL building and the optional Wikidata image enrichment."""

from musicare_metadata_plugin_sdk import Artist, TransportError

from src.images import wikimedia
from src.images.wikidata import WikidataArtistImages

from ._stubs import StubClient

_FILE_URI = (
    "http://commons.wikimedia.org/wiki/Special:FilePath/"
    "RadioheadO2211125%20composite.jpg?width=300"
)


def _sparql_response(bindings):
    return {"results": {"bindings": bindings}}


def _binding(mbid, image):
    return {"mbid": {"value": mbid}, "image": {"value": image}}


def test_from_file_name_builds_the_four_sizes():
    images = wikimedia.from_file_name("Radiohead live.jpg")

    assert [image.width for image in images] == [56, 250, 500, 1000]
    assert [image.height for image in images] == [56, 250, 500, 1000]
    assert images[0].url == (
        "https://commons.wikimedia.org/wiki/Special:FilePath/Radiohead%20live.jpg?width=56"
    )


def test_from_image_uri_upgrades_http_and_keeps_the_encoding():
    images = wikimedia.from_image_uri(_FILE_URI)

    assert images[0].url == (
        "https://commons.wikimedia.org/wiki/Special:FilePath/"
        "RadioheadO2211125%20composite.jpg?width=56"
    )
    assert "%2520" not in images[0].url


def test_from_image_uri_ignores_anything_that_is_not_a_commons_file():
    assert wikimedia.from_image_uri("https://example.com/cover.jpg") == []
    assert wikimedia.from_image_uri("") == []
    assert wikimedia.from_file_name("") == []


def test_resolve_maps_bindings_and_caches_misses():
    client = StubClient(
        lambda url, params: _sparql_response([_binding("a1", _FILE_URI)])
    )
    images = WikidataArtistImages(client)

    first = images.resolve(["a1", "a2"])
    second = images.resolve(["a1", "a2"])

    assert first["a1"][0].width == 56
    assert first["a2"] == []
    # a1 was a hit and a2 a cached miss: the second resolve must not call Wikidata again.
    assert len(client.calls) == 1
    assert second["a2"] == []


def test_resolve_degrades_to_empty_when_sparql_is_unusable():
    client = StubClient(lambda url, params: None)

    assert WikidataArtistImages(client).resolve(["a1"]) == {"a1": []}


def test_a_transport_failure_is_not_cached_as_a_missing_image():
    calls = {"count": 0}

    def handler(url, params):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TransportError("budget exhausted")
        return _sparql_response([_binding("a1", _FILE_URI)])

    images = WikidataArtistImages(StubClient(handler))

    assert images.resolve(["a1"]) == {"a1": []}
    assert images.resolve(["a1"])["a1"][0].width == 56
    assert calls["count"] == 2


def test_resolve_ignores_bindings_without_an_image_uri():
    client = StubClient(
        lambda url, params: _sparql_response([_binding("a1", "https://example.com/x.jpg")])
    )

    assert WikidataArtistImages(client).resolve(["a1"]) == {"a1": []}


def test_enrich_preserves_every_other_field():
    client = StubClient(lambda url, params: _sparql_response([_binding("a1", _FILE_URI)]))
    images = WikidataArtistImages(client)
    original = Artist(
        id="a1",
        name="Radiohead",
        external_uri="https://musicbrainz.org/artist/a1",
        genres=["alternative rock"],
        followers=42,
    )

    enriched = images.enrich([original])

    assert enriched[0].id == "a1"
    assert enriched[0].name == "Radiohead"
    assert enriched[0].genres == ["alternative rock"]
    assert enriched[0].followers == 42
    assert len(enriched[0].images) == 4
