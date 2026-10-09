"""``IPlaylist``: ListenBrainz playlists and the synthetic radio playlists."""

import pytest

from musicare_metadata_plugin_sdk import AuthRequiredError, NotFoundError, TransportError

from src.service import Credentials
from src.service import ListenBrainz
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
    assert client.headers[0]["Authorization"] == "Token token"


def test_radio_tracks_require_a_token(tmp_path):
    client = StubClient(lambda url, params: pytest.fail("no request must be made"))
    playlist = MusicBrainzPlaylist(ListenBrainz(client, Credentials()), client, FakeUser())

    with pytest.raises(AuthRequiredError):
        playlist.tracks("radio:tag:chill")

    assert client.calls == []


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
        if "playlist/pl-1" in url:
            return {"playlist": {"track": []}}
        assert url.endswith("recording/rec-1")
        return {"title": "Song", "artist-credit": [{"artist": {"id": "a1", "name": "A"}}]}

    def post_handler(url, body):
        assert url.endswith("playlist/pl-1/item/add")
        assert body["index"] == 3
        return {}

    playlist, _, _ = _playlist(tmp_path, handler, post_handler)

    result = playlist.add_tracks("pl-1", ["rec-1"], position=3)

    assert result.added == 1
    assert result.already_present == []


def test_add_tracks_embeds_the_release_group_in_the_entry(tmp_path):
    def handler(url, params):
        if "playlist/pl-1" in url:
            return {"playlist": {"track": []}}
        assert url.endswith("recording/rec-1")
        return {
            "title": "Song",
            "artist-credit": [{"artist": {"id": "a1", "name": "A"}}],
            "releases": [
                {
                    "id": "rel",
                    "title": "Album",
                    "release-group": {"id": "g1", "primary-type": "Album"},
                }
            ],
        }

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    playlist.add_tracks("pl-1", ["rec-1"])

    entries = [body for kind, url, body in client.calls if kind == "post"]
    extension = entries[0]["playlist"]["track"][0]["extension"]
    assert extension[MB_TRACK_EXTENSION]["release_group_mbid"] == "g1"


def test_add_tracks_skips_tracks_already_in_the_playlist(tmp_path):
    def handler(url, params):
        assert "playlist/pl-1" in url
        return {
            "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/t1"]}]}
        }

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    result = playlist.add_tracks("pl-1", ["t1"])

    assert result.added == 0
    assert result.already_present == ["t1"]
    assert [body for kind, url, body in client.calls if kind == "post"] == []


def test_add_tracks_adds_only_the_new_tracks_and_keeps_the_position(tmp_path):
    def handler(url, params):
        if "playlist/pl-1" in url:
            return {
                "playlist": {
                    "track": [{"identifier": ["https://musicbrainz.org/recording/t1"]}]
                }
            }
        assert "recording/t3" in url
        return {"title": "Three", "artist-credit": [{"artist": {"id": "a1", "name": "A"}}]}

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    result = playlist.add_tracks("pl-1", ["t1", "t3"], position=2)

    assert result.added == 1
    assert result.already_present == ["t1"]
    posts = [body for kind, url, body in client.calls if kind == "post"]
    assert len(posts) == 1
    assert posts[0]["index"] == 2
    assert [track["identifier"] for track in posts[0]["playlist"]["track"]] == [
        "https://musicbrainz.org/recording/t3"
    ]


def test_add_tracks_deduplicates_within_the_batch(tmp_path):
    def handler(url, params):
        if "playlist/pl-1" in url:
            return {"playlist": {"track": []}}
        assert "recording/t9" in url
        return {"title": "Nine", "artist-credit": [{"artist": {"id": "a1", "name": "A"}}]}

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    result = playlist.add_tracks("pl-1", ["t9", "t9"])

    assert result.added == 1
    assert result.already_present == ["t9"]
    posts = [body for kind, url, body in client.calls if kind == "post"]
    assert len(posts) == 1
    assert len(posts[0]["playlist"]["track"]) == 1


def test_add_tracks_reports_every_occurrence_of_an_already_present_id(tmp_path):
    def handler(url, params):
        assert "playlist/pl-1" in url
        return {
            "playlist": {"track": [{"identifier": ["https://musicbrainz.org/recording/t1"]}]}
        }

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    result = playlist.add_tracks("pl-1", ["t1", "t1"])

    assert result.added == 0
    assert result.already_present == ["t1", "t1"]
    assert [body for kind, url, body in client.calls if kind == "post"] == []


def test_add_tracks_propagates_a_resolution_failure_without_writing(tmp_path):
    def handler(url, params):
        if "playlist/pl-1" in url:
            return {"playlist": {"track": []}}
        raise NotFoundError("recording gone")

    playlist, client, _ = _playlist(tmp_path, handler, lambda url, body: {})

    with pytest.raises(NotFoundError):
        playlist.add_tracks("pl-1", ["t2"])

    assert [body for kind, url, body in client.calls if kind == "post"] == []


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


