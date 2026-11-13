"""
connectors/ai_client.py — Unified AI client with Gemini (primary) + OpenAI (fallback).

Credentials: read from _lib/config/settings.py (.env file).
Logging:     each call() writes a JSON record via _lib/utils/logger.py.
"""

import base64
import json
import os
import re
import time

from config.settings import (
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from utils.logger import Logger


ANTHROPIC_MODEL = "claude-sonnet-4-6"


# ── Log path ─────────────────────────────────────────────────────────────────

_AI_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs")
_AI_LOG_PATH = os.path.join(_AI_LOG_DIR, "ai_client.json")


# ── Exceptions ───────────────────────────────────────────────────────────────

class AIClientError(Exception):
    """Base exception for all ai_client errors."""


class AIAuthError(AIClientError):
    """401/403 or invalid API key — config error, no retry, no fallback."""


class AIContentFilterError(AIClientError):
    """Content safety filter blocked the request — no retry, no fallback."""


class AITimeoutError(AIClientError):
    """All providers timed out."""


class AIProviderError(AIClientError):
    """All providers failed after retries."""


class AIResponseParseError(AIProviderError):
    """json_mode=True and all providers returned unparseable content."""


# ── Error classification ─────────────────────────────────────────────────────

_AUTH_KEYWORDS = ("401", "403", "unauthenticated", "unauthorized",
                  "api_key", "invalid api", "incorrect api")

_FILTER_KEYWORDS = ("safety", "blocked", "content_filter",
                    "content policy", "harmful")

_QUOTA_KEYWORDS = ("429", "resource_exhausted", "quota",
                   "rate limit", "rate_limit", "too many")

_TRANSIENT_KEYWORDS = (
    "502", "503", "504",
    "bad gateway", "service unavailable", "gateway timeout",
    "temporarily unavailable", "temporarily",
)


def _classify_error(exc: Exception) -> str:
    """
    Classify an SDK exception into a category.

    Returns one of: "auth", "content_filter", "quota", "timeout", "transient".
    """
    msg = str(exc).lower()

    # Auth errors — check first because some 403 messages overlap with others
    if any(k in msg for k in _AUTH_KEYWORDS):
        return "auth"

    if any(k in msg for k in _FILTER_KEYWORDS):
        return "content_filter"

    if any(k in msg for k in _QUOTA_KEYWORDS):
        return "quota"

    # Transient server errors: 502/503/504, unavailable messages
    # Check BEFORE generic "timeout" to avoid "Gateway Timeout" -> timeout misclassification
    if any(k in msg for k in _TRANSIENT_KEYWORDS):
        return "transient"

    # Timeout: SDK raises TimeoutError or includes "timeout" in message
    if isinstance(exc, TimeoutError) or "timeout" in msg:
        return "timeout"

    return "transient"


# ── JSON response parsing ────────────────────────────────────────────────────

# Markdown code fence at start of string
_FENCE_OPEN = re.compile(r"^```[a-z]*\s*")
# Markdown code fence at end of string
_FENCE_CLOSE = re.compile(r"\s*```$")
# First JSON object in response (model may add preamble text)
_FIRST_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_response(text: str) -> dict:
    """
    Parse AI response text into a dict.

    Strips markdown code fences, then json.loads().
    If that fails, tries to extract the first JSON object via regex.
    """
    cleaned = text.strip()
    cleaned = _FENCE_OPEN.sub("", cleaned)
    cleaned = _FENCE_CLOSE.sub("", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block from the full text
    m = _FIRST_JSON.search(text)
    if m:
        return json.loads(m.group(0))

    raise json.JSONDecodeError("No JSON object found", text, 0)


# ── AIClient ─────────────────────────────────────────────────────────────────

class AIClient:
    """
    Unified AI client: Gemini (primary) with automatic OpenAI fallback.

    Usage:
        client = AIClient()
        result = client.call(
            prompt="Extract fields...",
            file_parts=[{"mime_type": "application/pdf", "data": pdf_bytes}],
            json_mode=True,
        )
    """

    def __init__(self, timeout: int = 60, max_retries: int = 3):
        """
        Args:
            timeout:     Per-request timeout in seconds (SDK-level).
            max_retries: Max retries per provider on transient/quota errors.
        """
        self._timeout = timeout
        self._max_retries = max_retries
        self._gemini_client = None
        self._openai_client = None
        self._anthropic_client = None
        self._logger = Logger(
            _AI_LOG_PATH,
            default={"calls": []},
        )

    # ── Lazy SDK init ──────────────────────────────────────────────────────

    def _get_gemini(self, timeout_ms: int):
        """
        Create or return cached Gemini client.

        Args:
            timeout_ms: Timeout in milliseconds for http_options.

        Raises:
            AIAuthError: if GEMINI_API_KEY is empty.
        """
        if self._gemini_client is None:
            if not GEMINI_API_KEY:
                raise AIAuthError("GEMINI_API_KEY is not set in .env")
            from google import genai
            self._gemini_client = genai.Client(
                api_key=GEMINI_API_KEY,
                http_options={"timeout": timeout_ms},
            )
        return self._gemini_client

    def _get_openai(self):
        """
        Create or return cached OpenAI client.

        Raises:
            AIAuthError: if OPENAI_API_KEY is empty.
        """
        if self._openai_client is None:
            if not OPENAI_API_KEY:
                raise AIAuthError("OPENAI_API_KEY is not set in .env")
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
        return self._openai_client

    def _get_anthropic(self, timeout: int):
        """Create or return a cached Anthropic client."""
        if self._anthropic_client is None:
            if not ANTHROPIC_API_KEY:
                raise AIAuthError("ANTHROPIC_API_KEY is not set in .env")
            from anthropic import Anthropic

            self._anthropic_client = Anthropic(
                api_key=ANTHROPIC_API_KEY,
                timeout=timeout,
            )
        return self._anthropic_client

    # ── Provider calls ─────────────────────────────────────────────────────

    @staticmethod
    def _build_gemini_content(prompt: str, file_parts: list | None):
        """
        Build content list for Gemini: file parts + prompt.

        Returns:
            list of str and/or types.Part objects.
        """
        from google.genai import types

        content = []
        for p in (file_parts or []):
            if isinstance(p, str):
                content.append(p)
            elif isinstance(p, dict):
                content.append(
                    types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"])
                )
        content.append(prompt)
        return content

    @staticmethod
    def _build_openai_content(prompt: str, file_parts: list | None):
        """
        Build message content for OpenAI Chat Completions.

        PDF parts use type "file" (Chat Completions PDF support);
        image parts use type "image_url".

        Returns:
            list of content dicts (text + file/image_url entries).
        """
        msg_content = []
        for p in (file_parts or []):
            if isinstance(p, str):
                msg_content.append({"type": "text", "text": p})
            elif isinstance(p, dict):
                b64 = base64.b64encode(p["data"]).decode()
                mime = p["mime_type"]
                if mime == "application/pdf":
                    msg_content.append({
                        "type": "file",
                        "file": {
                            "filename": "document.pdf",
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                    })
                else:
                    msg_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
        msg_content.append({"type": "text", "text": prompt})
        return msg_content

    def _call_gemini(self, prompt: str, file_parts: list | None, timeout: int) -> str:
        """
        Call Gemini API and return raw response text.  # [阻塞I/O]

        Args:
            prompt:      Text prompt.
            file_parts:  Optional list of str / {"mime_type": ..., "data": bytes}.
            timeout:     Per-request timeout in seconds.

        Returns:
            Raw response text from Gemini.

        Raises:
            Exception: any SDK error (classified by caller).
        """
        client = self._get_gemini(timeout * 1000)
        content = self._build_gemini_content(prompt, file_parts)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=content,
        )
        return response.text

    def _call_openai(self, prompt: str, file_parts: list | None, timeout: int) -> str:
        """
        Call OpenAI Chat Completions API and return raw response text.  # [阻塞I/O]

        Args:
            prompt:      Text prompt.
            file_parts:  Optional list of str / {"mime_type": ..., "data": bytes}.
            timeout:     Per-request timeout in seconds.

        Returns:
            Raw response text from OpenAI.

        Raises:
            Exception: any SDK error (classified by caller).
        """
        client = self._get_openai()
        msg_content = self._build_openai_content(prompt, file_parts)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": msg_content}],
            timeout=timeout,
        )
        return response.choices[0].message.content

    def _call_anthropic(self, prompt: str, file_parts: list | None, timeout: int) -> str:
        """Call Claude with PDF document or image content blocks."""
        content = []
        for part in (file_parts or []):
            if isinstance(part, str):
                content.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue

            mime_type = part["mime_type"]
            source = {
                "type": "base64",
                "media_type": mime_type,
                "data": base64.b64encode(part["data"]).decode("ascii"),
            }
            if mime_type == "application/pdf":
                content.append({"type": "document", "source": source})
            elif mime_type.startswith("image/"):
                content.append({"type": "image", "source": source})
            else:
                raise ValueError(f"Unsupported Anthropic file type: {mime_type}")

        content.append({"type": "text", "text": prompt})
        response = self._get_anthropic(timeout).messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text

    # ── Retry + fallback orchestration ─────────────────────────────────────

    def _call_provider(self, provider: str, prompt: str, file_parts: list | None,
                       timeout: int, max_retries: int, json_mode: bool) -> str:
        """
        Call a single provider with retry logic.

        Args:
            provider:     "gemini" or "openai".
            prompt:       Text prompt.
            file_parts:   Optional file parts.
            timeout:      Per-request timeout in seconds.
            max_retries:  Max retry count for this provider.
            json_mode:    Whether JSON parsing is expected.

        Returns:
            Raw response text string.

        Raises:
            AIAuthError:           on auth errors (no retry).
            AIContentFilterError:  on content filter (no retry).
            Exception:             last exception if all retries exhausted.
        """
        call_fns = {
            "gemini": self._call_gemini,
            "openai": self._call_openai,
            "anthropic": self._call_anthropic,
        }
        call_fn = call_fns[provider]
        last_exc = None

        for attempt in range(max_retries):
            try:
                return call_fn(prompt, file_parts, timeout)

            except Exception as exc:
                last_exc = exc
                category = _classify_error(exc)

                # Auth / content filter — raise immediately, no retry
                if category == "auth":
                    raise AIAuthError(str(exc)) from exc
                if category == "content_filter":
                    raise AIContentFilterError(str(exc)) from exc

                # Timeout — retry at most once (avoid long blocking)
                if category == "timeout":
                    if attempt >= 1:
                        raise
                    time.sleep(2)

                # Quota — linear backoff: 30, 60, 60, ...
                elif category == "quota":
                    wait = min(30 * (attempt + 1), 60)
                    time.sleep(wait)

                # Transient — exponential backoff capped at 30s
                else:
                    wait = min((2 ** attempt) * 2, 30)
                    time.sleep(wait)

        raise last_exc

    # ── Logging ────────────────────────────────────────────────────────────

    def _log(self, record: dict) -> None:
        """
        Append an AI call record to the JSON log.  # [阻塞I/O]

        Non-critical: silently ignores write failures.
        """
        try:
            data = self._logger.load()
            calls = data.setdefault("calls", [])
            calls.append(record)
            self._logger.save()
        except Exception:
            pass

    # ── Public interface ───────────────────────────────────────────────────

    def call(
        self,
        prompt: str,
        file_parts: list[str | dict] | None = None,
        *,
        json_mode: bool = True,
        provider: str = "auto",
        fallback: bool = True,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> str | dict:
        """
        Send a prompt to AI and return the response.

        Args:
            prompt:      Full text prompt (caller handles template formatting).
            file_parts:  Optional list of str or {"mime_type": str, "data": bytes}.
                         Compatible with existing prepare_parts() output.
            json_mode:   If True (default), parse response as JSON and return dict.
                         If False, return raw text string.
            provider:    "auto" (Gemini → OpenAI), "gemini", or "openai".
            fallback:    Whether to try the other provider on failure.
                         Ignored when provider is explicitly "gemini" or "openai".
            timeout:     Override instance-level per-request timeout (seconds).
            max_retries: Override instance-level retry count per provider.

        Returns:
            dict if json_mode=True, str if json_mode=False.

        Raises:
            AIAuthError:           API key missing or invalid (401/403).
            AIContentFilterError:  Request blocked by content safety filter.
            AITimeoutError:        All providers timed out.
            AIProviderError:       All providers failed.
            AIResponseParseError:  json_mode=True and all providers returned
                                   unparseable content.
        """
        t0 = time.time()
        eff_timeout = timeout if timeout is not None else self._timeout
        eff_retries = max_retries if max_retries is not None else self._max_retries

        # Determine provider order
        if provider == "openai":
            primary, secondary = "openai", None
        elif provider == "anthropic":
            primary, secondary = "anthropic", None
        elif provider == "gemini":
            primary, secondary = "gemini", None
        else:
            # "auto" — Gemini first, OpenAI as fallback
            primary, secondary = "gemini", "openai"
            if not fallback:
                secondary = None

        fallback_used = False
        fallback_from = None
        fallback_reason = None
        final_exc = None
        result_provider = primary

        # ── Try primary ────────────────────────────────────────────────────
        try:
            raw = self._call_provider(
                primary, prompt, file_parts, eff_timeout, eff_retries, json_mode,
            )

            if json_mode:
                return self._finalize_json(
                    raw, result_provider, t0, fallback_used,
                    fallback_from, fallback_reason, json_mode, file_parts,
                )
            return self._finalize_text(
                raw, result_provider, t0, fallback_used,
                fallback_from, fallback_reason, json_mode, file_parts,
            )

        except (AIAuthError, AIContentFilterError):
            raise

        except json.JSONDecodeError:
            # JSON parse failure on primary — try secondary if available
            if secondary:
                fallback_used = True
                fallback_from = primary
                fallback_reason = "json_parse"
            else:
                final_exc = AIResponseParseError(
                    f"{primary} returned unparseable JSON"
                )

        except Exception as exc:
            # Provider exhausted retries — try secondary if available
            if secondary:
                fallback_used = True
                fallback_from = primary
                fallback_reason = _classify_error(exc)
            else:
                final_exc = exc

        # ── Try secondary (fallback) ───────────────────────────────────────
        if fallback_used and secondary:
            result_provider = secondary
            try:
                raw = self._call_provider(
                    secondary, prompt, file_parts, eff_timeout, eff_retries, json_mode,
                )

                if json_mode:
                    return self._finalize_json(
                        raw, result_provider, t0, fallback_used,
                        fallback_from, fallback_reason, json_mode, file_parts,
                    )
                return self._finalize_text(
                    raw, result_provider, t0, fallback_used,
                    fallback_from, fallback_reason, json_mode, file_parts,
                )

            except json.JSONDecodeError:
                final_exc = AIResponseParseError(
                    f"Both providers returned unparseable JSON"
                )

            except (AIAuthError, AIContentFilterError) as exc:
                # Secondary also has config/content issues — raise directly
                self._log_call(
                    result_provider, t0, "error", fallback_used,
                    fallback_from, fallback_reason, json_mode, file_parts,
                    error_type=type(exc).__name__,
                )
                raise

            except Exception as exc:
                final_exc = exc

        # ── All attempts failed ────────────────────────────────────────────
        error_type = type(final_exc).__name__ if final_exc else "Unknown"

        # Use _classify_error to determine final exception type
        # This ensures 503/504/transient errors are not misclassified as timeout
        if final_exc and _classify_error(final_exc) == "timeout":
            self._log_call(
                result_provider, t0, "error", fallback_used,
                fallback_from, fallback_reason, json_mode, file_parts,
                error_type="AITimeoutError",
            )
            raise AITimeoutError(str(final_exc)) from final_exc

        self._log_call(
            result_provider, t0, "error", fallback_used,
            fallback_from, fallback_reason, json_mode, file_parts,
            error_type=error_type,
        )
        raise AIProviderError(str(final_exc)) from final_exc

    # ── Finalize helpers ───────────────────────────────────────────────────

    def _finalize_json(self, raw, provider, t0, fallback_used,
                       fallback_from, fallback_reason, json_mode, file_parts):
        """Parse raw text as JSON, log, and return dict."""
        parsed = _parse_json_response(raw)
        self._log_call(
            provider, t0, "ok", fallback_used,
            fallback_from, fallback_reason, json_mode, file_parts,
        )
        return parsed

    def _finalize_text(self, raw, provider, t0, fallback_used,
                       fallback_from, fallback_reason, json_mode, file_parts):
        """Log and return raw text string."""
        self._log_call(
            provider, t0, "ok", fallback_used,
            fallback_from, fallback_reason, json_mode, file_parts,
        )
        return raw

    def _log_call(self, provider, t0, status, fallback_used,
                  fallback_from, fallback_reason, json_mode, file_parts,
                  error_type=None):
        """
        Build and write a log record for this AI call.
        """
        model = {
            "gemini": GEMINI_MODEL,
            "openai": OPENAI_MODEL,
            "anthropic": ANTHROPIC_MODEL,
        }.get(provider, "unknown")
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "ai_call",
            "provider": provider,
            "model": model,
            "elapsed_sec": round(time.time() - t0, 2),
            "status": status,
            "fallback_used": fallback_used,
            "json_mode": json_mode,
            "has_files": bool(file_parts),
            "file_count": len(file_parts) if file_parts else 0,
            "error_type": error_type,
        }
        if fallback_used:
            record["fallback_from"] = fallback_from
            record["fallback_reason"] = fallback_reason

        self._log(record)
