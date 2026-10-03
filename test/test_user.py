"""``IUser``: saved tracks, the hidden album/artist playlists and the mutations."""

import pytest

from musicare_metadata_plugin_sdk import AuthRequiredError, NotFoundError, UnsupportedError

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.user import MusicBrainzUser

from ._fixtures import recording_payload, release_group_payload, release_payload
from ._stubs import StubClient, StubImages, authenticated_lb


def _user(tmp_path, handler, post_handler=None):
    client = StubClient(handler, post_handler)
    lb, credentials = authenticated_lb(client, tmp_path)
    return MusicBrainzUser(lb, client, StubImages()), client, credentials


def _posts(client, fragment):
    return [body for kind, url, body in client.calls if kind == "post" and fragment in url]


def test_saved_tracks_pages_the_feedback_and_resolves_the_page(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "get-feedback" in url:
            return {
                "feedback": [
                    {"recording_mbid": "r1"},
                    {"recording_mbid": "r2"},
                    {"recording_mbid": "r3"},
                ]
            }
        if url.endswith("/recording"):
            return {
                "recordings": [
                    recording_payload("r1", releases=[]),
                    recording_payload("r2", title="Two", releases=[]),
                ]
            }
        raise AssertionError(url)

    user, client, _ = _user(tmp_path, handler)

    page = user.saved_tracks(offset=0, limit=2)

    assert page.total == 3
    assert [track.id for track in page.items] == ["r1", "r2"]
    query = [params["query"] for kind, url, params in client.calls if url.endswith("/recording")][0]
    assert "rid:r1" in query and "rid:r2" in query and "rid:r3" not in query


def test_saved_tracks_prefers_the_release_from_the_feedback_metadata(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "get-feedback" in url:
            return {
                "feedback": [
                    {
                        "recording_mbid": "r1",
                        "track_metadata": {
                            "mbid_mapping": {"caa_release_mbid": "saved"}
                        },
                    }
                ]
            }
        if url.endswith("/recording"):
            return {
                "recordings": [
                    recording_payload(
                        "r1",
                        releases=[
                            release_payload(
                                release_id="studio",
                                group_id="studio-group",
                                date="2017-04-07",
                            ),
                            release_payload(
                                release_id="saved",
                                group_id="saved-group",
                                date="2019-10-25",
                            ),
                        ],
                    )
                ]
            }
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_tracks()

    assert [track.album.id for track in page.items] == ["rg:saved-group"]


def test_saved_tracks_without_feedback_metadata_uses_the_canonical_release(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "get-feedback" in url:
            return {"feedback": [{"recording_mbid": "r1", "track_metadata": None}]}
        if url.endswith("/recording"):
            return {
                "recordings": [
                    recording_payload(
                        "r1",
                        releases=[
                            release_payload(
                                release_id="comp",
                                group_id="comp-group",
                                date="2017",
                                secondary_types=["Compilation"],
                            ),
                            release_payload(
                                release_id="studio",
                                group_id="studio-group",
                                date="2017-04-07",
                            ),
                        ],
                    )
                ]
            }
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_tracks()

    assert [track.album.id for track in page.items] == ["rg:studio-group"]


def test_saved_tracks_requires_authentication(tmp_path):
    client = StubClient(lambda url, params: {"user_name": "tester"})
    lb = ListenBrainz(client, Credentials())
    user = MusicBrainzUser(lb, client, StubImages())

    with pytest.raises(AuthRequiredError):
        user.saved_tracks()


def test_saved_albums_reads_the_hidden_playlist(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_ALBUMS__",
                            "identifier": "https://listenbrainz.org/playlist/alb",
                        }
                    }
                ]
            }
        if "playlist/alb" in url:
            return {
                "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/g1"]}]}
            }
        if "release-group/g1" in url:
            return release_group_payload(group_id="g1")
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_albums()

    assert page.total == 1
    assert page.items[0].id == "rg:g1"


def test_saved_artists_creates_the_playlist_then_falls_back_to_stats(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {"playlists": []}
        if "playlist/new" in url:
            return {"playlist": {"track": []}}
        if "stats/user/tester/artists" in url:
            return {"payload": {"artists": [{"artist_name": "Radiohead", "artist_mbid": "a1"}]}}
        raise AssertionError(url)

    def post_handler(url, body):
        assert "playlist/create" in url
        return {"playlist_mbid": "new"}

    user, client, _ = _user(tmp_path, handler, post_handler)

    page = user.saved_artists()

    assert page.total == 1
    assert page.items[0].id == "a1"
    assert _posts(client, "playlist/create")


def test_saved_playlists_filters_the_hidden_playlists(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "My Mix",
                            "identifier": "https://listenbrainz.org/playlist/p1",
                            "creator": "tester",
                        }
                    },
                    {
                        "playlist": {
                            "title": "__GYAWUN_ARTISTS__",
                            "identifier": "https://listenbrainz.org/playlist/p2",
                        }
                    },
                ]
            }
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_playlists()

    assert page.total == 1
    assert page.items[0].id == "p1"


