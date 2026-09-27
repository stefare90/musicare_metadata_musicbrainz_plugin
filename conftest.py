"""Pytest bootstrap: make the plugin's ``src`` package importable from the repo root.

Also loads the developer's local secret store when a live test needs a token and the
variable is not already exported. The store lives **outside every repository**
(``$XDG_CONFIG_HOME/musicare/test.env`` or ``~/.config/musicare/test.env``, mode 0600) and
is never committed; an interactive shell sources it from ``~/.zshrc``, while this fallback
covers non-interactive runs such as an agent invoking pytest.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_SECRET_KEYS = ("LISTENBRAINZ_TOKEN",)


def _load_local_secrets() -> None:
    if all(os.environ.get(key) for key in _SECRET_KEYS):
        return
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(config_home, "musicare", "test.env")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_local_secrets()
