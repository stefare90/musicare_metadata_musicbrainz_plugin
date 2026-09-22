"""JSPF parsing: durations, artists, album reference and images."""

from src import jspf

MB_TRACK_EXTENSION = "https://musicbrainz.org/doc/jspf#track"


def _track(extension=None, **overrides):
    payload = {
        "identifier": ["https://musicbrainz.org/recording/rec-1"],
        "title": "Song",
        "creator": "Radiohead",
        "album": "OK Computer",
        "extension": {MB_TRACK_EXTENSION: extension or {}},
    }
    payload.update(overrides)
    return payload


def test_build_track_reads_the_extension():
    track = jspf.build_track(
        _track(
            {
                "artist_identifiers": ["https://musicbrainz.org/artist/ar-1"],
                "release_group_mbid": "g-1",
                "duration_ms": "180000",
            }
        )
    )

    assert track is not None
    assert track.id == "rec-1"
    assert track.name == "Song"
    assert track.duration_ms == 180000
    assert track.artists[0].id == "ar-1"
    assert track.album.id == "rg:g-1"
    assert track.album.name == "OK Computer"
    assert [image.width for image in track.album.images] == [250, 500, 1200]
    assert track.album.images[0].url == (
        "https://coverartarchive.org/release-group/g-1/front-250.jpg"
    )


def test_build_track_without_identifier_is_skipped():
    assert jspf.build_track({"title": "Song"}) is None


def test_track_image_wins_over_the_cover_art_archive():
    track = jspf.build_track(_track(image="https://example.com/a.jpg"))

    assert len(track.album.images) == 1
    assert track.album.images[0].width == 500


def test_album_reference_falls_back_to_the_release():
    track = jspf.build_track(
        _track({"release_mbid": "rel-1", "additional_metadata": {"caa_release_mbid": "rel-2"}})
    )

    assert track.album.id == "rel-2"
    assert track.album.external_uri == "https://musicbrainz.org/release/rel-2"


def test_duration_falls_back_to_the_top_level_fields():
    assert jspf.extract_duration_ms({"duration": "1000"}) == 1000
    assert jspf.extract_duration_ms({"length": "2000"}) == 2000
    assert jspf.extract_duration_ms({"extension": {MB_TRACK_EXTENSION: {"length": "3000"}}}) == 3000
    assert jspf.extract_duration_ms({"duration": "0"}) == 0
    assert jspf.extract_duration_ms({}) == 0


def test_artists_fall_back_to_the_creator_name():
    artists = jspf.extract_artists({"creator": "Someone"})

    assert len(artists) == 1
    assert artists[0].id == ""
    assert artists[0].name == "Someone"
