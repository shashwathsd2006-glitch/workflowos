"""AI provider abstraction.

Routes and services depend on :class:`AIProvider` only; whether the work is
done by a local Ollama server or the deterministic MockProvider used in tests
is decided by configuration (``AI_PROVIDER``), never by call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.schemas.ai import ContentAnalysisRequest, WorkflowUnderstandingInput
from app.schemas.generator import WorkflowGenerationInput


class AIProvider(ABC):
    """Interface every AI provider must implement.

    ``generate_workflow_understanding`` and ``generate_workflow_draft``
    return the provider's *raw* text response. JSON extraction, schema
    validation and persistence all happen once in the service layer so
    providers never duplicate that logic.
    """

    name: str = "provider"
    model: str = ""

    @abstractmethod
    async def generate_workflow_understanding(
        self, payload: WorkflowUnderstandingInput
    ) -> str:
        """Return the raw model response for the given workflow evidence."""

    async def generate_workflow_draft(self, payload: WorkflowGenerationInput) -> str:
        """Return the raw model response for workflow draft generation.

        Non-abstract so simple providers only need the understanding method;
        the generator uses providers that override this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support workflow generation"
        )

    async def analyze_content(self, payload: ContentAnalysisRequest) -> str:
        """Return the raw model response for a content analysis task.

        Used by the ``ai_analyze_content`` workflow step: real content (for
        example a real Gmail message) is sent to the configured provider and
        the answer is validated against :class:`ContentAnalysis`.

        Non-abstract for the same reason as ``generate_workflow_draft``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support content analysis"
        )


def get_provider(provider_name: Optional[str] = None) -> AIProvider:
    """Build the provider selected by configuration (``AI_PROVIDER``).

    ``ollama`` is the production/demo default; ``mock`` is used by the test
    suite so pytest never needs a running model.
    """
    from app.config import settings
    from app.ai.mock_provider import MockProvider
    from app.ai.ollama_provider import OllamaProvider

    selected = (provider_name or settings.ai_provider).strip().lower()
    if selected == "mock":
        return MockProvider()
    return OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
    )
