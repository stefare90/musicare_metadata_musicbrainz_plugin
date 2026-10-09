"""``IArtist``: profile, discography, top tracks and related artists."""

import pytest

from musicare_metadata_plugin_sdk import (
    AuthContext,
    AuthRequiredError,
    Image,
    TransportError,
)

from src.service import Credentials
from src.service import ListenBrainz
from src.segments.artist import MusicBrainzArtist

from ._fixtures import artist_payload, release_group_payload
from ._stubs import StubClient, StubImages, authenticated_lb


def _artist(client, images=None, lb=None):
    return MusicBrainzArtist(
        client,
        images or StubImages(),
        lb or ListenBrainz(client, Credentials()),
    )


def _entry(
    mbid,
    name="Track",
    count=100,
    release="rel-1",
    caa="caa-1",
    length=1000,
    artists=None,
):
    return {
        "recording_mbid": mbid,
        "recording_name": name,
        "total_listen_count": count,
        "length": length,
        "release_mbid": release,
        "release_name": "Album",
        "caa_release_mbid": caa,
        "artists": artists
        if artists is not None
        else [{"artist_mbid": "artist-1", "artist_credit_name": "Radiohead"}],
    }


def test_get_artist_merges_genres_and_images():
    def handler(url, params):
        return artist_payload(genres=["alternative rock"])

    images = StubImages({"artist-1": [Image(url="https://img", width=56, height=56)]})
    artist = _artist(StubClient(handler), images).get_artist("artist-1")

    assert artist.id == "artist-1"
    assert artist.genres == ["alternative rock"]
    assert artist.images[0].url == "https://img"


def test_albums_reads_the_release_group_total():
    def handler(url, params):
        return {"release-group-count": 9, "release-groups": [release_group_payload()]}

    page = _artist(StubClient(handler)).albums("artist-1")

    assert page.total == 9
    assert page.items[0].id == "rg:group-1"


def test_top_tracks_rank_by_listen_count_and_paginate(tmp_path):
    entries = [_entry(f"rec-{n}", name=f"Track {n}", count=n) for n in range(10, 0, -1)]
    client = StubClient(lambda url, params: entries)
    lb, _ = authenticated_lb(client, tmp_path)

    page = _artist(client, lb=lb).top_tracks("artist-1", offset=2, limit=3)

    assert [track.name for track in page.items] == ["Track 8", "Track 7", "Track 6"]
    assert (page.total, page.offset, page.limit) == (10, 2, 3)


def test_top_tracks_keep_the_best_row_of_a_duplicated_recording(tmp_path):
    entries = [
        _entry("rec-x", name="Duplicated", count=3),
        _entry("rec-y", name="Other", count=10),
        _entry("rec-x", name="Duplicated", count=99),
    ]
    client = StubClient(lambda url, params: entries)
    lb, _ = authenticated_lb(client, tmp_path)

    page = _artist(client, lb=lb).top_tracks("artist-1")

    assert [track.name for track in page.items] == ["Duplicated", "Other"]
    assert page.total == 2


def test_top_tracks_tie_break_is_stable_by_mbid(tmp_path):
    entries = [
        _entry("rec-b", name="B", count=5),
        _entry("rec-c", name="C", count=10),
        _entry("rec-a", name="A", count=5),
    ]
    client = StubClient(lambda url, params: entries)
    lb, _ = authenticated_lb(client, tmp_path)

    page = _artist(client, lb=lb).top_tracks("artist-1")

    assert [track.name for track in page.items] == ["C", "A", "B"]


def test_top_tracks_are_capped_to_the_most_popular(tmp_path):
    entries = [_entry(f"rec-{n}", name=f"Track {n}", count=n) for n in range(60, 0, -1)]
    client = StubClient(lambda url, params: entries)
    lb, _ = authenticated_lb(client, tmp_path)

    page = _artist(client, lb=lb).top_tracks("artist-1", offset=0, limit=100)

    assert page.total == 50
    assert len(page.items) == 50
    assert page.items[0].name == "Track 60"
    assert page.items[-1].name == "Track 11"


def test_top_tracks_are_empty_when_the_artist_has_no_listens(tmp_path):
    client = StubClient(lambda url, params: [])
    lb, _ = authenticated_lb(client, tmp_path)

    page = _artist(client, lb=lb).top_tracks("artist-1", offset=5, limit=10)

    assert page.items == []
    assert page.total == 0
    assert (page.offset, page.limit) == (5, 10)


