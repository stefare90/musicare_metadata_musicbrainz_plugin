"""In-memory credentials shared between the auth contract and the user library.

The runtime hands an :class:`~musicare_plugin_auth.AuthContext` only to the ``IAuth``
methods, but ``IUser`` needs the ListenBrainz token to read and write the user's library,
and ``IAuth.status`` must answer without a ``validate-token`` call. The plugin therefore
keeps the token and the account name in this small object: ``IAuth`` refreshes both from
``auth.json`` and ``IUser`` reads the token. The file lives in the plugin's private data
directory (``ctx.data_dir``, which is its own path in ``/load_plugin``) and the token is
never sent to the host.
"""

from typing import Optional

from musicare_metadata_plugin_sdk import AuthContext

AUTH_FILE = "auth.json"


class Credentials:
    def __init__(self) -> None:
        self._token = ""
        self._user = ""

    @property
    def token(self) -> str:
        return self._token

    @property
    def user(self) -> str:
        return self._user

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def load(self, ctx: AuthContext) -> bool:
        """Refresh from the plugin data directory; returns whether a token is stored."""
        payload = ctx.read_json(AUTH_FILE) or {}
        token = payload.get("token") if isinstance(payload, dict) else None
        user = payload.get("user") if isinstance(payload, dict) else None
        self._token = str(token) if token else ""
        self._user = str(user) if user else ""
        return self.is_authenticated

    def store(self, ctx: AuthContext, token: str, user: str) -> None:
        self._token = token
        self._user = user
        ctx.write_json(AUTH_FILE, {"token": token, "user": user})

    def clear(self, ctx: AuthContext) -> None:
        self._token = ""
        self._user = ""
        ctx.delete(AUTH_FILE)

    def authorization_header(self) -> Optional[dict]:
        return {"Authorization": f"Token {self._token}"} if self._token else None
