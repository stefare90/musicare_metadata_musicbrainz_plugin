"""``IPlaylist``: ListenBrainz playlists and the synthetic radio playlists."""

import pytest

from musicare_metadata_plugin_sdk import NotFoundError

from src.segments.playlist import MusicBrainzPlaylist

from ._stubs import StubClient, authenticated_lb

MB_TRACK_EXTENSION = "https://musicbrainz.org/doc/jspf#track"
_JSPF_PLAYLIST = "https://musicbrainz.org/doc/jspf#playlist"

_PLAYLIST = {
    "playlist": {
        "title": "Mix",
        "annotation": "desc",
        "creator": "bob",
        "extension": {_JSPF_PLAYLIST: {"public": True}},
        "track": [
            {
                "identifier": ["https://musicbrainz.org/recording/t1"],
                "title": "One",
                "creator": "A",
                "extension": {MB_TRACK_EXTENSION: {"release_group_mbid": "g1"}},
            },
            {"identifier": ["https://musicbrainz.org/recording/t2"], "title": "Two", "creator": "B"},
        ],
    }
}


class FakeUser:
    def __init__(self):
        self.calls = []

    def save_playlist(self, id):
        self.calls.append(("save", id))

    def unsave_playlist(self, id):
        self.calls.append(("unsave", id))


def _playlist(tmp_path, handler, post_handler=None):
    client = StubClient(handler, post_handler)
    lb, credentials = authenticated_lb(client, tmp_path)
    user = FakeUser()
    return MusicBrainzPlaylist(lb, client, user), client, user


def test_get_playlist_reads_metadata_and_cover_art(tmp_path):
    playlist, _, _ = _playlist(tmp_path, lambda url, params: _PLAYLIST)

    result = playlist.get_playlist("pl-1")

    assert result.id == "pl-1"
    assert result.name == "Mix"
    assert result.description == "desc"
    assert result.owner.id == "bob"
    assert result.is_public is True
    assert result.images[0].url == (
        "https://coverartarchive.org/release-group/g1/front-250.jpg"
    )


def test_get_playlist_raises_not_found(tmp_path):
    playlist, _, _ = _playlist(tmp_path, lambda url, params: {})

    with pytest.raises(NotFoundError):
        playlist.get_playlist("missing")


def test_tracks_pages_the_jspf_entries(tmp_path):
    playlist, _, _ = _playlist(tmp_path, lambda url, params: _PLAYLIST)

    page = playlist.tracks("pl-1", offset=0, limit=1)

    assert page.total == 2
    assert [track.id for track in page.items] == ["t1"]
    assert page.items[0].album.id == "rg:g1"


def test_radio_tracks_are_materialised_from_lb_radio(tmp_path):
    radio = {
        "payload": {
            "jspf": {
                "playlist": {
                    "track": [
                        {"identifier": ["https://musicbrainz.org/recording/t9"], "title": "Radio"}
                    ]
                }
            }
        }
    }
    playlist, client, _ = _playlist(tmp_path, lambda url, params: radio)

    page = playlist.tracks("radio:tag:chill")

    assert [track.id for track in page.items] == ["t9"]
    assert page.total == 1
    assert client.calls[0][2]["prompt"] == "tag:(chill)"


def test_radio_playlist_metadata_is_synthesised(tmp_path):
    playlist, _, _ = _playlist(tmp_path, lambda url, params: {})

    result = playlist.get_playlist("radio:artist:Radiohead")

    assert result.name == "Radiohead Radio"
    assert "Radiohead" in result.description


def test_create_playlist_returns_the_created_document(tmp_path):
    def post_handler(url, body):
        assert url.endswith("playlist/create")
        assert body["playlist"]["title"] == "New"
        return {"playlist_mbid": "new"}

    playlist, _, _ = _playlist(tmp_path, lambda url, params: {}, post_handler)

    result = playlist.create_playlist("tester", "New", description="d", public=True)

    assert result.id == "new"
    assert result.is_public is True
    assert result.owner.id == "tester"


def test_update_playlist_merges_with_the_current_document(tmp_path):
    def post_handler(url, body):
        assert url.endswith("playlist/edit/pl-1")
        assert body["playlist"]["annotation"] == "desc"
        assert body["playlist"]["extension"][_JSPF_PLAYLIST]["public"] is True
        return {}

    playlist, _, _ = _playlist(tmp_path, lambda url, params: _PLAYLIST, post_handler)

    playlist.update_playlist("pl-1", name="Renamed")


def test_add_tracks_builds_jspf_entries_with_a_position(tmp_path):
    def handler(url, params):
        assert url.endswith("recording/rec-1")
        return {"title": "Song", "artist-credit": [{"artist": {"id": "a1", "name": "A"}}]}

    def post_handler(url, body):
        assert url.endswith("playlist/pl-1/item/add")
        assert body["index"] == 3
        return {}

    playlist, _, _ = _playlist(tmp_path, handler, post_handler)

    playlist.add_tracks("pl-1", ["rec-1"], position=3)


def test_remove_tracks_deletes_the_matching_indices_in_reverse(tmp_path):
    def post_handler(url, body):
        assert url.endswith("playlist/pl-1/item/delete")
        return {}

    playlist, client, _ = _playlist(tmp_path, lambda url, params: _PLAYLIST, post_handler)

    playlist.remove_tracks("pl-1", ["t1", "t2"])

    deletes = [body for kind, url, body in client.calls if kind == "post"]
    assert deletes == [{"index": 1, "count": 1}, {"index": 0, "count": 1}]


def test_save_and_unsave_delegate_to_the_user_library(tmp_path):
    playlist, _, user = _playlist(tmp_path, lambda url, params: {})

    playlist.save("p1")
    playlist.unsave("p1")

    assert user.calls == [("save", "p1"), ("unsave", "p1")]
