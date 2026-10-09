"""The entry point the runtime imports must satisfy the SDK contract."""

import json
from pathlib import Path

from musicare_metadata_plugin_sdk import BaseMetadataPlugin, SearchCategory

from src.main import get_plugin
from src.plugin import USER_AGENT

_MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "plugin.json").read_text())


def test_get_plugin_returns_a_metadata_plugin():
    plugin = get_plugin()

    assert isinstance(plugin, BaseMetadataPlugin)
    assert plugin.id == "org.musicare.metadata.musicbrainz"
    assert plugin.name == "MusicBrainz & ListenBrainz"
    # The runtime reports ``plugin.version``, and the build validates ``plugin.json``:
    # they are two separate sources that must never drift (a release bumped only the
    # manifest and the runtime kept reporting the old version).
    assert plugin.version == _MANIFEST["version"]
    # The User-Agent must carry the shipped version, not a stale literal: providers
    # identify (and may block) callers by it.
    assert _MANIFEST["version"] in USER_AGENT
    # The client must actually send it: the wiring (not just the constant) is
    # what the providers see.
    assert plugin._search._client._user_agent == USER_AGENT


def test_implemented_interfaces_behave_without_network():
    plugin = get_plugin()

    assert plugin.core.support().startswith("https://")
    assert plugin.search.chips() == [
        SearchCategory.TRACKS,
        SearchCategory.ALBUMS,
        SearchCategory.ARTISTS,
        SearchCategory.PLAYLISTS,
    ]
