"""``IArtist``: profile, discography, top tracks and related artists."""

from musicare_metadata_plugin_sdk import Image

from src.segments.artist import MusicBrainzArtist

from ._fixtures import artist_payload, recording_payload, release_group_payload
from ._stubs import StubClient, StubImages


def _rating(value, votes):
    return {"rating": {"value": value, "votes-count": votes}}


def _release_with(tracks):
    return {
        "releases": [
            {
                "id": "release-1",
                "title": "OK Computer",
                "media": [{"tracks": [{"recording": recording} for recording in tracks]}],
            }
        ]
    }


def test_get_artist_merges_genres_and_images():
    def handler(url, params):
        return artist_payload(genres=["alternative rock"])

    images = StubImages({"artist-1": [Image(url="https://img", width=56, height=56)]})
    artist = MusicBrainzArtist(StubClient(handler), images).get_artist("artist-1")

    assert artist.id == "artist-1"
    assert artist.genres == ["alternative rock"]
    assert artist.images[0].url == "https://img"


def test_albums_reads_the_release_group_total():
    def handler(url, params):
        return {"release-group-count": 9, "release-groups": [release_group_payload()]}

    page = MusicBrainzArtist(StubClient(handler), StubImages()).albums("artist-1")

    assert page.total == 9
    assert page.items[0].id == "rg:group-1"


def test_top_tracks_deduplicates_by_title_and_ranks_by_rating():
    tracks = [
        recording_payload("rec-a", title="A", **_rating(8, 2)),
        recording_payload("rec-b", title="B", **_rating(5, 1)),
        recording_payload("rec-a2", title="A", **_rating(1, 10)),
    ]

    def handler(url, params):
        return _release_with(tracks)

    page = MusicBrainzArtist(StubClient(handler), StubImages()).top_tracks("artist-1")

    assert [track.name for track in page.items] == ["B", "A"]
    assert [track.id for track in page.items] == ["rec-b", "rec-a"]
    assert page.total == 2


def test_top_tracks_pages_manually_and_survives_an_empty_response():
    def handler(url, params):
        return {"releases": []}

    page = MusicBrainzArtist(StubClient(handler), StubImages()).top_tracks(
        "artist-1", offset=5, limit=10
    )

    assert page.items == []
    assert page.total == 0
    assert (page.offset, page.limit) == (5, 10)


def test_related_degrades_to_empty_when_labs_fails():
    def handler(url, params):
        return {"error": "unavailable"}

    page = MusicBrainzArtist(StubClient(handler), StubImages()).related("artist-1")

    assert page.items == []
    assert page.total == 0


def test_related_resolves_the_requested_page_and_keeps_the_total():
    def handler(url, params):
        if url.endswith("similar-artists/json"):
            return [
                {"artist_mbid": "a1"},
                {"artist_mbid": "a2"},
                {"artist_mbid": "a3"},
            ]
        mbid = url.rsplit("/", 1)[-1]
        return artist_payload(mbid=mbid, name=f"Artist {mbid}")

    images = StubImages()
    client = StubClient(handler)
    page = MusicBrainzArtist(client, images).related("artist-1", offset=0, limit=2)

    assert [artist.id for artist in page.items] == ["a1", "a2"]
    assert page.total == 3
    assert images.requested[-1] == ["a1", "a2"]


def test_related_page_beyond_the_candidates_is_empty():
    def handler(url, params):
        if url.endswith("similar-artists/json"):
            return [{"artist_mbid": "a1"}]
        return artist_payload()

    page = MusicBrainzArtist(StubClient(handler), StubImages()).related(
        "artist-1", offset=10, limit=5
    )

    assert page.items == []
    assert page.total == 1
