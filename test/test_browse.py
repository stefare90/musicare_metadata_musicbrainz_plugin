"""``IBrowse``: the home sections and their playlist items."""

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.browse import MusicBrainzBrowse

from ._stubs import StubClient


class FakeUser:
    def __init__(self, user_id="tester", saved=None):
        self._user_id = user_id
        self._saved = saved

    def me(self):
        return {"user_id": self._user_id}

    def saved_playlists(self, offset=0, limit=20):
        assert self._saved is not None, "the fallback must only be used when needed"
        return self._saved


def _browse(handler, user=None):
    lb = ListenBrainz(StubClient(handler), Credentials())
    return MusicBrainzBrowse(lb, user or FakeUser())


def test_sections_are_the_three_editorial_entries():
    page = _browse(lambda url, params: {}).sections()

    assert [section.id for section in page.items] == [
        "created_for_you",
        "top_artist_radios",
        "mood_playlists",
    ]


def test_created_for_you_maps_the_algorithmic_playlists():
    def handler(url, params):
        assert url.endswith("user/tester/playlists/createdfor")
        return {
            "playlists": [
                {
                    "playlist": {
                        "identifier": "https://listenbrainz.org/playlist/c1",
                        "title": "Weekly",
                        "creator": "listenbrainz",
                    }
                }
            ]
        }

    page = _browse(handler).section_items("created_for_you")

    assert [item.id for item in page.items] == ["c1"]
    assert page.total == 1


def test_created_for_you_falls_back_to_the_saved_playlists():
    saved = "the-saved-page"

    page = _browse(lambda url, params: {"playlists": []}, FakeUser(saved=saved)).section_items(
        "created_for_you"
    )

    assert page == saved


def test_top_artist_radios_become_radio_playlists():
    def handler(url, params):
        assert url.endswith("stats/user/tester/artists")
        return {"payload": {"artists": [{"artist_name": "Radiohead", "artist_mbid": "a1"}]}}

    page = _browse(handler).section_items("top_artist_radios")

    assert [item.id for item in page.items] == ["radio:artist:Radiohead"]
    assert page.items[0].name == "Radiohead Radio"


def test_mood_playlists_are_static():
    page = _browse(lambda url, params: {}).section_items("mood_playlists")

    assert len(page.items) == 5
    assert page.items[0].id == "radio:tag:romantic"


def test_unknown_section_is_empty():
    page = _browse(lambda url, params: {}, FakeUser(user_id=None)).section_items("nope")

    assert page.items == []
    assert page.total == 0
