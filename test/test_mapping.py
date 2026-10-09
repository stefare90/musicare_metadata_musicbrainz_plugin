"""Model builders: the mapping from MusicBrainz payloads to the SDK models."""

from musicare_metadata_plugin_sdk import AlbumType

from src.shared.mapping import (
    build_album,
    build_album_from_release_group,
    build_artist,
    build_track,
    recording_artist_ids,
    recording_tag_names,
    select_release,
)

from ._fixtures import (
    ARTIST_MBID,
    artist_payload,
    recording_payload,
    release_group_payload,
    release_payload,
)


def test_build_album_prefers_the_release_group_cover():
    album = build_album(release_payload(front=True))

    assert album.id == "rg:group-1"
    assert album.name == "OK Computer"
    assert album.external_uri == "https://musicbrainz.org/release/release-1"
    assert album.release_date == "1997-05-28"
    assert album.total_tracks == 13
    assert album.album_type is AlbumType.ALBUM
    assert [(image.width, image.height) for image in album.images] == [(250, 250), (500, 500)]
    assert album.images[0].url == (
        "https://coverartarchive.org/release-group/group-1/front-250.jpg"
    )
    assert [artist.id for artist in album.artists] == [ARTIST_MBID]


def test_build_album_uses_the_release_group_cover_without_a_release_cover():
    album = build_album(release_payload(front=False))

    assert album.images[0].url == (
        "https://coverartarchive.org/release-group/group-1/front-250.jpg"
    )
    assert [image.width for image in album.images] == [250, 500]


def test_build_album_falls_back_to_release_cover_without_release_group():
    album = build_album(release_payload(with_release_group=False))

    assert album.id == "release-1"
    assert album.images[0].url == "https://coverartarchive.org/release/release-1/front-250.jpg"


def test_build_album_maps_album_types():
    assert build_album(release_payload(primary_type="Single")).album_type is AlbumType.SINGLE
    assert (
        build_album(release_payload(primary_type="Compilation")).album_type
        is AlbumType.COMPILATION
    )
    assert build_album(release_payload(primary_type=None)).album_type is AlbumType.ALBUM


def test_build_album_from_release_group_has_three_cover_sizes():
    album = build_album_from_release_group(release_group_payload())

    assert album.id == "rg:group-1"
    assert album.external_uri == "https://musicbrainz.org/release-group/group-1"
    assert [image.width for image in album.images] == [250, 500, 1200]
    assert album.total_tracks == 0


def test_build_track_embeds_the_selected_release_as_album():
    track = build_track(recording_payload(releases=[release_payload(front=True)]))

    assert track.id == "recording-1"
    assert track.name == "Paranoid Android"
    assert track.duration_ms == 383000
    assert track.external_uri == "https://musicbrainz.org/recording/recording-1"
    assert track.album.id == "rg:group-1"
    assert track.artists[0].id == ARTIST_MBID


def test_build_track_skips_a_compilation_for_the_studio_release():
    compilation = release_payload(
        release_id="comp",
        group_id="comp-group",
        title="Summer Hits",
        date="2017",
        secondary_types=["Compilation"],
    )
    studio = release_payload(
        release_id="studio",
        group_id="studio-group",
        title="Fenomeno",
        date="2017-04-07",
    )

    track = build_track(recording_payload(releases=[compilation, studio]))

    assert track.album.id == "rg:studio-group"
    assert track.album.name == "Fenomeno"


def test_build_track_prefers_the_album_over_an_earlier_single():
    single = release_payload(
        release_id="single",
        group_id="single-group",
        title="Fenomeno",
        date="2017-03-03",
        primary_type="Single",
    )
    album = release_payload(
        release_id="album",
        group_id="album-group",
        title="Fenomeno",
        date="2017-04-07",
        primary_type="Album",
    )

    track = build_track(recording_payload(releases=[single, album]))

    assert track.album.id == "rg:album-group"


def test_select_release_prefers_the_oldest_studio_album():
    reissue = release_payload(release_id="reissue", group_id="a-group", date="2020-01-01")
    original = release_payload(release_id="original", group_id="b-group", date="1997-05-28")

    chosen = select_release([reissue, original])

    assert chosen["id"] == "original"


def test_select_release_uses_a_single_when_there_is_no_album():
    single = release_payload(
        release_id="only", group_id="single-group", date="2017-03-03", primary_type="Single"
    )
    compilation = release_payload(
        release_id="comp",
        group_id="comp-group",
        date="2017",
        secondary_types=["Compilation"],
    )

    chosen = select_release([compilation, single])

    assert chosen["id"] == "only"


def test_build_track_picks_the_earliest_release_date():
    later = release_payload(
        release_id="later", group_id="later-group", date="2024-02-06"
    )
    earlier = release_payload(
        release_id="earlier", group_id="earlier-group", date="2017-01-19"
    )

    track = build_track(recording_payload(releases=[later, earlier]))

    assert track.album.id == "rg:earlier-group"


def test_build_track_does_not_let_a_year_only_date_jump_ahead():
    year_only = release_payload(release_id="year", group_id="year-group", date="2017")
    dated = release_payload(release_id="dated", group_id="dated-group", date="2017-03-03")

    track = build_track(recording_payload(releases=[year_only, dated]))

    assert track.album.id == "rg:dated-group"


def test_build_track_honours_an_explicit_release_hint():
    playlist_release = release_payload(
        release_id="saved", group_id="saved-group", title="Saved Edition", date="2019"
    )
    studio = release_payload(release_id="studio", group_id="studio-group", date="2017-04-07")

    track = build_track(
        recording_payload(releases=[studio, playlist_release]), release_mbid="saved"
    )

    assert track.album.id == "rg:saved-group"


def test_build_track_falls_back_when_the_hint_is_not_in_the_releases():
    studio = release_payload(release_id="studio", group_id="studio-group", date="2017-04-07")
    compilation = release_payload(
        release_id="comp",
        group_id="comp-group",
        date="2017",
        secondary_types=["Compilation"],
    )

    track = build_track(
        recording_payload(releases=[compilation, studio]), release_mbid="absent"
    )

    assert track.album.id == "rg:studio-group"


def test_build_track_without_releases_has_an_empty_album():
    track = build_track(recording_payload(releases=[]))

    assert track.album.id == ""
    assert track.album.name == ""
    assert track.album.images == []
    assert track.artists[0].name == "Radiohead"


def test_build_track_tolerates_missing_length():
    assert build_track(recording_payload(length=None)).duration_ms == 0


def test_build_artist_reads_genres_then_tags():
    with_genres = build_artist(artist_payload(genres=["alternative rock"]))
    with_tags = build_artist(artist_payload(tags=["rock", "art rock"]))
    without = build_artist(artist_payload())

    assert with_genres.genres == ["alternative rock"]
    assert with_tags.genres == ["rock", "art rock"]
    assert without.genres is None


def test_recording_helpers_for_the_radio_prompt():
    recording = recording_payload(tags=["rock", "electronic"])

    assert recording_tag_names(recording) == ["rock", "electronic"]
    assert recording_artist_ids(recording) == [ARTIST_MBID]
