"""Live provider tests (network required): ``pytest -m live``.

These are excluded from the default run by ``pytest.ini``. They exercise the real
MusicBrainz, ListenBrainz and Wikidata endpoints through the same entry point the runtime
uses, so they are the last check before the end-to-end harness.
"""

import os
import time

import pytest

from musicare_metadata_plugin_sdk import (
    Authenticated,
    AuthContext,
    AuthRequiredError,
    RateLimitedError,
    SearchCategory,
)

from src.http import HttpClient
from src.images.wikidata import WikidataArtistImages
from src.main import get_plugin

pytestmark = pytest.mark.live

RADIOHEAD_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
# Wolfgang Muthspiel: a niche artist, enough listeners to have a ranking.
NICHE_ARTIST_MBID = "955ff0c2-502a-4aea-92fd-1810fba171c2"
# Arianna Neikrug: no ListenBrainz listens at the time of writing.
NO_LISTENS_ARTIST_MBID = "0b1b6c28-d84a-43e9-a326-3b87bff7b656"


def _retry(call, attempts: int = 3):
    """Retry a live call when the provider throttles, with exponential backoff."""
    for attempt in range(attempts):
        try:
            return call()
        except RateLimitedError:
            if attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))


def test_search_details_round_trip():
    _retry(_search_details_round_trip)


def _search_details_round_trip():
    plugin = get_plugin()

    tracks = plugin.search.tracks("radiohead", limit=5)
    assert tracks.items
    assert plugin.track.get_track(tracks.items[0].id).id == tracks.items[0].id

    albums = plugin.search.albums("radiohead", limit=5)
    assert albums.items
    assert plugin.album.get_album(albums.items[0].id).name

    artists = plugin.search.artists("radiohead", limit=5)
    assert artists.items
    assert plugin.artist.get_artist(artists.items[0].id).name


def test_search_playlists_filters_the_curated_account():
    plugin = get_plugin()

    assert SearchCategory.PLAYLISTS in plugin.search.chips()
    page = plugin.search.playlists("weekly", limit=5)

    assert page.items
    assert page.total >= len(page.items)


def test_album_tracks_are_complete():
    plugin = get_plugin()
    album = plugin.search.albums("radiohead", limit=1).items[0]

    page = plugin.album.tracks(album.id, limit=5)

    assert page.items
    assert all(track.album.id == album.id for track in page.items)


def test_artist_has_images_from_wikidata():
    artist = get_plugin().artist.get_artist(RADIOHEAD_MBID)

    assert artist.name == "Radiohead"
    assert artist.images
    assert artist.images[0].url.startswith("https://commons.wikimedia.org/")


def test_wikidata_maps_a_musicbrainz_id_to_the_commons_sizes():
    images = WikidataArtistImages(HttpClient(rate_limits={})).resolve([RADIOHEAD_MBID])

    assert [image.width for image in images[RADIOHEAD_MBID]] == [56, 250, 500, 1000]
    assert images[RADIOHEAD_MBID][0].url.startswith("https://commons.wikimedia.org/")


def test_browse_is_available_without_authentication():
    plugin = get_plugin()

    assert [section.id for section in plugin.browse.sections().items] == [
        "created_for_you",
        "top_artist_radios",
        "mood_playlists",
    ]
    assert plugin.browse.section_items("mood_playlists").items
    context = AuthContext(data_dir="/tmp/musicare-metadata-live", plugin_id=plugin.id)
    assert plugin.auth.is_authenticated(context) is False


def _authenticated_plugin(tmp_path):
    token = os.environ.get("LISTENBRAINZ_TOKEN", "").strip()
    if not token:
        pytest.skip("LISTENBRAINZ_TOKEN is not set")
    plugin = get_plugin()
    context = AuthContext(data_dir=str(tmp_path), plugin_id=plugin.id)
    assert isinstance(plugin.auth.complete(context, {"token": token}), Authenticated)
    return plugin


def test_radio_returns_tracks_when_authenticated(tmp_path):
    _retry(lambda: _radio_round_trip(tmp_path))


def _radio_round_trip(tmp_path):
    plugin = _authenticated_plugin(tmp_path)
    # Anchor on Radiohead: `lb-radio` can answer 400 for a seed whose artist cannot
    # generate a radio, and a free-text search can return such a track.
    seed = plugin.artist.top_tracks(RADIOHEAD_MBID, limit=1).items[0]

    tracks = plugin.track.radio(seed.id)

    assert tracks


def test_radio_playlist_returns_tracks_when_authenticated(tmp_path):
    _retry(lambda: _radio_playlist_round_trip(tmp_path))


def _radio_playlist_round_trip(tmp_path):
    plugin = _authenticated_plugin(tmp_path)

    page = plugin.playlist.tracks("radio:tag:chill", limit=5)

    assert page.items


def test_top_tracks_require_a_token():
    plugin = get_plugin()

    with pytest.raises(AuthRequiredError):
        plugin.artist.top_tracks(RADIOHEAD_MBID)


def test_top_tracks_are_ranked_by_real_listen_count(tmp_path):
    _retry(lambda: _top_tracks_round_trip(tmp_path))


def _top_tracks_round_trip(tmp_path):
    plugin = _authenticated_plugin(tmp_path)

    page = plugin.artist.top_tracks(RADIOHEAD_MBID, offset=0, limit=50)

    assert page.total == 50
    assert len(page.items) == 50
    ids = [track.id for track in page.items]
    assert len(ids) == len(set(ids))
    assert page.items[0].name == "Karma Police"
    assert page.items[0].album.images


def test_top_tracks_are_populated_for_a_niche_artist(tmp_path):
    _retry(lambda: _niche_artist_round_trip(tmp_path))


def _niche_artist_round_trip(tmp_path):
    plugin = _authenticated_plugin(tmp_path)

    page = plugin.artist.top_tracks(NICHE_ARTIST_MBID, limit=5)

    assert page.items
    assert page.total <= 50
    assert page.items[0].name == "Maya"


def test_top_tracks_are_empty_when_the_artist_has_no_listens(tmp_path):
    _retry(lambda: _no_listens_round_trip(tmp_path))


def _no_listens_round_trip(tmp_path):
    plugin = _authenticated_plugin(tmp_path)

    page = plugin.artist.top_tracks(NO_LISTENS_ARTIST_MBID, limit=5)

    assert page.items == []
    assert page.total == 0
