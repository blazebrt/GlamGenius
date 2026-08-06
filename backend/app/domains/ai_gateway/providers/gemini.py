"""Gemini transport.

Moved verbatim in behaviour from the old ``ai.py`` — same model fallback chain,
same JSON response mode, same "never expose the key" rule. Three things are new:

* a hard timeout, so a hanging provider fails instead of holding a request open
* token counts returned for cost estimation
* a typed result object instead of a bare string

This module talks to Google and nothing else. It does no recording, no
validation and no error translation — that is the gateway's job.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Any

from app.config import AI_TIMEOUT_SECONDS, GEMINI_API_KEY, GEMINI_FALLBACK_MODELS, GEMINI_MODEL

logger = logging.getLogger(__name__)

try:
    from google import genai as google_genai
    from google.genai import types as google_genai_types

    HAS_GOOGLE_GENAI = True
except ImportError:  # pragma: no cover - exercised only where the package is absent
    google_genai = None  # type: ignore[assignment]
    google_genai_types = None  # type: ignore[assignment]
    HAS_GOOGLE_GENAI = False

PROVIDER_NAME = "gemini"

_client: Any = None


class ProviderNotConfigured(RuntimeError):
    """No API key, or the client library is not installed."""


class ProviderTimeout(RuntimeError):
    """The provider did not answer within AI_TIMEOUT_SECONDS."""


class ProviderCallFailed(RuntimeError):
    """The provider answered with an error, or with nothing usable."""


class ImageRejected(ValueError):
    """The supplied image could not be decoded before sending."""


@dataclass
class ProviderResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def is_configured() -> bool:
    return bool(GEMINI_API_KEY and HAS_GOOGLE_GENAI)


def get_client() -> Any:
    """Lazy client. The key stays in process memory, sourced from env only."""
    global _client
    if not is_configured():
        return None
    if _client is None:
        _client = google_genai.Client(api_key=GEMINI_API_KEY)
    return _client


def reset_client() -> None:
    """Drop the cached client. Tests use this after changing configuration."""
    global _client
    _client = None


def _decode_image(image_base64: str) -> tuple[bytes, str]:
    raw = image_base64
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageRejected("Image is not valid base64") from exc
    if not image_bytes:
        raise ImageRejected("Image is empty")

    mime = "image/jpeg"
    if image_bytes.startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes.startswith(b"RIFF"):
        mime = "image/webp"
    return image_bytes, mime


def _models_to_try() -> list[str]:
    ordered: list[str] = []
    for name in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        try:
            text = response.candidates[0].content.parts[0].text or ""
        except (AttributeError, IndexError, TypeError):
            text = ""
    return text


def _extract_tokens(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None, None
    return (
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "candidates_token_count", None),
    )


async def generate(
    prompt: str, system: str, image_base64: str | None = None
) -> ProviderResponse:
    """One call to Gemini, with the configured model fallback chain."""
    client = get_client()
    if client is None:
        raise ProviderNotConfigured(
            "GEMINI_API_KEY is not set, or google-genai is not installed"
        )

    parts: list[Any] = []
    if image_base64:
        image_bytes, mime = _decode_image(image_base64)
        parts.append(
            google_genai_types.Part.from_bytes(data=image_bytes, mime_type=mime)
        )
    parts.append(prompt)

    last_error: Exception | None = None
    for model_name in _models_to_try():

        def _run(model: str = model_name) -> Any:
            return client.models.generate_content(
                model=model,
                contents=parts,
                config=google_genai_types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=AI_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            # A timeout is not a "try the next model" situation — the next one
            # would take just as long and the caller is already waiting.
            raise ProviderTimeout(
                f"Gemini did not respond within {AI_TIMEOUT_SECONDS:.0f}s"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - provider errors are untyped
            last_error = exc
            message = str(exc)
            # Quota exhausted or model retired: the next model in the chain may
            # well work. Anything else is a real failure — stop immediately.
            if any(
                token in message
                for token in ("429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND")
            ):
                logger.warning(
                    "gemini_model_unavailable model=%s type=%s",
                    model_name,
                    type(exc).__name__,
                )
                continue
            raise ProviderCallFailed(
                f"{type(exc).__name__} from Gemini"
            ) from exc

        text = _extract_text(response)
        if text:
            input_tokens, output_tokens = _extract_tokens(response)
            return ProviderResponse(
                text=text,
                model=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    if last_error is not None:
        raise ProviderCallFailed(
            f"All Gemini models failed; last error {type(last_error).__name__}"
        ) from last_error
    raise ProviderCallFailed("Gemini returned an empty response")
