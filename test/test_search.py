"""``ISearch`` against canned MusicBrainz payloads (no network)."""

from musicare_metadata_plugin_sdk import Artist, Image, SearchCategory

from src.segments.search import MusicBrainzSearch

from ._fixtures import (
    ARTIST_MBID,
    artist_payload,
    recording_payload,
    release_group_payload,
)
from ._stubs import StubClient, StubImages, router


def test_chips_declare_the_three_supported_categories():
    search = MusicBrainzSearch(StubClient(router({})), StubImages())

    assert search.chips() == [
        SearchCategory.TRACKS,
        SearchCategory.ALBUMS,
        SearchCategory.ARTISTS,
    ]


def test_tracks_reads_recordings_and_the_total_count():
    client = StubClient(
        router({"recording": {"count": 42, "recordings": [recording_payload(releases=[])]}})
    )
    search = MusicBrainzSearch(client, StubImages())

    page = search.tracks("radiohead", offset=5, limit=10)

    assert page.total == 42
    assert page.offset == 5
    assert page.limit == 10
    assert [track.id for track in page.items] == ["recording-1"]
    assert client.calls[0][2] == {
        "query": "radiohead",
        "limit": 10,
        "offset": 5,
        "fmt": "json",
    }


def test_albums_read_release_groups():
    client = StubClient(
        router({"release-group": {"count": 7, "release-groups": [release_group_payload()]}})
    )
    search = MusicBrainzSearch(client, StubImages())

    page = search.albums("radiohead")

    assert page.total == 7
    assert page.items[0].id == "rg:group-1"


def test_artists_are_enriched_with_images():
    client = StubClient(router({"artist": {"count": 1, "artists": [artist_payload(tags=["rock"])]}}))
    images = StubImages({ARTIST_MBID: [Image(url="https://img", width=56, height=56)]})
    search = MusicBrainzSearch(client, images)

    page = search.artists("radiohead")

    assert page.items[0].genres == ["rock"]
    assert page.items[0].images[0].url == "https://img"


def test_playlists_are_an_empty_page():
    search = MusicBrainzSearch(StubClient(router({})), StubImages())

    page = search.playlists("radiohead", offset=3, limit=4)

    assert page.items == []
    assert (page.total, page.offset, page.limit) == (0, 3, 4)


def test_all_aggregates_five_of_each_without_playlists():
    client = StubClient(
        router(
            {
                "recording": {"count": 1, "recordings": [recording_payload(releases=[])]},
                "release-group": {"count": 1, "release-groups": [release_group_payload()]},
                "artist": {"count": 1, "artists": [artist_payload()]},
            }
        )
    )
    search = MusicBrainzSearch(client, StubImages())

    response = search.all("radiohead")

    assert [track.id for track in response.tracks] == ["recording-1"]
    assert [album.id for album in response.albums] == ["rg:group-1"]
    assert [artist.id for artist in response.artists] == [ARTIST_MBID]
    assert response.playlists == []
    assert [call[2]["limit"] for call in client.calls] == [5, 5, 5]
