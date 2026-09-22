"""``ICore``: plugin lifecycle and account-scoped side effects.

``check_update`` and ``scrobble`` stay unimplemented on purpose (the runtime reports them
as ``unsupported``), matching the old Dart plugin. ``support`` is the one addition: the
contract expects a support contact and the harness probes it.
"""

from musicare_metadata_plugin_sdk import ICore

SUPPORT_URL = "https://github.com/stefare90/musicare_metadata_musicbrainz_plugin/issues"


class MusicBrainzCore(ICore):
    def support(self) -> str:
        return SUPPORT_URL