def test_save_and_unsave_track_submit_feedback(tmp_path):
    def post_handler(url, body):
        assert "recording-feedback" in url
        return {}

    user, client, _ = _user(tmp_path, lambda url, params: {}, post_handler)

    user.save_track("r1")
    user.unsave_track("r1")

    bodies = _posts(client, "recording-feedback")
    assert bodies == [
        {"recording_mbid": "r1", "score": 1},
        {"recording_mbid": "r1", "score": 0},
    ]


def test_save_album_adds_the_release_group_to_the_hidden_playlist(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {"playlists": []}
        if "playlist/alb" in url:
            return {"playlist": {"track": []}}
        if "release-group/g1" in url:
            return release_group_payload(group_id="g1", artist_name="Radiohead")
        raise AssertionError(url)

    def post_handler(url, body):
        if "playlist/create" in url:
            return {"playlist_mbid": "alb"}
        return {}

    user, client, _ = _user(tmp_path, handler, post_handler)

    user.save_album("rg:g1")

    track = _posts(client, "item/add")[0]["playlist"]["track"][0]
    assert track["identifier"] == "https://musicbrainz.org/recording/g1"
    assert track["title"] == "OK Computer"
    assert track["creator"] == "Radiohead"


def test_unsave_album_deletes_the_matching_indices_in_reverse(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_ALBUMS__",
                            "identifier": "https://listenbrainz.org/playlist/alb",
                        }
                    }
                ]
            }
        if "playlist/alb" in url:
            return {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/g1"]},
                        {"identifier": ["https://musicbrainz.org/recording/other"]},
                        {"identifier": ["https://musicbrainz.org/recording/g1"]},
                    ]
                }
            }
        raise AssertionError(url)

    user, client, _ = _user(tmp_path, handler, lambda url, body: {})

    user.unsave_album("rg:g1")

    assert _posts(client, "item/delete") == [
        {"index": 2, "count": 1},
        {"index": 0, "count": 1},
    ]


def test_save_playlist_adds_a_reference_to_the_hidden_playlist(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {"playlists": []}
        if "playlist/refs" in url:
            return {"playlist": {"track": []}}
        raise AssertionError(url)

    def post_handler(url, body):
        if "playlist/create" in url:
            assert body["playlist"]["title"] == "__GYAWUN_PLAYLISTS__"
            return {"playlist_mbid": "refs"}
        return {}

    user, client, _ = _user(tmp_path, handler, post_handler)

    user.save_playlist("p1")

    track = _posts(client, "item/add")[0]["playlist"]["track"][0]
    assert track["identifier"] == "https://musicbrainz.org/recording/p1"
    assert not any("playlist/p1/copy" in url for _, url, _ in client.calls)


def test_save_playlist_is_idempotent_when_already_referenced(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_PLAYLISTS__",
                            "identifier": "https://listenbrainz.org/playlist/refs",
                        }
                    }
                ]
            }
        if "playlist/refs" in url:
            return {
                "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/p1"]}]}
            }
        raise AssertionError(url)

    user, client, _ = _user(tmp_path, handler, lambda url, body: {})

    user.save_playlist("p1")

    assert _posts(client, "item/add") == []


def test_save_playlist_rejects_a_synthetic_radio(tmp_path):
    user, client, _ = _user(tmp_path, lambda url, params: {})

    with pytest.raises(UnsupportedError):
        user.save_playlist("radio:tag:chill")

    assert client.calls == []


def test_unsave_playlist_removes_the_reference(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_PLAYLISTS__",
                            "identifier": "https://listenbrainz.org/playlist/refs",
                        }
                    }
                ]
            }
        if "playlist/refs" in url:
            return {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/p1"]},
                        {"identifier": ["https://musicbrainz.org/recording/p2"]},
                    ]
                }
            }
        raise AssertionError(url)

    user, client, _ = _user(tmp_path, handler, lambda url, body: {})

    user.unsave_playlist("p1")

    assert _posts(client, "item/delete") == [{"index": 0, "count": 1}]
    assert not any("playlist/p1/delete" in url for _, url, _ in client.calls)


def test_saved_playlists_includes_referenced_playlists(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_PLAYLISTS__",
                            "identifier": "https://listenbrainz.org/playlist/refs",
                        }
                    }
                ]
            }
        if "playlist/refs" in url:
            return {
                "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/p9"]}]}
            }
        if "playlist/p9" in url:
            return {
                "playlist": {
                    "title": "Someone else's mix",
                    "annotation": "shared",
                    "creator": "bob",
                    "identifier": "https://listenbrainz.org/playlist/p9",
                }
            }
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_playlists()

    assert page.total == 1
    assert page.items[0].id == "p9"
    assert page.items[0].name == "Someone else's mix"
    assert page.items[0].owner.id == "bob"


