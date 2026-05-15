"""
Gemini LLM client — supports both Vertex AI and API Key modes.

Mode is controlled by env var GEMINI_BACKEND:
  - "vertex"  → google-genai unified SDK + GCP Service Account (default)
  - "apikey"  → google-generativeai SDK + API key

Usage:
    from gemini_client import generate, generate_json

    text = generate("your prompt here")
    text = generate_json("your prompt here")  # forces JSON output
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_BACKEND,
    GCP_PROJECT_ID,
    GCP_LOCATION,
    GCP_SERVICE_ACCOUNT_JSON,
    logger,
)


# ── Lazy-init clients ───────────────────────────────────────────────────────
_vertex_client = None
_apikey_model = None


def _init_vertex():
    """Initialize Vertex AI client with Service Account credentials."""
    global _vertex_client
    if _vertex_client is not None:
        return

    # Set env vars BEFORE importing google.genai
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
    if GCP_PROJECT_ID:
        os.environ["GOOGLE_CLOUD_PROJECT"] = GCP_PROJECT_ID
    if GCP_LOCATION:
        os.environ["GOOGLE_CLOUD_LOCATION"] = GCP_LOCATION

    # Service Account auth for headless environments (Docker, VM)
    if GCP_SERVICE_ACCOUNT_JSON and os.path.isfile(GCP_SERVICE_ACCOUNT_JSON):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GCP_SERVICE_ACCOUNT_JSON
        logger.info("Vertex AI auth: Service Account → %s", GCP_SERVICE_ACCOUNT_JSON)
    else:
        # Falls back to Application Default Credentials (gcloud auth)
        logger.info("Vertex AI auth: Application Default Credentials")

    from google import genai
    _vertex_client = genai.Client()
    logger.info("Vertex AI client ready (project=%s, location=%s, model=%s)",
                GCP_PROJECT_ID, GCP_LOCATION, GEMINI_MODEL)


def _init_apikey():
    """Initialize API Key client (google-generativeai SDK)."""
    global _apikey_model
    if _apikey_model is not None:
        return

    assert GEMINI_API_KEY, "GEMINI_API_KEY is required when GEMINI_BACKEND=apikey"
    import google.generativeai as genai_legacy
    genai_legacy.configure(api_key=GEMINI_API_KEY)
    _apikey_model = genai_legacy.GenerativeModel(GEMINI_MODEL)
    logger.info("Gemini API Key client ready (model=%s)", GEMINI_MODEL)


# ── Public API ───────────────────────────────────────────────────────────────
def generate(prompt: str, temperature: float = None) -> str:
    """
    Generate text from a prompt.

    Args:
        prompt: The prompt text.
        temperature: Optional temperature override.

    Returns:
        Generated text string.
    """
    if GEMINI_BACKEND == "vertex":
        return _generate_vertex(prompt, temperature=temperature)
    else:
        return _generate_apikey(prompt, temperature=temperature)


def generate_json(prompt: str, temperature: float = 0.0) -> str:
    """
    Generate JSON output from a prompt.

    Args:
        prompt: The prompt text.
        temperature: Temperature (default 0.0 for deterministic).

    Returns:
        Raw JSON string from LLM.
    """
    if GEMINI_BACKEND == "vertex":
        return _generate_vertex(prompt, response_mime_type="application/json", temperature=temperature)
    else:
        return _generate_apikey_json(prompt, temperature=temperature)


# ── Vertex AI implementation ────────────────────────────────────────────────
def _generate_vertex(prompt: str, response_mime_type: str = None, temperature: float = None) -> str:
    """Call Vertex AI via unified google-genai SDK."""
    _init_vertex()
    from google.genai import types

    cfg_kwargs = {}
    if response_mime_type is not None:
        cfg_kwargs["response_mime_type"] = response_mime_type
    if temperature is not None:
        cfg_kwargs["temperature"] = temperature

    kwargs = {
        "model": GEMINI_MODEL,
        "contents": prompt,
    }
    if cfg_kwargs:
        kwargs["config"] = types.GenerateContentConfig(**cfg_kwargs)

    resp = _vertex_client.models.generate_content(**kwargs)
    return (getattr(resp, "text", None) or "").strip()


# ── API Key implementation ──────────────────────────────────────────────────
def _generate_apikey(prompt: str, temperature: float = None) -> str:
    """Call Gemini via API key (google-generativeai SDK)."""
    _init_apikey()
    gen_config = {}
    if temperature is not None:
        gen_config["temperature"] = temperature

    resp = _apikey_model.generate_content(prompt, generation_config=gen_config or None)
    return (getattr(resp, "text", None) or "").strip()


def _generate_apikey_json(prompt: str, temperature: float = 0.0) -> str:
    """Call Gemini with JSON mode via API key."""
    _init_apikey()
    try:
        resp = _apikey_model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": temperature,
            },
        )
        return (getattr(resp, "text", None) or "").strip()
    except Exception:
        # Fallback: some models don't support response_mime_type
        resp = _apikey_model.generate_content(
            prompt,
            generation_config={"temperature": temperature},
        )
        return (getattr(resp, "text", None) or "").strip()
