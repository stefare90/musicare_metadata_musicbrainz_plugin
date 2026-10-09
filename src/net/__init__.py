"""Network transport: the shared stdlib HTTP client and its etiquette."""

from .http import HttpClient, USER_AGENT, RATE_LIMITS, user_agent_for

__all__ = ["HttpClient", "USER_AGENT", "RATE_LIMITS", "user_agent_for"]