def test_top_tracks_require_a_token(tmp_path):
    client = StubClient(lambda url, params: pytest.fail("no request must be made"))

    with pytest.raises(AuthRequiredError):
        _artist(client).top_tracks("artist-1")


def test_top_tracks_raise_when_listenbrainz_is_unreachable(tmp_path):
    def handler(url, params):
        raise TransportError("listenbrainz is down")

    client = StubClient(handler)
    lb, _ = authenticated_lb(client, tmp_path)

    with pytest.raises(TransportError):
        _artist(client, lb=lb).top_tracks("artist-1")


def test_top_tracks_raise_on_an_unexpected_response(tmp_path):
    client = StubClient(lambda url, params: {"error": "unavailable"})
    lb, _ = authenticated_lb(client, tmp_path)

    with pytest.raises(TransportError):
        _artist(client, lb=lb).top_tracks("artist-1")


def test_top_tracks_serve_pages_from_the_buffered_ranking(tmp_path):
    entries = [_entry(f"rec-{n}", count=n) for n in range(9, -1, -1)]
    client = StubClient(lambda url, params: entries)
    lb, _ = authenticated_lb(client, tmp_path)
    artist = _artist(client, lb=lb)

    artist.top_tracks("artist-1", offset=0, limit=4)
    page = artist.top_tracks("artist-1", offset=4, limit=4)

    assert len(client.calls) == 1
    assert [track.id for track in page.items] == ["rec-5", "rec-4", "rec-3", "rec-2"]


def test_top_tracks_are_not_served_from_the_buffer_after_logout(tmp_path):
    client = StubClient(lambda url, params: [_entry("rec-1", count=10)])
    lb, credentials = authenticated_lb(client, tmp_path)
    artist = _artist(client, lb=lb)

    artist.top_tracks("artist-1")
    credentials.clear(AuthContext(data_dir=str(tmp_path), plugin_id="test"))

    with pytest.raises(AuthRequiredError):
        artist.top_tracks("artist-1")


def test_top_tracks_build_album_and_covers_from_the_entry(tmp_path):
    entry = _entry(
        "rec-1", name="Karma Police", count=100, release="rel-1", caa="caa-9", length=262426
    )
    client = StubClient(lambda url, params: [entry])
    lb, _ = authenticated_lb(client, tmp_path)

    track = _artist(client, lb=lb).top_tracks("artist-1").items[0]

    assert track.id == "rec-1"
    assert track.name == "Karma Police"
    assert track.duration_ms == 262426
    assert track.artists[0].id == "artist-1"
    assert track.album.id == "rel-1"
    assert track.album.name == "Album"
    assert "coverartarchive.org/release/caa-9/front-250.jpg" in track.album.images[0].url


def test_top_tracks_fall_back_to_the_release_for_covers(tmp_path):
    entry = _entry("rec-1", release="rel-1", caa="")
    client = StubClient(lambda url, params: [entry])
    lb, _ = authenticated_lb(client, tmp_path)

    track = _artist(client, lb=lb).top_tracks("artist-1").items[0]

    assert "coverartarchive.org/release/rel-1/front-250.jpg" in track.album.images[0].url


def test_related_raises_when_labs_is_unreachable():
    def handler(url, params):
        raise TransportError("labs is down")

    with pytest.raises(TransportError):
        _artist(StubClient(handler)).related("artist-1")


def test_related_is_empty_when_the_service_knows_no_similar_artist():
    def handler(url, params):
        return []

    page = _artist(StubClient(handler)).related("artist-1")

    assert page.items == []
    assert page.total == 0


def test_related_raises_on_an_unexpected_labs_response():
    def handler(url, params):
        return {"error": "unavailable"}

    with pytest.raises(TransportError):
        _artist(StubClient(handler)).related("artist-1")


def test_related_uses_the_propagating_client():
    client = StubClient(lambda url, params: [])

    _artist(client).related("artist-1")

    assert [kind for kind, _, _ in client.calls] == ["get"]


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
    page = _artist(client, images).related("artist-1", offset=0, limit=2)

    assert [artist.id for artist in page.items] == ["a1", "a2"]
    assert page.total == 3
    assert images.requested[-1] == ["a1", "a2"]


def test_related_page_beyond_the_candidates_is_empty():
    def handler(url, params):
        if url.endswith("similar-artists/json"):
            return [{"artist_mbid": "a1"}]
        return artist_payload()

    page = _artist(StubClient(handler)).related("artist-1", offset=10, limit=5)

    assert page.items == []
    assert page.total == 1
