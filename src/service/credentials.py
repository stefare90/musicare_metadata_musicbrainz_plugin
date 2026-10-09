"""In-memory token shared between the auth state machine and the user library.

The runtime hands an :class:`~musicare_metadata_plugin_sdk.AuthContext` only to the
``IAuth`` methods, but ``IUser`` needs the ListenBrainz token to read and write the user's
library. The plugin therefore keeps the token in this small object: ``IAuth`` refreshes it
from ``auth.json`` whenever the host asks, and ``IUser`` reads it. The token is persisted
in the plugin's own data directory, never sent to the host.
"""

from typing import Any, Optional

from musicare_metadata_plugin_sdk import AuthContext

AUTH_FILE = "auth.json"


class Credentials:
    def __init__(self) -> None:
        self._token = ""

    @property
    def token(self) -> str:
        return self._token

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def load(self, ctx: AuthContext) -> bool:
        """Refresh from the plugin data directory; returns whether a token is stored."""
        payload = ctx.read_json(AUTH_FILE) or {}
        token = payload.get("token") if isinstance(payload, dict) else None
        self._token = str(token) if token else ""
        return self.is_authenticated

    def store(self, ctx: AuthContext, token: str) -> None:
        self._token = token
        ctx.write_json(AUTH_FILE, {"token": token})

    def clear(self, ctx: AuthContext) -> None:
        self._token = ""
        ctx.delete(AUTH_FILE)

    def authorization_header(self) -> Optional[dict]:
        return {"Authorization": f"Token {self._token}"} if self._token else None
