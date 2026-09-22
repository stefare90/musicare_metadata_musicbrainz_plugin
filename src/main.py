"""Plugin entry point: the runtime calls ``get_plugin()`` after importing this module."""

from musicare_metadata_plugin_sdk import BaseMetadataPlugin

from .plugin import MusicBrainzPlugin


def get_plugin() -> BaseMetadataPlugin:
    return MusicBrainzPlugin()
