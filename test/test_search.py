"""``ISearch`` against canned MusicBrainz payloads (no network)."""

from musicare_metadata_plugin_sdk import Artist, Image, SearchCategory

from src.service import Credentials
from src.service import ListenBrainz
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


def test_tracks_boost_the_title_over_the_artist():
    search, client = _search(
        router({"recording": {"count": 1, "recordings": [recording_payload(releases=[])]}})
    )

    page = search.tracks("radiohead", offset=0, limit=10)

    assert page.total == 1
    assert page.offset == 0
    assert page.limit == 10
    assert [track.id for track in page.items] == ["recording-1"]
    assert client.calls[0][2] == {
        "query": "recording:(radiohead)^4 OR artist:(radiohead)",
        "limit": 100,
        "offset": 0,
        "fmt": "json",
    }


def test_tracks_join_multiple_words_with_and_and_still_boost_the_title():
    search, client = _search(
        router({"recording": {"count": 2, "recordings": [recording_payload(releases=[])]}})
    )

    search.tracks("Vivo Fabri Fibra")

    assert client.calls[0][2]["query"] == (
        "recording:(Vivo AND Fabri AND Fibra)^4 OR (Vivo AND Fabri AND Fibra)"
    )


def test_tracks_escape_lucene_special_chars_before_building_the_query():
    search, client = _search(router({"recording": {"count": 0, "recordings": []}}))

    search.tracks("AC/DC")

    assert client.calls[0][2]["query"] == r"recording:(AC\/DC)^4 OR artist:(AC\/DC)"


def test_tracks_fall_back_to_the_raw_query_when_the_weighted_one_is_empty():
    calls = []

    def handler(url, params):
        calls.append(params["query"])
        if len(calls) == 1:
            return {"count": 0, "recordings": []}
        return {"count": 1, "recordings": [recording_payload(releases=[])]}

    search, _ = _search(handler)

    page = search.tracks("Vivo Fabri Fibra")

    assert calls == [
        "recording:(Vivo AND Fabri AND Fibra)^4 OR (Vivo AND Fabri AND Fibra)",
        "Vivo Fabri Fibra",
    ]
    assert [track.id for track in page.items] == ["recording-1"]


def test_tracks_do_not_fall_back_for_a_single_word_or_a_non_empty_result():
    search, client = _search(
        router({"recording": {"count": 0, "recordings": []}})
    )
    search.tracks("radiohead")
    assert len(client.calls) == 1

    search, client = _search(
        router({"recording": {"count": 3, "recordings": []}})
    )
    search.tracks("Vivo Fabri Fibra")
    assert len(client.calls) == 1


def test_tracks_leave_an_empty_query_untouched():
    search, client = _search(router({"recording": {"count": 0, "recordings": []}}))

    search.tracks("")

    assert client.calls[0][2]["query"] == ""


def _vivo_page(*tunes):
    return {
        "count": len(tunes),
        "recordings": [
            recording_payload(recording_id=f"r{i}", title=title, artist_name=artist)
            for i, (title, artist) in enumerate(tunes)
        ],
    }


def test_tracks_rank_the_exact_title_first():
    search, _ = _search(
        router(
            {
                "recording": _vivo_page(
                    ("Pescao vivo", "Pescao Vivo"),
                    ("Vivo, vivo", "Los Especialistas"),
                    ("Vivo", "Luca Barbarossa"),
                    ("Respire", "Vivo"),
                )
            }
        )
    )

    page = search.tracks("vivo")

    assert [(track.name, track.artists[0].name) for track in page.items] == [
        ("Vivo", "Luca Barbarossa"),
        ("Pescao vivo", "Pescao Vivo"),
        ("Vivo, vivo", "Los Especialistas"),
        ("Respire", "Vivo"),
    ]


def test_tracks_rank_a_split_title_artist_match_before_a_partial_one():
    search, _ = _search(
        router(
            {
                "recording": _vivo_page(
                    ("Story: Fabri Fibra", "Fabri Fibra"),
                    ("Vivo", "Fabri Fibra"),
                )
            }
        )
    )

    page = search.tracks("Vivo Fabri Fibra")

    assert [(track.name, track.artists[0].name) for track in page.items] == [
        ("Vivo", "Fabri Fibra"),
        ("Story: Fabri Fibra", "Fabri Fibra"),
    ]


