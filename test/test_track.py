"""``ITrack``: details and ListenBrainz radio."""

from src.segments.track import MusicBrainzTrack

from ._fixtures import recording_payload, release_payload
from ._stubs import StubClient


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


def test_get_track_embeds_the_release_as_album():
    def handler(url, params):
        return recording_payload(releases=[release_payload(front=True)])

    track = MusicBrainzTrack(StubClient(handler)).get_track("recording-1")

    assert track.id == "recording-1"
    assert track.album.id == "rg:group-1"


def test_radio_builds_the_prompt_and_resolves_the_recordings():
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

    tracks = MusicBrainzTrack(StubClient(handler)).radio("rec-1")

    assert [track.id for track in tracks] == ["rec-9", "rec-10"]


def test_radio_without_tags_only_uses_the_artist():
    def handler(url, params):
        if url.endswith("/recording/rec-1"):
            return recording_payload()
        if url.endswith("explore/lb-radio"):
            assert params["prompt"] == "artist:(artist-1)"
            return _radio_payload([])
        raise AssertionError(f"unexpected URL: {url}")

    assert MusicBrainzTrack(StubClient(handler)).radio("rec-1") == []