def test_saved_playlists_skips_a_vanished_reference(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_PLAYLISTS__",
                            "identifier": "https://listenbrainz.org/playlist/refs",
                        }
                    }
                ]
            }
        if "playlist/refs" in url:
            return {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/gone"]},
                        {"identifier": ["https://musicbrainz.org/recording/here"]},
                    ]
                }
            }
        if "playlist/gone" in url:
            raise NotFoundError("gone")
        if "playlist/here" in url:
            return {
                "playlist": {
                    "title": "Still here",
                    "identifier": "https://listenbrainz.org/playlist/here",
                }
            }
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler)

    page = user.saved_playlists()

    assert [playlist.id for playlist in page.items] == ["here"]


def test_saved_playlists_without_a_token_does_not_expand_the_references(tmp_path):
    def handler(url, params):
        if "user/listenbrainz/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "Weekly Exploration",
                            "identifier": "https://listenbrainz.org/playlist/w1",
                            "creator": "listenbrainz",
                        }
                    }
                ]
            }
        raise AssertionError(url)

    client = StubClient(handler)
    user = MusicBrainzUser(ListenBrainz(client, Credentials()), client, StubImages())

    page = user.saved_playlists()

    assert [playlist.id for playlist in page.items] == ["w1"]
    assert all(kind == "get" for kind, _, _ in client.calls)


def _albums_library(handler_extra=None):
    """Handler for the albums playlist with rows ``g1`` and ``g2``."""

    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_ALBUMS__",
                            "identifier": "https://listenbrainz.org/playlist/alb",
                        }
                    }
                ]
            }
        if "playlist/alb" in url:
            return {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/g1"]},
                        {"identifier": ["https://musicbrainz.org/recording/g2"]},
                    ]
                }
            }
        if url.startswith("https://musicbrainz.org/ws/2/release-group/"):
            return release_group_payload(group_id=url.rsplit("/", 1)[-1])
        raise AssertionError(url)

    return handler


def test_save_album_is_idempotent_when_the_id_is_already_saved(tmp_path):
    user, client, _ = _user(tmp_path, _albums_library(), lambda url, body: {})

    user.save_album("rg:g1")

    assert _posts(client, "item/add") == []
    assert not any("release-group/g1" in url for kind, url, _ in client.calls)


def test_save_album_still_adds_an_unsaved_id(tmp_path):
    user, client, _ = _user(tmp_path, _albums_library(), lambda url, body: {})

    user.save_album("rg:g3")

    track = _posts(client, "item/add")[0]["playlist"]["track"][0]
    assert track["identifier"] == "https://musicbrainz.org/recording/g3"


def test_save_artist_is_idempotent_when_the_id_is_already_saved(tmp_path):
    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_ARTISTS__",
                            "identifier": "https://listenbrainz.org/playlist/art",
                        }
                    }
                ]
            }
        if "playlist/art" in url:
            return {
                "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/a1"]}]}
            }
        raise AssertionError(url)

    user, client, _ = _user(tmp_path, handler, lambda url, body: {})

    user.save_artist("a1")

    assert _posts(client, "item/add") == []
    assert not any("artist/a1" in url for kind, url, _ in client.calls)


def test_unsave_of_an_absent_id_is_silent(tmp_path):
    user, client, _ = _user(tmp_path, _albums_library(), lambda url, body: {})

    user.unsave_album("rg:absent")

    assert _posts(client, "item/delete") == []


def test_saved_albums_does_not_collapse_legacy_duplicate_rows(tmp_path):
    """The write side keeps the playlist unique; reads return it as-is, on purpose.

    A library saved before the idempotent ``save_*`` may still hold duplicate rows:
    those are a manual cleanup, not something the read path hides.
    """

    def handler(url, params):
        if "validate-token" in url:
            return {"user_name": "tester"}
        if "user/tester/playlists" in url:
            return {
                "playlists": [
                    {
                        "playlist": {
                            "title": "__GYAWUN_ALBUMS__",
                            "identifier": "https://listenbrainz.org/playlist/alb",
                        }
                    }
                ]
            }
        if "playlist/alb" in url:
            return {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/g1"]},
                        {"identifier": ["https://musicbrainz.org/recording/g1"]},
                    ]
                }
            }
        if url.startswith("https://musicbrainz.org/ws/2/release-group/"):
            return release_group_payload(group_id="g1")
        raise AssertionError(url)

    user, _, _ = _user(tmp_path, handler, lambda url, body: {})

    page = user.saved_albums()

    assert page.total == 2
    assert [album.id for album in page.items] == ["rg:g1", "rg:g1"]