def test_tracks_keep_the_provider_order_inside_a_tier():
    search, _ = _search(
        router(
            {
                "recording": _vivo_page(
                    ("Vivo", "Luca Barbarossa"),
                    ("Vivo", "Renato Zero"),
                )
            }
        )
    )

    page = search.tracks("vivo")

    assert [track.artists[0].name for track in page.items] == [
        "Luca Barbarossa",
        "Renato Zero",
    ]


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


def test_all_fetches_deeper_and_returns_the_reranked_top_ten():
    search, client = _search(
        router(
            {
                "recording": _vivo_page(
                    ("Pescao vivo", "Pescao Vivo"),
                    ("Vivo, vivo", "Los Especialistas"),
                    ("Vivo", "Luca Barbarossa"),
                    ("Vivo", "Renato Zero"),
                    ("Vivo", "Gustavo Cerati"),
                    ("Vivo", "Piero Pelù"),
                    ("Respire", "Vivo"),
                ),
                "release-group": {"count": 1, "release-groups": [release_group_payload()]},
                "artist": {"count": 1, "artists": [artist_payload()]},
                "user/listenbrainz/playlists": {"playlists": []},
            }
        )
    )

    response = search.all("vivo")

    assert [(track.name, track.artists[0].name) for track in response.tracks] == [
        ("Vivo", "Luca Barbarossa"),
        ("Vivo", "Renato Zero"),
        ("Vivo", "Gustavo Cerati"),
        ("Vivo", "Piero Pelù"),
        ("Pescao vivo", "Pescao Vivo"),
        ("Vivo, vivo", "Los Especialistas"),
        ("Respire", "Vivo"),
    ]
    recording_limits = [
        call[2]["limit"] for call in client.calls if call[1].endswith("recording")
    ]
    assert recording_limits == [100]


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
    assert [call[2]["limit"] for call in client.calls if isinstance(call[2], dict)] == [100, 5, 5]


def test_tracks_serve_later_pages_from_the_same_pool():
    search, client = _search(
        router(
            {
                "recording": _vivo_page(
                    ("Pescao vivo", "Pescao Vivo"),
                    ("Vivo, vivo", "Los Especialistas"),
                    ("Vivo", "Luca Barbarossa"),
                    ("Vivo", "Renato Zero"),
                    ("Respire", "Vivo"),
                )
            }
        )
    )

    first = search.tracks("vivo", offset=0, limit=2)
    second = search.tracks("vivo", offset=2, limit=2)

    assert [track.name for track in first.items] == ["Vivo", "Vivo"]
    assert [(track.name, track.artists[0].name) for track in second.items] == [
        ("Pescao vivo", "Pescao Vivo"),
        ("Vivo, vivo", "Los Especialistas"),
    ]
    assert len(client.calls) == 1


def test_tracks_beyond_the_pool_serve_the_provider_page():
    first = _vivo_page(
        ("Pescao vivo", "Pescao Vivo"),
        ("Vivo, vivo", "Los Especialistas"),
        ("Vivo", "Luca Barbarossa"),
    )
    first["count"] = 5
    second = _vivo_page(
        ("Respire", "Vivo"),
        ("Vivo", "Renato Zero"),
    )
    second["count"] = 5

    def handler(url, params):
        return second if params["offset"] >= 3 else first

    search, client = _search(handler)

    page = search.tracks("vivo", offset=3, limit=2)

    assert [(track.name, track.artists[0].name) for track in page.items] == [
        ("Vivo", "Renato Zero"),
        ("Respire", "Vivo"),
    ]
    assert [call[2]["offset"] for call in client.calls] == [3]


def test_tracks_beyond_the_last_result_serve_an_empty_page():
    payload = {"count": 500, "recordings": [recording_payload(releases=[])]}

    def handler(url, params):
        if params["offset"] >= 1:
            return {"count": 500, "recordings": []}
        return payload

    search, client = _search(handler)

    page = search.tracks("vivo", offset=100, limit=20)

    assert page.items == []
    assert page.total == 500
    assert len(client.calls) == 1
