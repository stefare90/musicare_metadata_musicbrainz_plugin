"""``ITrack``: details and ListenBrainz radio."""

import pytest

from musicare_metadata_plugin_sdk import AuthRequiredError

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz
from src.segments.track import MusicBrainzTrack

from ._fixtures import recording_payload, release_payload
from ._stubs import StubClient, authenticated_lb


def _radio_payload(recording_ids):
    return {
        "payload": {
            "jspf": {
                "playlist": {
                    "track": [
                        {"identifier": [f"https://musicbrainz.org/recording/{rid}"]}
                        for rid in recording_ids
                    ]
                }
            }
        }
    }


def _lb_radio_indices(client):
    return [
        index
        for index, (kind, url, _) in enumerate(client.calls)
        if kind == "get" and url.endswith("explore/lb-radio")
    ]


def test_get_track_embeds_the_release_as_album():
    def handler(url, params):
        return recording_payload(releases=[release_payload(front=True)])

    client = StubClient(handler)
    track = MusicBrainzTrack(ListenBrainz(client, Credentials()), client).get_track(
        "recording-1"
    )

    assert track.id == "recording-1"
    assert track.album.id == "rg:group-1"


def test_radio_builds_the_prompt_and_resolves_the_recordings(tmp_path):
    def handler(url, params):
        if url.endswith("/recording/rec-1"):
            return recording_payload(tags=["art rock", "alternative"])
        if url.endswith("explore/lb-radio"):
            assert params["mode"] == "easy"
            assert params["prompt"] == "artist:(artist-1) tag:(art rock,alternative):2:easy"
            return _radio_payload(["rec-9", "rec-10"])
        if url.endswith("/recording"):
            assert "rid:rec-9" in params["query"] and "rid:rec-10" in params["query"]
            return {
                "recordings": [
                    recording_payload("rec-9", releases=[]),
                    recording_payload("rec-10", title="Karma Police", releases=[]),
                ]
            }
        raise AssertionError(f"unexpected URL: {url}")

    client = StubClient(handler)
    tracks = MusicBrainzTrack(authenticated_lb(client, tmp_path)[0], client).radio("rec-1")

    assert [track.id for track in tracks] == ["rec-9", "rec-10"]


def test_radio_without_tags_only_uses_the_artist(tmp_path):
    def handler(url, params):
        if url.endswith("/recording/rec-1"):
            return recording_payload()
        if url.endswith("explore/lb-radio"):
            assert params["prompt"] == "artist:(artist-1)"
            return _radio_payload([])
        raise AssertionError(f"unexpected URL: {url}")

    client = StubClient(handler)
    track = MusicBrainzTrack(authenticated_lb(client, tmp_path)[0], client)

    assert track.radio("rec-1") == []


def test_radio_sends_the_authorization_header(tmp_path):
    def handler(url, params):
        if url.endswith("explore/lb-radio"):
            return _radio_payload([])
        return recording_payload()

    client = StubClient(handler)
    MusicBrainzTrack(authenticated_lb(client, tmp_path)[0], client).radio("rec-1")

    [index] = _lb_radio_indices(client)
    assert client.headers[index]["Authorization"] == "Token token"


def test_radio_requires_a_token_before_any_request():
    client = StubClient(lambda url, params: pytest.fail("no request must be made"))

    with pytest.raises(AuthRequiredError):
        MusicBrainzTrack(ListenBrainz(client, Credentials()), client).radio("rec-1")

    assert client.calls == []
