"""Gemini client supporting Vertex AI and API-key modes."""

from __future__ import annotations

import concurrent.futures
import copy
import logging
import math
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import NameResolutionError

from .exceptions import GatewayError, GeminiError
from .settings import Settings

logger = logging.getLogger("dms-watcher")
gateway_logger = logging.getLogger("dms.gateway")


@dataclass
class GeminiResponse:
    """Wrapper for Gemini API responses with token usage metadata."""

    text: str
    usage: dict = field(default_factory=dict)
    route: str | None = None
    response_id: str | None = None
    model_actual: str | None = None


class GeminiClient:
    """Lazy Gemini client wrapper for Vertex AI and API-key backends."""

    def __init__(self, settings: Settings, usage_tracker=None, on_attempt=None) -> None:
        self.settings = settings
        self._vertex_client: Any | None = None
        self._apikey_model: Any | None = None
        self._usage_tracker = usage_tracker
        self._on_attempt = on_attempt
        self._state_lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._generation = 0
        self._unreachable = 0
        self._incident_id = None
        self._incident_started = 0.0
        self._direct_attempted = 0
        self._direct_succeeded = 0
        self._probe_inflight = False
        self._probe_after = 0.0
        self._direct_client = None

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

    def generate(self, prompt: str, temperature: float | None = None, *,
                 actor: str | None = None, job_id: str | None = None,
                 operation: str = "generate") -> GeminiResponse:
        """Generate text from a prompt, returning a GeminiResponse with usage metadata."""
        import time

        if self.settings.gemini_backend == "gateway":
            return self._generate_gateway(prompt, temperature, False, actor, job_id, operation)

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

    def generate_json(self, prompt: str, temperature: float = 0.0, *,
                      actor: str | None = None, job_id: str | None = None,
                      operation: str = "generate_json") -> GeminiResponse:
        """Generate JSON from a prompt, returning a GeminiResponse with usage metadata."""
        import time

        if self.settings.gemini_backend == "gateway":
            return self._generate_gateway(prompt, temperature, True, actor, job_id, operation)

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

    def _generate_gateway(self, prompt, temperature, json_mode, actor, job_id, operation):
        config = copy.copy(self.settings)
        self._validate_gateway(config, actor)
        try:
            from .logging_config import ensure_gateway_logging
            ensure_gateway_logging(config.log_dir)
        except Exception:
            raise GatewayError("accounting") from None
        fallback = self._fallback_available(config)
        event = dict(operation_id=uuid.uuid4().hex,
                     actor=actor, job_id=job_id, route="gateway", incident_id=None,
                     model_requested=config.gateway_model, call_type=operation)
        with self._state_lock:
            generation = self._generation
            incident = self._incident_id if fallback else None
            probe = bool(incident and not self._probe_inflight and time.monotonic() >= self._probe_after)
            if probe:
                self._probe_inflight = True
        if probe:
            return self._probe(config, event, incident, generation, prompt, temperature, json_mode, actor)
        if incident:
            return self._direct_attempt(config, event, incident, prompt, temperature, json_mode, actor)
        for number in range(1, config.max_retry + 1):
            try:
                result = self._attempt(config, dict(event, attempt_id=uuid.uuid4().hex),
                                       prompt, temperature, json_mode, actor)
            except GatewayError as error:
                if error.category == "unreachable":
                    with self._state_lock:
                        if generation == self._generation:
                            self._unreachable += 1
                            if fallback and self._unreachable >= config.fallback_fail_threshold:
                                self._generation += 1
                                self._incident_id = uuid.uuid4().hex
                                self._incident_started = time.monotonic()
                                self._probe_after = self._incident_started + config.fallback_retry_after_s
                                self._probe_inflight = False
                                self._direct_attempted = self._direct_succeeded = 0
                                gateway_logger.warning("fallback_open", extra={"incident_id": self._incident_id})
                        incident = self._incident_id if fallback else None
                    if incident:
                        return self._direct_attempt(config, event, incident, prompt, temperature, json_mode, actor)
                if not error.retryable or number == config.max_retry:
                    raise
                wait = error.retry_after if error.retry_after is not None else config.base_wait * number
                time.sleep(min(wait, 60.0))
            else:
                with self._state_lock:
                    if generation == self._generation:
                        self._unreachable = 0
                return result

    def _probe(self, config, event, incident, generation, prompt, temperature, json_mode, actor):
        event = dict(event, incident_id=incident, attempt_id=uuid.uuid4().hex)
        try:
            result = self._attempt(config, event, prompt, temperature, json_mode, actor)
        except GatewayError as error:
            with self._state_lock:
                if generation == self._generation:
                    if error.category == "unreachable":
                        self._probe_inflight = False
                        self._probe_after = time.monotonic() + config.fallback_retry_after_s
                    else:
                        self._close_incident(error.category)
            if error.category == "unreachable":
                return self._direct_attempt(config, event, incident, prompt, temperature, json_mode, actor)
            raise
        else:
            with self._state_lock:
                if generation == self._generation:
                    self._close_incident("recovered")
            return result
        finally:
            with self._state_lock:
                if generation == self._generation:
                    self._probe_inflight = False

    def _close_incident(self, reason):
        gateway_logger.info("fallback_closed", extra={
            "incident_id": self._incident_id, "error_category": reason,
            "duration_ms": int((time.monotonic() - self._incident_started) * 1000),
            "direct_attempted": self._direct_attempted, "direct_succeeded": self._direct_succeeded})
        self._generation += 1
        self._incident_id = None
        self._unreachable = 0
        self._direct_attempted = self._direct_succeeded = 0
        self._probe_inflight = False

    @staticmethod
    def _fallback_available(config):
        if not getattr(config, "fallback_enabled", False):
            return False
        available = bool(config.gcp_project_id and config.gcp_location and config.gemini_model
                         and config.gcp_service_account_json
                         and Path(config.gcp_service_account_json).is_file())
        if not available:
            gateway_logger.warning("fallback_disabled_missing_direct_configuration")
        return available

    def _direct_attempt(self, config, event, incident, prompt, temperature, json_mode, actor):
        event = dict(event, route="direct_vertex", incident_id=incident,
                     attempt_id=uuid.uuid4().hex)
        return self._attempt(config, event, prompt, temperature, json_mode, actor)

    def _direct_request(self, config, prompt, temperature, json_mode):
        from google import genai
        from google.genai import types
        from google.oauth2 import service_account

        with self._init_lock:
            if self._direct_client is None:
                credentials = service_account.Credentials.from_service_account_file(
                    config.gcp_service_account_json,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"])
                self._direct_client = genai.Client(
                    vertexai=True, project=config.gcp_project_id, location=config.gcp_location,
                    credentials=credentials,
                    http_options=types.HttpOptions(
                        timeout=max(1, int(config.gemini_timeout_seconds * 1000)),
                        retry_options=types.HttpRetryOptions(attempts=1)))
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if json_mode:
            options["response_mime_type"] = "application/json"
        response = self._direct_client.models.generate_content(
            model=config.gemini_model, contents=prompt,
            config=types.GenerateContentConfig(**options))
        usage = {}
        metadata = getattr(response, "usage_metadata", None)
        if metadata is not None:
            for source, target in (("prompt_token_count", "prompt_tokens"),
                                   ("candidates_token_count", "completion_tokens"),
                                   ("total_token_count", "total_tokens")):
                value = getattr(metadata, source, None)
                if type(value) is int and value >= 0:
                    usage[target] = value
            for source, group, target in (
                ("thoughts_token_count", "completion_tokens_details", "reasoning_tokens"),
                ("cached_content_token_count", "prompt_tokens_details", "cached_tokens")):
                value = getattr(metadata, source, None)
                if type(value) is int and value >= 0:
                    usage[group] = {target: value}
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            error = GatewayError("response", outcome_unknown=True)
            error.usage = usage or None
            raise error
        return GeminiResponse(text=text.strip(), usage=usage, route="direct_vertex",
                              model_actual=_response_token(getattr(response, "model_version", None)))

    def _audit(self, config, **event):
        try:
            gateway_logger.info(event["event_kind"], extra=event)
            if self._usage_tracker is None:
                with self._init_lock:
                    if self._usage_tracker is None:
                        from .usage_tracker import UsageTracker
                        self._usage_tracker = UsageTracker(Path(config.work_dir) / "classification_jobs.db")
            self._usage_tracker.record_attempt(**event)
            if event.get("event_kind") == "attempt_finished" and self._on_attempt:
                self._on_attempt(event)
        except Exception:
            raise GatewayError("accounting", outcome_unknown=event["event_kind"] == "attempt_finished") from None

    def _attempt(self, config, event, prompt, temperature, json_mode, actor):
        self._audit(config, event_kind="attempt_started", outcome="started", **event)
        start = time.monotonic()
        direct = event["route"] == "direct_vertex"
        if direct:
            with self._state_lock:
                if event["incident_id"] == self._incident_id:
                    self._direct_attempted += 1
        try:
            response = (self._direct_request(config, prompt, temperature, json_mode) if direct
                        else self._gateway_request(config, prompt, temperature, json_mode, actor))
        except Exception as exc:
            if direct:
                error = exc if isinstance(exc, GatewayError) else GatewayError("direct", outcome_unknown=True)
            else:
                error = _transport_error(exc)
            self._audit(config, event_kind="attempt_finished", **event,
                        outcome="unknown" if error.outcome_unknown else "failed",
                        error_category=error.category, usage=error.usage,
                        response_id=error.response_id, model_actual=error.model_actual,
                        duration_ms=int((time.monotonic() - start) * 1000))
            raise error from None
        self._audit(config,
            event_kind="attempt_finished", outcome="success", **event,
            model_actual=response.model_actual, response_id=response.response_id,
            usage=response.usage or None, estimated_cost_usd=None,
            duration_ms=int((time.monotonic() - start) * 1000))
        if direct:
            with self._state_lock:
                if event["incident_id"] == self._incident_id:
                    self._direct_succeeded += 1
        return response

    @staticmethod
    def _validate_gateway(config, actor):
        if not isinstance(actor, str) or not actor or actor != actor.strip() or any(
                ord(c) < 33 or ord(c) > 126 for c in actor):
            raise GatewayError("identity")
        try:
            url = config.gateway_chat_completions_url
            parsed = urlsplit(url)
            key, model = config.gateway_api_key, config.gateway_model
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or not parsed.path or parsed.path == "/"
                    or any(c.isspace() or ord(c) < 32 for c in url)
                    or (parsed.scheme == "http" and not config.gateway_allow_insecure_http)
                    or not key or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key)
                    or any(c in key for c in "{}<>`")
                    or not model or any(c.isspace() or ord(c) < 32 for c in model)
                    or type(config.max_retry) is not int or config.max_retry < 1
                    or not math.isfinite(config.base_wait) or config.base_wait < 0
                    or not math.isfinite(config.gemini_timeout_seconds) or config.gemini_timeout_seconds <= 0):
                raise ValueError
            _ = parsed.port
            if getattr(config, "fallback_enabled", False):
                if (type(config.fallback_fail_threshold) is not int
                        or not 1 <= config.fallback_fail_threshold <= config.max_retry
                        or not math.isfinite(config.fallback_retry_after_s)
                        or config.fallback_retry_after_s <= 0):
                    raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise GatewayError("configuration") from None

    def _gateway_request(self, config, prompt, temperature, json_mode, actor):
        body = {"model": config.gateway_model, "messages": [{"role": "user", "content": prompt}],
                "user": actor}
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        with requests.Session() as session:
            # No adapter retries and no redirects on inference POSTs.
            response = session.post(config.gateway_chat_completions_url, json=body,
                                    headers={"Authorization": "Bearer " + config.gateway_api_key,
                                             "X-User": actor}, allow_redirects=False,
                                    timeout=config.gemini_timeout_seconds)
            status = response.status_code
            if status == 429:
                error = GatewayError("quota", retryable=True)
                try:
                    delay = float(response.headers.get("Retry-After", ""))
                    if math.isfinite(delay) and delay >= 0:
                        error.retry_after = min(delay, 60.0)
                except ValueError:
                    pass
                raise error
            if status != 200:
                category = ("auth" if status in {401, 403} else "redirect" if 300 <= status < 400
                            else "outcome_unknown" if status >= 500 else "request")
                raise GatewayError(category, outcome_unknown=status >= 500)
            payload = None
            usage = None
            try:
                payload = response.json()
                raw_usage = payload.get("usage")
                usage = _gateway_usage(raw_usage)
                if raw_usage is not None and usage is None:
                    raise ValueError
                text = payload["choices"][0]["message"]["content"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError
                return GeminiResponse(text=text, usage=usage or {}, route="gateway",
                                      response_id=_response_token(payload.get("id")),
                                      model_actual=_response_token(payload.get("model")))
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                error = GatewayError("response", outcome_unknown=True)
                error.usage = usage
                if isinstance(payload, dict):
                    error.response_id = _response_token(payload.get("id"))
                    error.model_actual = _response_token(payload.get("model"))
                raise error from None

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


def _transport_error(exc):
    if isinstance(exc, GatewayError):
        return exc
    if isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ProxyError,
                        requests.exceptions.InvalidURL, requests.exceptions.InvalidSchema)):
        return GatewayError("configuration")
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return GatewayError("unreachable", retryable=True)
    # Inspect typed causes only: a generic reset/message never proves pre-send.
    if isinstance(exc, requests.exceptions.ConnectionError):
        pending, visited = [exc], set()
        while pending:
            cause = pending.pop()
            if id(cause) in visited:
                continue
            visited.add(id(cause))
            if isinstance(cause, (ConnectionRefusedError, socket.gaierror, NameResolutionError)):
                return GatewayError("unreachable", retryable=True)
            if isinstance(cause, BaseException):
                pending.extend([cause.__cause__, getattr(cause, "reason", None)])
                pending.extend(arg for arg in cause.args if isinstance(arg, BaseException))
    return GatewayError("outcome_unknown", outcome_unknown=True)


def _response_token(value):
    return value if isinstance(value, str) and 0 < len(value) <= 256 and all(
        c.isalnum() or c in "-_.:/" for c in value) else None


def _gateway_usage(raw):
    if not isinstance(raw, dict):
        return None
    usage = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_tokens_details", "completion_tokens_details"):
        if key not in raw:
            continue
        value = raw[key]
        if key.endswith("_details"):
            if not isinstance(value, dict):
                return None
            value = {k: v for k, v in value.items() if k in {
                "cached_tokens", "reasoning_tokens", "audio_tokens",
                "accepted_prediction_tokens", "rejected_prediction_tokens"}}
            if any(type(v) is not int or v < 0 for v in value.values()):
                return None
        elif type(value) is not int or value < 0:
            return None
        usage[key] = value
    return usage


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
