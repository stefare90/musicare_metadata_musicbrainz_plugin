"""``ISearch`` against canned MusicBrainz payloads (no network)."""

from musicare_metadata_plugin_sdk import Artist, Image, SearchCategory

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.search import MusicBrainzSearch
from src.segments.user import MusicBrainzUser

from ._fixtures import (
    ARTIST_MBID,
    artist_payload,
    recording_payload,
    release_group_payload,
)
from ._stubs import StubClient, StubImages, router


def _playlist(title, identifier, annotation=""):
    return {
        "playlist": {
            "title": title,
            "identifier": identifier,
            "annotation": annotation,
        }
    }


def _search(handler=None, images=None, post_handler=None):
    """A search wired to an anonymous user, so the ListenBrainz calls are mocked too."""
    client = StubClient(handler or router({}), post_handler)
    resolved_images = images or StubImages()
    user = MusicBrainzUser(ListenBrainz(client, Credentials()), client, resolved_images)
    return MusicBrainzSearch(client, resolved_images, user), client


def test_chips_declare_the_four_supported_categories():
    search, _ = _search()

    assert search.chips() == [
        SearchCategory.TRACKS,
        SearchCategory.ALBUMS,
        SearchCategory.ARTISTS,
        SearchCategory.PLAYLISTS,
    ]


def test_tracks_reads_recordings_and_the_total_count():
    search, client = _search(
        router({"recording": {"count": 42, "recordings": [recording_payload(releases=[])]}})
    )

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
    search, _ = _search(
        router({"release-group": {"count": 7, "release-groups": [release_group_payload()]}})
    )

    page = search.albums("radiohead")

    assert page.total == 7
    assert page.items[0].id == "rg:group-1"


def test_artists_are_enriched_with_images():
    images = StubImages({ARTIST_MBID: [Image(url="https://img", width=56, height=56)]})
    search, _ = _search(
        router({"artist": {"count": 1, "artists": [artist_payload(tags=["rock"])]}}),
        images=images,
    )

    page = search.artists("radiohead")

    assert page.items[0].genres == ["rock"]
    assert page.items[0].images[0].url == "https://img"


def test_playlists_filter_on_name_and_description_case_insensitively():
    playlists = [
        _playlist("Late Night Mix", "https://listenbrainz.org/playlist/p1", "Calm songs"),
        _playlist("Workout", "https://listenbrainz.org/playlist/p2", "Energetic Beats"),
        _playlist("Focus", "https://listenbrainz.org/playlist/p3", "Solo piano"),
    ]
    search, _ = _search(router({"user/listenbrainz/playlists": {"playlists": playlists}}))

    by_name = search.playlists("NIGHT")
    assert [playlist.id for playlist in by_name.items] == ["p1"]

    by_description = search.playlists("beats")
    assert [playlist.id for playlist in by_description.items] == ["p2"]


def test_playlists_paginate_after_filtering_and_report_the_filtered_total():
    playlists = [
        _playlist(f"Jazz {index}", f"https://listenbrainz.org/playlist/p{index}")
        for index in range(7)
    ] + [_playlist("Rock", "https://listenbrainz.org/playlist/rock")]
    search, _ = _search(router({"user/listenbrainz/playlists": {"playlists": playlists}}))

    page = search.playlists("jazz", offset=2, limit=3)

    assert [playlist.name for playlist in page.items] == ["Jazz 2", "Jazz 3", "Jazz 4"]
    assert page.total == 7
    assert (page.offset, page.limit) == (2, 3)


def test_playlists_exclude_the_internal_playlists():
    playlists = [
        _playlist("Public Mix", "https://listenbrainz.org/playlist/p1"),
        _playlist("__GYAWUN_ALBUMS__", "https://listenbrainz.org/playlist/p2"),
        _playlist("__GYAWUN_ARTISTS__", "https://listenbrainz.org/playlist/p3"),
    ]
    search, _ = _search(router({"user/listenbrainz/playlists": {"playlists": playlists}}))

    page = search.playlists("")

    assert [playlist.id for playlist in page.items] == ["p1"]


def test_playlists_without_a_token_use_the_public_account():
    search, client = _search(router({"user/listenbrainz/playlists": {"playlists": []}}))

    search.playlists("radiohead")

    urls = [url for kind, url, _ in client.calls if kind == "get"]
    assert any("user/listenbrainz/playlists" in url for url in urls)
    assert not any("validate-token" in url for url in urls)


def test_all_includes_up_to_five_playlists():
    playlists = [
        _playlist(f"Weekly {index}", f"https://listenbrainz.org/playlist/p{index}")
        for index in range(7)
    ]
    search, client = _search(
        router(
            {
                "recording": {"count": 1, "recordings": [recording_payload(releases=[])]},
                "release-group": {"count": 1, "release-groups": [release_group_payload()]},
                "artist": {"count": 1, "artists": [artist_payload()]},
                "user/listenbrainz/playlists": {"playlists": playlists},
            }
        )
    )

    response = search.all("weekly")

    assert [track.id for track in response.tracks] == ["recording-1"]
    assert [album.id for album in response.albums] == ["rg:group-1"]
    assert [artist.id for artist in response.artists] == [ARTIST_MBID]
    assert len(response.playlists) == 5
    assert [call[2]["limit"] for call in client.calls if isinstance(call[2], dict)] == [5, 5, 5]
