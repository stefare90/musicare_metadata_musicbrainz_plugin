"""Network transport: the shared stdlib HTTP client and its etiquette."""

from .http import (
    HttpClient,
    HttpAuthError,
    HttpStatusError,
    USER_AGENT,
    RATE_LIMITS,
    user_agent_for,
)

__all__ = [
    "HttpClient",
    "HttpAuthError",
    "HttpStatusError",
    "USER_AGENT",
    "RATE_LIMITS",
    "user_agent_for",
]
