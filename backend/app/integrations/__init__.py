"""External service integrations (Phase 8).

Every external system is reached through ``IntegrationProvider`` and resolved
via ``app.integrations.registry``. Tokens are encrypted at rest and are never
returned by any API response.
"""
