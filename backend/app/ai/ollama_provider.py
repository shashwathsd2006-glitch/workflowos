"""Ollama provider — local, on-premise LLM access over the Ollama HTTP API.

Connection details come from configuration (``OLLAMA_BASE_URL``,
``OLLAMA_MODEL``, ``OLLAMA_TIMEOUT``); nothing is hardcoded. JSON mode
(``format: "json"``) and temperature 0 keep the response as close to the
schema as the model manages — strict validation still happens downstream.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from app.ai.errors import AIProviderError
from app.ai.prompts import ANALYSIS_SYSTEM_PROMPT
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.ai.provider import AIProvider
from app.generator.prompts import (
    SYSTEM_PROMPT as GENERATION_SYSTEM_PROMPT,
    build_user_prompt as build_generation_user_prompt,
)
from app.schemas.ai import (
    ContentAnalysis,
    ContentAnalysisRequest,
    WorkflowUnderstandingInput,
)
from app.schemas.generator import WorkflowGenerationInput

logger = logging.getLogger("workflowos.ai")

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _request_body(self, payload: WorkflowUnderstandingInput) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(payload)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

    def _generation_request_body(
        self, payload: WorkflowGenerationInput
    ) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
                {"role": "user", "content": build_generation_user_prompt(payload)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

    async def _chat(self, body: Dict[str, Any]) -> str:
        """POST a chat request and return the raw assistant message content.

        Every transport failure — timeout, HTTP error, unreachable host,
        undecodable body, empty content — becomes an ``AIProviderError`` so
        the service layer can map it to a controlled 502.
        """
        url = f"{self.base_url}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            logger.warning("Ollama timeout calling %s", url)
            raise AIProviderError(
                f"Local Ollama server timed out after {self.timeout}s at "
                f"{self.base_url} (model {self.model})"
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("Ollama HTTP %s from %s", exc.response.status_code, url)
            raise AIProviderError(
                f"Local Ollama server returned HTTP "
                f"{exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Ollama unreachable at %s: %s", url, exc)
            raise AIProviderError(
                f"Local Ollama server unavailable at {self.base_url}"
            ) from exc

        content = (body.get("message") or {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            logger.warning("Ollama returned an empty message for model %s", self.model)
            raise AIProviderError(
                f"Local Ollama server returned an empty response for model "
                f"{self.model}"
            )
        return content

    async def generate_workflow_understanding(
        self, payload: WorkflowUnderstandingInput
    ) -> str:
        return await self._chat(self._request_body(payload))

    async def generate_workflow_draft(
        self, payload: WorkflowGenerationInput
    ) -> str:
        return await self._chat(self._generation_request_body(payload))

    def _analysis_request_body(self, payload: ContentAnalysisRequest) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Task:\n{payload.task}\n\n"
                        f"Content to analyse:\n{payload.content}"
                    ),
                },
            ],
            "stream": False,
            "format": ContentAnalysis.model_json_schema(),
            "options": {"temperature": 0},
        }

    async def analyze_content(self, payload: ContentAnalysisRequest) -> str:
        """Real local inference over arbitrary content.

        Uses Ollama's structured-output ``format`` field, so the model is
        constrained to the ``ContentAnalysis`` schema. A transport failure
        still surfaces as ``AIProviderError`` — never as a fabricated answer.
        """
        return await self._chat(self._analysis_request_body(payload))
