"""Gemini client supporting Vertex AI and API-key modes."""

from __future__ import annotations

import concurrent.futures
import logging
import os
from dataclasses import dataclass, field
from typing import Any, cast

from .exceptions import GeminiError
from .settings import Settings

logger = logging.getLogger("dms-watcher")


@dataclass
class GeminiResponse:
    """Wrapper for Gemini API responses with token usage metadata."""

    text: str
    usage: dict = field(default_factory=dict)


class GeminiClient:
    """Lazy Gemini client wrapper for Vertex AI and API-key backends."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._vertex_client: Any | None = None
        self._apikey_model: Any | None = None

    def _init_vertex(self) -> None:
        if self._vertex_client is not None:
            return

        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
        os.environ["GOOGLE_CLOUD_PROJECT"] = self.settings.gcp_project_id
        os.environ["GOOGLE_CLOUD_LOCATION"] = self.settings.gcp_location
        if self.settings.gcp_service_account_json:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.settings.gcp_service_account_json

        from google import genai

        self._vertex_client = genai.Client()
        logger.info(
            "Vertex AI client ready (project=%s, location=%s, model=%s)",
            self.settings.gcp_project_id,
            self.settings.gcp_location,
            self.settings.gemini_model,
        )

    def _init_apikey(self) -> None:
        if self._apikey_model is not None:
            return

        import google.generativeai as genai_legacy

        genai_legacy.configure(api_key=self.settings.gemini_api_key)
        self._apikey_model = genai_legacy.GenerativeModel(self.settings.gemini_model)
        logger.info("Gemini API Key client ready (model=%s)", self.settings.gemini_model)

    def generate(self, prompt: str, temperature: float | None = None) -> GeminiResponse:
        """Generate text from a prompt, returning a GeminiResponse with usage metadata."""
        import time

        last_err = None
        for attempt in range(1, self.settings.max_retry + 1):
            try:
                if self.settings.gemini_backend == "vertex":
                    return self._generate_vertex(prompt, temperature=temperature)
                return self._generate_apikey(prompt, temperature=temperature)
            except Exception as exc:
                last_err = exc
                wait = self.settings.base_wait * attempt
                logger.warning(
                    "GeminiClient generate error (%d/%d): %s -> sleep %.1fs",
                    attempt,
                    self.settings.max_retry,
                    exc,
                    wait,
                )
                time.sleep(wait)
        raise GeminiError(str(last_err)) from last_err

    def generate_json(self, prompt: str, temperature: float = 0.0) -> GeminiResponse:
        """Generate JSON from a prompt, returning a GeminiResponse with usage metadata."""
        import time

        last_err = None
        for attempt in range(1, self.settings.max_retry + 1):
            try:
                if self.settings.gemini_backend == "vertex":
                    return self._generate_vertex(
                        prompt,
                        response_mime_type="application/json",
                        temperature=temperature,
                    )
                return self._generate_apikey_json(prompt, temperature=temperature)
            except Exception as exc:
                last_err = exc
                wait = self.settings.base_wait * attempt
                logger.warning(
                    "GeminiClient generate_json error (%d/%d): %s -> sleep %.1fs",
                    attempt,
                    self.settings.max_retry,
                    exc,
                    wait,
                )
                time.sleep(wait)
        raise GeminiError(str(last_err)) from last_err

    def _call_with_timeout(self, fn, *args, **kwargs) -> Any:  # noqa: ANN001
        """Execute *fn* with a timeout from settings.gemini_timeout_seconds.

        Raises TimeoutError if the SDK call doesn't return within the limit.
        Uses a thread so the calling thread (watcher loop) is not blocked forever.
        """
        timeout = getattr(self.settings, "gemini_timeout_seconds", 120.0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn, *args, **kwargs)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(f"Gemini API call timed out after {timeout}s") from None

    def _generate_vertex(
        self,
        prompt: str,
        response_mime_type: str | None = None,
        temperature: float | None = None,
    ) -> GeminiResponse:
        self._init_vertex()
        from google.genai import types

        config_kwargs: dict[str, object] = {}
        if response_mime_type is not None:
            config_kwargs["response_mime_type"] = response_mime_type
        if temperature is not None:
            config_kwargs["temperature"] = temperature

        kwargs = {"model": self.settings.gemini_model, "contents": prompt}
        if config_kwargs:
            kwargs["config"] = cast(
                Any,
                types.GenerateContentConfig(**cast(Any, config_kwargs)),
            )

        if self._vertex_client is None:
            raise GeminiError("Vertex AI client is not initialized")
        response = self._call_with_timeout(self._vertex_client.models.generate_content, **kwargs)
        usage = _extract_usage(response)
        return GeminiResponse(text=(getattr(response, "text", None) or "").strip(), usage=usage)

    def _generate_apikey(self, prompt: str, temperature: float | None = None) -> GeminiResponse:
        self._init_apikey()
        if self._apikey_model is None:
            raise GeminiError("Gemini API key client is not initialized")
        gen_config = {}
        if temperature is not None:
            gen_config["temperature"] = temperature
        response = self._call_with_timeout(
            self._apikey_model.generate_content,
            prompt,
            generation_config=gen_config or None,
        )
        usage = _extract_usage(response)
        return GeminiResponse(text=(getattr(response, "text", None) or "").strip(), usage=usage)

    def _generate_apikey_json(self, prompt: str, temperature: float = 0.0) -> GeminiResponse:
        self._init_apikey()
        if self._apikey_model is None:
            raise GeminiError("Gemini API key client is not initialized")
        try:
            response = self._call_with_timeout(
                self._apikey_model.generate_content,
                prompt,
                generation_config={
                    "response_mime_type": "application/json",
                    "temperature": temperature,
                },
            )
            usage = _extract_usage(response)
            return GeminiResponse(text=(getattr(response, "text", None) or "").strip(), usage=usage)
        except Exception:
            response = self._call_with_timeout(
                self._apikey_model.generate_content,
                prompt,
                generation_config={"temperature": temperature},
            )
            usage = _extract_usage(response)
            return GeminiResponse(text=(getattr(response, "text", None) or "").strip(), usage=usage)


def _extract_usage(response: Any) -> dict:
    """Extract token usage metadata from a Gemini API response."""
    usage: dict[str, int] = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        usage = {
            "prompt_tokens": getattr(um, "prompt_token_count", 0) or 0,
            "completion_tokens": getattr(um, "candidates_token_count", 0) or 0,
            "total_tokens": getattr(um, "total_token_count", 0) or 0,
        }
    return usage
