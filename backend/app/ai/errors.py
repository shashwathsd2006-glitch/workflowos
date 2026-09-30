"""Controlled exceptions for the AI Understanding module.

Both errors map to HTTP 502 at the API layer: the upstream AI provider is
either unreachable (``AIProviderError``) or produced unusable output
(``AIResponseError``). Neither ever crashes the application.
"""

from __future__ import annotations


class AIError(Exception):
    """Base class for AI module failures."""


class AIProviderError(AIError):
    """The provider could not be reached or returned no usable content."""


class AIResponseError(AIError):
    """The provider answered, but the response is not valid understanding JSON."""
