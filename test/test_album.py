"""``IAlbum``: release-group resolution, details and track listing."""

import pytest

from musicare_metadata_plugin_sdk import NotFoundError

from src.segments.album import MusicBrainzAlbum

from ._fixtures import recording_payload, release_payload
from ._stubs import StubClient


def _handler(url, params):
    if url.endswith("/release"):
        return {"releases": [{"id": "release-9"}]}
    if url.endswith("/release/release-9"):
        return release_payload(release_id="release-9", group_id="group-9", front=True, tracks=3)
    if url.endswith("/recording"):
        return {
            "recording-count": 2,
            "recordings": [
                recording_payload("rec-1"),
                recording_payload("rec-2", title="Exit Music (For a Film)"),
            ],
        }
    raise AssertionError(f"unexpected URL: {url}")


def test_get_album_resolves_a_release_group_to_the_first_release():
    album = MusicBrainzAlbum(StubClient(_handler)).get_album("rg:group-9")

    assert album.id == "rg:group-9"
    assert album.name == "OK Computer"
    assert album.total_tracks == 4
    assert album.external_uri == "https://musicbrainz.org/release/release-9"


def test_get_album_accepts_a_release_id_without_lookup():
    client = StubClient(_handler)

    album = MusicBrainzAlbum(client).get_album("release-9")

    assert album.id == "rg:group-9"
    assert all(url != "https://musicbrainz.org/ws/2/release" for _, url, _ in client.calls)


def test_tracks_attach_the_resolved_album_to_every_track():
    page = MusicBrainzAlbum(StubClient(_handler)).tracks("rg:group-9")

    assert page.total == 2
    assert [track.name for track in page.items] == ["Paranoid Android", "Exit Music (For a Film)"]
    assert all(track.album.id == "rg:group-9" for track in page.items)


def test_tracks_total_falls_back_to_the_page_size():
    def handler(url, params):
        if url.endswith("/release/release-1"):
            return release_payload(release_id="release-1")
        if url.endswith("/recording"):
            return {"recordings": [recording_payload("rec-1")]}
        raise AssertionError(url)

    page = MusicBrainzAlbum(StubClient(handler)).tracks("release-1")

    assert page.total == 1


def test_missing_release_group_raises_not_found():
    def handler(url, params):
        return {"releases": []}

    with pytest.raises(NotFoundError):
        MusicBrainzAlbum(StubClient(handler)).get_album("rg:missing")
