"""``IUser``: saved tracks, the hidden album/artist playlists and the mutations."""

import pytest

from musicare_metadata_plugin_sdk import AuthRequiredError

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.user import MusicBrainzUser

from ._fixtures import recording_payload, release_group_payload
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


def test_save_and_unsave_playlist_copy_then_delete(tmp_path):
    user, client, _ = _user(tmp_path, lambda url, params: {}, lambda url, body: {})

    user.save_playlist("p1")
    user.unsave_playlist("p1")

    urls = [url for kind, url, _ in client.calls if kind == "post"]
    assert urls[-2].endswith("playlist/p1/copy")
    assert urls[-1].endswith("playlist/p1/delete")