def test_delete_playlist_calls_the_provider_delete(tmp_path):
    def post_handler(url, body):
        assert url.endswith("playlist/pl-1/delete")
        return {}

    playlist, client, user = _playlist(tmp_path, lambda url, params: _PLAYLIST, post_handler)

    playlist.delete_playlist("pl-1")

    posts = [url for kind, url, _ in client.calls if kind == "post"]
    assert posts == ["https://api.listenbrainz.org/1/playlist/pl-1/delete"]
    assert user.calls == []


def test_delete_playlist_makes_the_playlist_unreachable(tmp_path):
    state = {"deleted": False}

    def handler(url, params):
        if state["deleted"]:
            raise NotFoundError("playlist gone")
        return _PLAYLIST

    def post_handler(url, body):
        state["deleted"] = True
        return {}

    playlist, _, _ = _playlist(tmp_path, handler, post_handler)

    playlist.delete_playlist("pl-1")

    with pytest.raises(NotFoundError):
        playlist.get_playlist("pl-1")


def _identified_entry(track_id, creator, *mbids):
    return {
        "identifier": [f"https://musicbrainz.org/recording/{track_id}"],
        "title": f"Song {track_id}",
        "creator": creator,
        "extension": {
            MB_TRACK_EXTENSION: {
                "artist_identifiers": [f"https://musicbrainz.org/artist/{mbid}" for mbid in mbids]
            }
        },
    }


def _packed_playlist(*entries):
    return {"playlist": {"title": "Mix", "creator": "bob", "track": list(entries)}}


def _resolving_handler(payload, artists_by_id=None, fail_lookup=False):
    def handler(url, params):
        if "musicbrainz.org/ws/2/artist/" in url:
            if fail_lookup:
                raise TransportError("musicbrainz down")
            return {
                "artists": [
                    {"id": mbid, "name": name} for mbid, name in (artists_by_id or {}).items()
                ]
            }
        return payload

    return handler


def _artist_lookups(client):
    return [url for kind, url, _ in client.calls if "ws/2/artist/" in url]


def test_tracks_resolve_packed_artists_from_their_mbids_in_one_lookup(tmp_path):
    payload = _packed_playlist(
        _identified_entry("t1", "Uno con Due", "u1", "u2"),
        _identified_entry("t2", "Uno con Due", "u2", "u1"),
    )
    handler = _resolving_handler(payload, {"u1": "Uno", "u2": "Due"})
    playlist, client, _ = _playlist(tmp_path, handler)

    page = playlist.tracks("pl-1")

    assert [artist.name for artist in page.items[0].artists] == ["Uno", "Due"]
    assert [artist.name for artist in page.items[1].artists] == ["Due", "Uno"]
    assert [artist.id for artist in page.items[0].artists] == ["u1", "u2"]
    assert len(_artist_lookups(client)) == 1


def test_tracks_fall_back_to_the_split_when_the_lookup_fails(tmp_path):
    payload = _packed_playlist(_identified_entry("t1", "A & B", "a1", "a2"))
    playlist, _, _ = _playlist(tmp_path, _resolving_handler(payload, fail_lookup=True))

    page = playlist.tracks("pl-1")

    assert [artist.name for artist in page.items[0].artists] == ["A", "B"]


def test_tracks_fall_back_to_the_split_when_the_lookup_is_partial(tmp_path):
    payload = _packed_playlist(_identified_entry("t1", "Uno con Due", "u1", "u2"))
    playlist, _, _ = _playlist(tmp_path, _resolving_handler(payload, {"u1": "Uno"}))

    page = playlist.tracks("pl-1")

    assert [artist.name for artist in page.items[0].artists] == ["Uno con Due", "Uno con Due"]


def test_tracks_make_no_artist_lookup_for_single_identifier_entries(tmp_path):
    payload = _packed_playlist(_identified_entry("t1", "Primo & Squarta", "a1"))
    playlist, client, _ = _playlist(tmp_path, _resolving_handler(payload, {"a1": "Primo"}))

    page = playlist.tracks("pl-1")

    assert [artist.name for artist in page.items[0].artists] == ["Primo & Squarta"]
    assert _artist_lookups(client) == []


def test_tracks_remember_resolved_names_for_the_session(tmp_path):
    payload = _packed_playlist(_identified_entry("t1", "Uno con Due", "u1", "u2"))
    handler = _resolving_handler(payload, {"u1": "Uno", "u2": "Due"})
    playlist, client, _ = _playlist(tmp_path, handler)

    playlist.tracks("pl-1")
    page = playlist.tracks("pl-1")

    assert [artist.name for artist in page.items[0].artists] == ["Uno", "Due"]
    assert len(_artist_lookups(client)) == 1


def test_radio_tracks_resolve_packed_artists_from_their_mbids(tmp_path):
    radio = {
        "payload": {
            "jspf": {
                "playlist": {
                    "track": [_identified_entry("t9", "Uno con Due", "u1", "u2")]
                }
            }
        }
    }
    playlist, _, _ = _playlist(tmp_path, _resolving_handler(radio, {"u1": "Uno", "u2": "Due"}))

    page = playlist.tracks("radio:tag:chill")

    assert [artist.name for artist in page.items[0].artists] == ["Uno", "Due"]
