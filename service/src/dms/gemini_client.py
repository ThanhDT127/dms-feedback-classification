"""Gemini client supporting Vertex AI and API-key modes."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

from .exceptions import GeminiError, GeminiStreamError, GeminiStreamTimeout
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
        # google-genai cho stream và system_instruction (design b04 D2); đường cũ không dùng.
        self._genai_apikey_client: Any | None = None
        self._genai_stream_client: Any | None = None

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

    def _init_genai_apikey(self) -> None:
        if self._genai_apikey_client is not None:
            return

        from google import genai

        self._genai_apikey_client = genai.Client(api_key=self.settings.gemini_api_key)
        logger.info("google-genai API key client ready (model=%s)", self.settings.gemini_model)

    def _genai_client(self) -> Any:
        """Client ``google-genai`` cho cả hai backend (design b04 D2).

        Vertex dùng client sẵn có; API key tạo client riêng bằng ``genai.Client(api_key=...)``.
        Đường ``generate``/``generate_json`` cũ không đi qua đây.
        """
        if self.settings.gemini_backend == "vertex":
            self._init_vertex()
            if self._vertex_client is None:
                raise GeminiError("Vertex AI client is not initialized")
            return self._vertex_client
        self._init_genai_apikey()
        if self._genai_apikey_client is None:
            raise GeminiError("Gemini API key client is not initialized")
        return self._genai_apikey_client

    def _stream_client(self) -> Any:
        """Client riêng cho stream, có ``HttpOptions.timeout`` làm chặn ở tầng mạng (D4)."""
        if self._genai_stream_client is not None:
            return self._genai_stream_client

        from google import genai
        from google.genai import types

        timeout_ms = int(
            float(getattr(self.settings, "chat_stream_total_timeout_seconds", 60.0)) * 1000
        )
        http_options = types.HttpOptions(timeout=timeout_ms)
        if self.settings.gemini_backend == "vertex":
            self._init_vertex()  # đặt biến môi trường cho Vertex
            self._genai_stream_client = genai.Client(http_options=http_options)
        else:
            self._genai_stream_client = genai.Client(
                api_key=self.settings.gemini_api_key, http_options=http_options
            )
        return self._genai_stream_client

    def reset_clients(self) -> None:
        """Xoá mọi client đã khởi tạo lười; watcher gọi khi setting Gemini đổi (D8)."""
        self._vertex_client = None
        self._apikey_model = None
        self._genai_apikey_client = None
        self._genai_stream_client = None

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

    def generate_json(
        self,
        prompt: str,
        temperature: float = 0.0,
        *,
        system_instruction: str | None = None,
        model: str | None = None,
    ) -> GeminiResponse:
        """Generate JSON from a prompt, returning a GeminiResponse with usage metadata.

        Không truyền ``system_instruction``/``model`` thì đi **đúng nhánh cũ** (design b04 D1).
        """
        import time

        last_err = None
        for attempt in range(1, self.settings.max_retry + 1):
            try:
                if system_instruction is not None or model is not None:
                    return self._generate_genai_json(
                        prompt,
                        temperature=temperature,
                        system_instruction=system_instruction,
                        model=model,
                    )
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

    def _generate_genai_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        system_instruction: str | None = None,
        model: str | None = None,
    ) -> GeminiResponse:
        """JSON qua ``google-genai`` cho cả hai backend, có ``system_instruction``."""
        client = self._genai_client()
        from google.genai import types

        config_kwargs: dict[str, object] = {
            "response_mime_type": "application/json",
            "temperature": temperature,
        }
        if system_instruction is not None:
            config_kwargs["system_instruction"] = system_instruction

        response = self._call_with_timeout(
            client.models.generate_content,
            model=model or self.settings.gemini_model,
            contents=prompt,
            config=cast(Any, types.GenerateContentConfig(**cast(Any, config_kwargs))),
        )
        return GeminiResponse(
            text=(getattr(response, "text", None) or "").strip(), usage=_extract_usage(response)
        )

    def stream(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiStream:
        """Stream văn bản qua ``google-genai`` cho cả hai backend (design b04 D2–D7)."""
        target_model = model or self.settings.gemini_model

        # Dựng config ở luồng gọi: lần import SDK đầu tiên tốn cả giây, không được tính vào
        # hạn chờ chunk đầu (vốn bắt đầu đếm khi thread đọc chạy).
        from google.genai import types

        config_kwargs: dict[str, object] = {}
        if system_instruction is not None:
            config_kwargs["system_instruction"] = system_instruction
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = max_output_tokens
        budget = getattr(self.settings, "chat_gemini_thinking_budget", None)
        if budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))

        request_kwargs: dict[str, Any] = {"model": target_model, "contents": prompt}
        if config_kwargs:
            request_kwargs["config"] = cast(
                Any, types.GenerateContentConfig(**cast(Any, config_kwargs))
            )

        def open_stream() -> Iterator[Any]:
            client = self._stream_client()
            return client.models.generate_content_stream(**request_kwargs)

        return GeminiStream(
            open_stream=open_stream,
            model=target_model,
            first_chunk_timeout=float(
                getattr(self.settings, "chat_stream_first_chunk_timeout_seconds", 15.0)
            ),
            idle_timeout=float(getattr(self.settings, "chat_stream_idle_timeout_seconds", 10.0)),
            total_timeout=float(getattr(self.settings, "chat_stream_total_timeout_seconds", 60.0)),
            max_retry=int(getattr(self.settings, "chat_llm_max_retry", 1)),
        )

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


def _extract_stream_usage(response: Any) -> dict[str, int]:
    """Usage của stream; ``completion_tokens`` gồm cả thinking token (design b04 D7)."""
    um = getattr(response, "usage_metadata", None)
    if not um:
        return {}
    prompt = int(getattr(um, "prompt_token_count", 0) or 0)
    candidates = int(getattr(um, "candidates_token_count", 0) or 0)
    thoughts = int(getattr(um, "thoughts_token_count", 0) or 0)
    total = int(getattr(um, "total_token_count", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": candidates + thoughts,
        "thoughts_tokens": thoughts,
        "total_tokens": total,
    }


def _extract_finish_reason(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    return str(getattr(reason, "name", reason))


class GeminiStream:
    """Các đoạn văn bản của một lần stream, có timeout nhiều tầng và huỷ được (b04 D3–D6).

    Iterator của SDK chặn luồng, nên một thread đọc đẩy sự kiện vào ``queue.Queue``; phía tiêu
    thụ chờ bằng ``queue.get(timeout=...)`` nên không bao giờ bị chặn quá ngưỡng đã cấu hình.
    """

    BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"})

    def __init__(
        self,
        *,
        open_stream: Callable[[], Iterator[Any]],
        model: str,
        first_chunk_timeout: float = 15.0,
        idle_timeout: float = 10.0,
        total_timeout: float = 60.0,
        max_retry: int = 1,
        retry_wait: float = 0.5,
    ) -> None:
        self._open_stream = open_stream
        self.model = model
        self._first_chunk_timeout = float(first_chunk_timeout)
        self._idle_timeout = float(idle_timeout)
        self._total_timeout = float(total_timeout)
        self._max_retry = max(0, int(max_retry))
        self._retry_wait = float(retry_wait)

        self.usage: dict[str, int] = {}
        self.finish_reason: str | None = None
        self.first_chunk_ms: int | None = None
        self.total_ms: int | None = None
        self.cancelled = False

        self._queue: queue.Queue[tuple[str, Any, str]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._sdk_iterator: Any | None = None
        self._cancel = threading.Event()
        self._attempts = 0
        self._started_at: float | None = None
        self._got_text = False
        self._finished = False
        self._last_response: Any | None = None

    # ── Iterator ──

    def __iter__(self) -> GeminiStream:
        return self

    def __next__(self) -> str:
        if self.cancelled or self._finished:
            raise StopIteration
        if self._thread is None:
            self._start()

        while True:
            timeout, stage = self._wait_budget()
            if timeout <= 0:
                self._abort()
                raise GeminiStreamTimeout(
                    "stream vượt tổng thời gian cho phép", stage="total", partial=self._got_text
                )
            try:
                kind, payload, text = self._queue.get(timeout=timeout)
            except queue.Empty:
                if stage != "total" and self._can_retry():
                    self._stop_attempt()
                    time.sleep(self._retry_wait)
                    self._start()
                    continue
                self._abort()
                raise GeminiStreamTimeout(
                    f"stream hết giờ ở giai đoạn {stage}", stage=stage, partial=self._got_text
                ) from None

            if kind == "chunk":
                if payload is not None:
                    self._last_response = payload
                if not text:
                    continue  # đoạn rỗng bị bỏ qua
                if self.first_chunk_ms is None:
                    self.first_chunk_ms = self._ms_since_start()
                self._got_text = True
                return text

            if kind == "end":
                self._complete()
                raise StopIteration

            # kind == "error"
            if self._can_retry():
                self._stop_attempt()
                time.sleep(self._retry_wait)
                self._start()
                continue
            self._abort()
            raise GeminiStreamError(str(payload), partial=self._got_text) from payload

    # ── Huỷ ──

    def close(self) -> None:
        """Dừng stream; gọi nhiều lần vẫn an toàn."""
        if self.cancelled:
            return
        self.cancelled = True
        self._stop_attempt()
        self._finished = True
        if self.total_ms is None and self._started_at is not None:
            self.total_ms = self._ms_since_start()

    def __enter__(self) -> GeminiStream:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── Nội bộ ──

    def _start(self) -> None:
        self._queue = queue.Queue()
        self._cancel = threading.Event()
        self._attempts += 1
        if self._started_at is None:
            self._started_at = time.monotonic()
        thread = threading.Thread(target=self._read, name="gemini-stream-reader", daemon=True)
        self._thread = thread
        thread.start()

    def _read(self) -> None:
        cancel = self._cancel
        target_queue = self._queue
        try:
            iterator = self._open_stream()
            self._sdk_iterator = iterator
            for chunk in iterator:
                if cancel.is_set():
                    return
                target_queue.put(("chunk", chunk, str(getattr(chunk, "text", None) or "")))
        except BaseException as exc:  # lỗi SDK/mạng được chuyển cho phía tiêu thụ
            target_queue.put(("error", exc, ""))
            return
        target_queue.put(("end", None, ""))

    def _wait_budget(self) -> tuple[float, str]:
        stage = "first_chunk" if not self._got_text else "idle"
        stage_timeout = self._first_chunk_timeout if not self._got_text else self._idle_timeout
        if self._started_at is None:
            return stage_timeout, stage
        remaining = self._total_timeout - (time.monotonic() - self._started_at)
        if remaining <= 0:
            return 0.0, "total"
        if remaining < stage_timeout:
            return remaining, "total"
        return stage_timeout, stage

    def _can_retry(self) -> bool:
        """Chỉ retry khi chưa có đoạn văn bản nào (design b04 D5)."""
        return not self._got_text and self._attempts <= self._max_retry

    def _complete(self) -> None:
        self._finished = True
        self.total_ms = self._ms_since_start()
        self.usage = _extract_stream_usage(self._last_response)
        self.finish_reason = _extract_finish_reason(self._last_response)
        if not self.usage:
            logger.warning("usage_missing (model=%s)", self.model)
        if self.finish_reason in self.BLOCKED_FINISH_REASONS and not self._got_text:
            raise GeminiStreamError(
                f"phản hồi bị chặn ({self.finish_reason})", partial=False, code="BLOCKED"
            )

    def _abort(self) -> None:
        """Dừng hẳn lượt stream vì lỗi hoặc hết giờ (khác ``close()`` do phía gọi chủ động)."""
        self._stop_attempt()
        self._finished = True
        if self.total_ms is None:
            self.total_ms = self._ms_since_start()

    def _stop_attempt(self) -> None:
        self._cancel.set()
        iterator = self._sdk_iterator
        close = getattr(iterator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # đóng iterator lỗi không được che lỗi gốc
                logger.debug("gemini_stream_iterator_close_failed")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.1)
            if thread.is_alive():
                logger.warning("gemini_stream_reader_orphaned (model=%s)", self.model)
        self._thread = None
        self._sdk_iterator = None

    def _ms_since_start(self) -> int:
        if self._started_at is None:
            return 0
        return int((time.monotonic() - self._started_at) * 1000)
