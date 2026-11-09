"""``IListen``: the listening-history payload and its best-effort policy."""

import pytest

from musicare_metadata_plugin_sdk import (
    Album,
    AlbumType,
    Artist,
    AuthContext,
    AuthRequiredError,
    ListenEvent,
    ListenKind,
    RateLimitedError,
    Track,
    TransportError,
)

from src.service import Credentials, ListenBrainz
from src.segments.listen import MusicBrainzListen

from ._stubs import StubClient

RECORDING_MBID = "11111111-1111-1111-1111-111111111111"
RELEASE_GROUP_MBID = "22222222-2222-2222-2222-222222222222"
RELEASE_MBID = "33333333-3333-3333-3333-333333333333"
ARTIST_MBID = "44444444-4444-4444-4444-444444444444"

SUBMIT_LISTENS = "https://api.listenbrainz.org/1/submit-listens"
VERSION = "4.0.0"


def _artist(name="Radiohead", mbid=ARTIST_MBID):
    return Artist(
        id=mbid,
        name=name,
        external_uri=f"https://musicbrainz.org/artist/{mbid}",
    )


def _track(
    track_id=RECORDING_MBID,
    album_id=f"rg:{RELEASE_GROUP_MBID}",
    artists=None,
    duration_ms=240000,
):
    return Track(
        id=track_id,
        name="Karma Police",
        external_uri="https://musicbrainz.org/recording/" + RECORDING_MBID,
        artists=artists or [_artist()],
        album=Album(
            id=album_id,
            name="OK Computer",
            artists=[_artist()],
            release_date="1997-05-28",
            external_uri="https://musicbrainz.org/release-group/" + RELEASE_GROUP_MBID,
            total_tracks=12,
            album_type=AlbumType.ALBUM,
        ),
        duration_ms=duration_ms,
    )


def _additional_info(**extra):
    info = {
        "submission_client": "MusicAre Metadata Plugin",
        "submission_client_version": VERSION,
    }
    info.update(extra)
    return info


def _track_metadata(**extra):
    metadata = {
        "artist_name": "Radiohead",
        "track_name": "Karma Police",
        "release_name": "OK Computer",
        "additional_info": _additional_info(
            duration_ms=240000,
            recording_mbid=RECORDING_MBID,
            release_group_mbid=RELEASE_GROUP_MBID,
        ),
    }
    metadata.update(extra)
    return metadata


def _listen(tmp_path, post_handler=None, token="token"):
    client = StubClient(post_handler=post_handler)
    credentials = Credentials()
    if token:
        credentials.store(AuthContext(data_dir=str(tmp_path), plugin_id="test"), token)
    return MusicBrainzListen(ListenBrainz(client, credentials), VERSION), client


def test_playing_now_payload_is_exact(tmp_path):
    listen, client = _listen(tmp_path)

    listen.record(ListenEvent(kind=ListenKind.PLAYING_NOW, track=_track()))

    assert client.calls == [
        (
            "post",
            SUBMIT_LISTENS,
            {"listen_type": "playing_now", "payload": [{"track_metadata": _track_metadata()}]},
        )
    ]
    assert client.headers == [{"Authorization": "Token token"}]


def test_single_payload_carries_the_start_time(tmp_path):
    listen, client = _listen(tmp_path)

    listen.record(
        ListenEvent(kind=ListenKind.SINGLE, track=_track(), listened_at=1700000000)
    )

    assert client.calls == [
        (
            "post",
            SUBMIT_LISTENS,
            {
                "listen_type": "single",
                "payload": [
                    {"listened_at": 1700000000, "track_metadata": _track_metadata()}
                ],
            },
        )
    ]


def test_playing_now_never_carries_a_timestamp(tmp_path):
    listen, client = _listen(tmp_path)

    # The contract forbids the field on ``playing_now``; the mapper must not trust it
    # even when an event is built by hand.
    listen.record(
        ListenEvent(kind=ListenKind.PLAYING_NOW, track=_track(), listened_at=1700000000)
    )

    body = client.calls[0][2]
    assert "listened_at" not in body["payload"][0]


def test_multiple_artists_are_joined_with_an_ampersand(tmp_path):
    listen, client = _listen(tmp_path)

    listen.record(
        ListenEvent(
            kind=ListenKind.PLAYING_NOW,
            track=_track(
                artists=[_artist("Thom Yorke"), _artist("Jonny Greenwood", "55555555-5555-5555-5555-555555555555")]
            ),
        )
    )

    metadata = client.calls[0][2]["payload"][0]["track_metadata"]
    assert metadata["artist_name"] == "Thom Yorke & Jonny Greenwood"


def test_a_release_id_is_sent_as_the_release(tmp_path):
    listen, client = _listen(tmp_path)

    listen.record(
        ListenEvent(kind=ListenKind.PLAYING_NOW, track=_track(album_id=RELEASE_MBID))
    )

    info = client.calls[0][2]["payload"][0]["track_metadata"]["additional_info"]
    assert info["release_mbid"] == RELEASE_MBID
    assert "release_group_mbid" not in info


def test_ids_that_are_not_mbids_are_omitted(tmp_path):
    listen, client = _listen(tmp_path)

    # A track from another provider (or a local file) carries the app's own id: sending
    # it as an MBID would be a lie, while ListenBrainz matches by artist and title.
    listen.record(
        ListenEvent(
            kind=ListenKind.PLAYING_NOW,
            track=_track(track_id="local:42", album_id="local-album", duration_ms=0),
        )
    )

    metadata = client.calls[0][2]["payload"][0]["track_metadata"]
    assert metadata["additional_info"] == _additional_info()
    assert metadata["track_name"] == "Karma Police"


def test_no_token_sends_nothing(tmp_path):
    listen, client = _listen(tmp_path, token="")

    listen.record(ListenEvent(kind=ListenKind.PLAYING_NOW, track=_track()))
    listen.record(ListenEvent(kind=ListenKind.SINGLE, track=_track(), listened_at=1700000000))

    # Not an error and not a request: the account simply shares no history.
    assert client.calls == []


def test_the_service_refuses_to_post_without_a_token():
    client = StubClient()

    with pytest.raises(AuthRequiredError):
        ListenBrainz(client, Credentials()).submit_listens(
            {"listen_type": "playing_now", "payload": []}
        )

    assert client.calls == []


def test_a_rate_limited_single_raises(tmp_path):
    def throttled(url, body):
        raise RateLimitedError("slow down", retryable=True)

    listen, _ = _listen(tmp_path, post_handler=throttled)

    with pytest.raises(RateLimitedError):
        listen.record(ListenEvent(kind=ListenKind.SINGLE, track=_track(), listened_at=1))


def test_a_failed_playing_now_never_raises(tmp_path):
    def unreachable(url, body):
        raise TransportError("connection reset")

    listen, client = _listen(tmp_path, post_handler=unreachable)

    listen.record(ListenEvent(kind=ListenKind.PLAYING_NOW, track=_track()))

    assert len(client.calls) == 1


def test_a_failed_single_propagates(tmp_path):
    def unreachable(url, body):
        raise TransportError("connection reset")

    listen, _ = _listen(tmp_path, post_handler=unreachable)

    with pytest.raises(TransportError):
        listen.record(ListenEvent(kind=ListenKind.SINGLE, track=_track(), listened_at=1))
