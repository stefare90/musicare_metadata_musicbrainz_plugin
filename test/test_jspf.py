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


def test_album_reference_reads_the_radio_release_identifier():
    track = jspf.build_track(
        _track({"release_identifier": "https://musicbrainz.org/release/rel-9"})
    )

    assert track.album.id == "rel-9"
    assert track.album.external_uri == "https://musicbrainz.org/release/rel-9"
    assert [image.width for image in track.album.images] == [250, 500, 1200]
    assert track.album.images[0].url == (
        "https://coverartarchive.org/release/rel-9/front-250.jpg"
    )


def test_release_identifier_accepts_a_list_and_loses_to_release_mbid():
    preferred = jspf.build_track(
        _track(
            {
                "release_mbid": "rel-1",
                "release_identifier": ["https://musicbrainz.org/release/rel-9"],
            }
        )
    )
    assert preferred.album.id == "rel-1"

    listed = jspf.build_track(
        _track({"release_identifier": ["https://musicbrainz.org/release/rel-9"]})
    )
    assert listed.album.id == "rel-9"


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


def _identified_creator(creator, *mbids):
    return {
        "creator": creator,
        "extension": {
            MB_TRACK_EXTENSION: {
                "artist_identifiers": [f"https://musicbrainz.org/artist/{mbid}" for mbid in mbids]
            }
        },
    }


def test_artists_split_a_packed_credit_over_the_identifiers():
    artists = jspf.extract_artists(_identified_creator("Dela feat. J Sands", "a1", "a2"))

    assert [(artist.id, artist.name) for artist in artists] == [("a1", "Dela"), ("a2", "J Sands")]
    assert artists[0].external_uri == "https://musicbrainz.org/artist/a1"


def test_artists_split_an_ampersand_credit():
    artists = jspf.extract_artists(_identified_creator("Nome Uno & Nome Due", "a1", "a2"))

    assert [artist.name for artist in artists] == ["Nome Uno", "Nome Due"]


def test_artists_keep_a_joint_name_with_a_single_identifier():
    artists = jspf.extract_artists(_identified_creator("Primo & Squarta", "a1"))

    assert len(artists) == 1
    assert artists[0].id == "a1"
    assert artists[0].name == "Primo & Squarta"


def test_artists_keep_the_full_credit_when_the_counts_do_not_match():
    artists = jspf.extract_artists(_identified_creator("A & B & C", "a1", "a2"))

    assert [artist.name for artist in artists] == ["A & B & C", "A & B & C"]
    assert [artist.id for artist in artists] == ["a1", "a2"]


def test_playlist_is_public_reads_the_playlist_extension():
    def playlist(extension):
        return {"title": "Mix", "extension": extension}

    assert jspf.playlist_is_public(playlist({jspf.MB_PLAYLIST_EXTENSION: {"public": True}})) is True
    assert jspf.playlist_is_public(playlist({jspf.MB_PLAYLIST_EXTENSION: {"public": "true"}})) is True
    assert jspf.playlist_is_public(playlist({jspf.MB_PLAYLIST_EXTENSION: {"public": False}})) is False
    assert jspf.playlist_is_public(playlist({jspf.MB_PLAYLIST_EXTENSION: {}})) is False
    assert jspf.playlist_is_public(playlist({})) is False
    assert jspf.playlist_is_public({}) is False


def test_artists_prefer_resolved_names_over_the_split():
    artists = jspf.extract_artists(
        _identified_creator("A con B", "a1", "a2"), {"a1": "A", "a2": "B"}
    )

    assert [(artist.id, artist.name) for artist in artists] == [("a1", "A"), ("a2", "B")]


def test_artists_ignore_a_partial_name_map():
    artists = jspf.extract_artists(
        _identified_creator("A & B", "a1", "a2"), {"a1": "A"}
    )

    assert [artist.name for artist in artists] == ["A", "B"]
