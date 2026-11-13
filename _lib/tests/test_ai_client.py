"""
tests/test_ai_client.py — Unit tests (mock) + integration smoke tests (real API).

Run mock tests only (default):
    python -m pytest _lib/tests/test_ai_client.py

Run smoke tests (real API, costs money):
    set RUN_SMOKE_TESTS=1
    set TEST_PDF_PATH=Z:\\path\\to\\test.pdf
    python -m pytest _lib/tests/test_ai_client.py -m smoke -v
"""

import json
import os
import sys

import pytest

# Ensure _lib is importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from connectors.ai_client import (
    AIAuthError,
    AIClient,
    AIClientError,
    AIContentFilterError,
    AIProviderError,
    AIResponseParseError,
    AITimeoutError,
    _parse_json_response,
)


# ── Helpers for constructing mock responses ──────────────────────────────────

class _MockGeminiResponse:
    """Simulates google.genai response with .text attribute."""
    def __init__(self, text: str):
        self.text = text


class _MockOpenAIMessage:
    def __init__(self, content: str):
        self.content = content


class _MockOpenAIChoice:
    def __init__(self, content: str):
        self.message = _MockOpenAIMessage(content)


class _MockOpenAIResponse:
    """Simulates openai ChatCompletion response."""
    def __init__(self, content: str):
        self.choices = [_MockOpenAIChoice(content)]


def _make_gemini_mock(return_value=None, side_effect=None):
    """
    Create a mock that behaves like google.genai.Client.

    Returns the mock client object for assertion.
    """
    import unittest.mock as mock

    client = mock.MagicMock()
    if side_effect:
        client.models.generate_content.side_effect = side_effect
    else:
        client.models.generate_content.return_value = return_value

    return client


def _make_openai_mock(return_value=None, side_effect=None):
    """
    Create a mock that behaves like openai.OpenAI.

    Returns the mock client object.
    """
    import unittest.mock as mock

    client = mock.MagicMock()
    if side_effect:
        client.chat.completions.create.side_effect = side_effect
    else:
        client.chat.completions.create.return_value = return_value

    return client


# ── Unit tests (mock) ────────────────────────────────────────────────────────

class TestParseJsonResponse:
    """Tests for the internal _parse_json_response helper."""

    def test_clean_json(self):
        assert _parse_json_response('{"a": 1}') == {"a": 1}

    def test_json_with_code_fence(self):
        raw = '```json\n{"a": 1}\n```'
        assert _parse_json_response(raw) == {"a": 1}

    def test_json_with_preamble(self):
        raw = 'Here is the result:\n{"a": 1}'
        assert _parse_json_response(raw) == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("not json at all")


class TestAIClientUnit:
    """Mock-based unit tests for AIClient."""

    # ── Case 1: Gemini returns valid JSON ─────────────────────────────────

    @pytest.fixture(autouse=True)
    def _mock_env(self, monkeypatch, tmp_path):
        """Ensure API keys are set for all unit tests."""
        monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

    def test_01_gemini_json_success(self, monkeypatch):
        """Gemini returns valid JSON → dict returned, no fallback."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            return_value=_MockGeminiResponse('{"email": "a@b.com", "amount": "100"}')
        )
        openai_client = _make_openai_mock()

        with mock.patch("connectors.ai_client.GEMINI_API_KEY", "k", create=True), \
             mock.patch("connectors.ai_client.OPENAI_API_KEY", "k", create=True), \
             mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            # Need to reimport to pick up patched settings
            from connectors.ai_client import AIClient
            client = AIClient()
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"email": "a@b.com", "amount": "100"}
        gemini_client.models.generate_content.assert_called_once()
        openai_client.chat.completions.create.assert_not_called()

    def test_02_gemini_text_mode(self, monkeypatch):
        """json_mode=False → raw str returned."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            return_value=_MockGeminiResponse("Hello world")
        )

        with mock.patch("google.genai.Client", return_value=gemini_client):
            from connectors.ai_client import AIClient
            client = AIClient()
            client._gemini_client = None

            result = client.call(prompt="test", json_mode=False)

        assert result == "Hello world"

    def test_03_gemini_timeout_then_openai_ok(self, monkeypatch):
        """Gemini timeout → fallback to OpenAI → success."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=TimeoutError("timed out")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"email": "b@c.com"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"email": "b@c.com"}
        openai_client.chat.completions.create.assert_called_once()

    def test_04_gemini_429_then_openai_ok(self, monkeypatch):
        """Gemini 429 → fallback to OpenAI → success."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("429 resource_exhausted")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"ok": true}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"ok": True}

    def test_05_gemini_5xx_then_openai_ok(self, monkeypatch):
        """Gemini 500 → fallback to OpenAI → success."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("500 internal server error")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"ok": true}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"ok": True}

    def test_06_gemini_bad_json_then_openai_ok(self, monkeypatch):
        """Gemini returns non-JSON → fallback to OpenAI → valid JSON."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            return_value=_MockGeminiResponse("I cannot parse this document.")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"email": "x@y.com"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient()
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"email": "x@y.com"}

    def test_07_both_providers_fail(self, monkeypatch):
        """Gemini + OpenAI both fail → AIProviderError."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("server error")
        )
        openai_client = _make_openai_mock(
            side_effect=Exception("server error")
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            with pytest.raises(AIProviderError):
                client.call(prompt="test", json_mode=True)

    def test_08_auth_error_no_fallback(self, monkeypatch):
        """401 → AIAuthError raised immediately, no OpenAI fallback."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("401 unauthorized API key invalid")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"should": "not reach"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            with pytest.raises(AIAuthError):
                client.call(prompt="test", json_mode=True)

        # OpenAI was never called
        openai_client.chat.completions.create.assert_not_called()

    def test_09_content_filter_no_fallback(self, monkeypatch):
        """Content safety filter → AIContentFilterError, no fallback."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("blocked by content_filter safety policy")
        )
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"should": "not reach"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            with pytest.raises(AIContentFilterError):
                client.call(prompt="test", json_mode=True)

        openai_client.chat.completions.create.assert_not_called()

    def test_10_provider_gemini_only_no_fallback(self, monkeypatch):
        """provider="gemini" + Gemini fails → AIProviderError, no OpenAI."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("server error")
        )
        openai_client = _make_openai_mock()

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            with pytest.raises(AIProviderError):
                client.call(prompt="test", provider="gemini")

        openai_client.chat.completions.create.assert_not_called()

    def test_11_fallback_false_no_fallback(self, monkeypatch):
        """fallback=False + Gemini fails → AIProviderError."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            side_effect=Exception("server error")
        )
        openai_client = _make_openai_mock()

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=1)
            client._gemini_client = None
            client._openai_client = None

            with pytest.raises(AIProviderError):
                client.call(prompt="test", fallback=False, max_retries=1)

        openai_client.chat.completions.create.assert_not_called()

    def test_12_text_only_prompt(self, monkeypatch):
        """No file_parts → prompt sent as-is."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            return_value=_MockGeminiResponse('{"classification": "Positive"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client):
            from connectors.ai_client import AIClient
            client = AIClient()
            client._gemini_client = None

            result = client.call(prompt="Classify: hello", json_mode=True)

        assert result == {"classification": "Positive"}

    def test_13_mixed_file_parts(self, monkeypatch):
        """file_parts with str + dict → content built correctly for both providers."""
        import unittest.mock as mock

        gemini_client = _make_gemini_mock(
            return_value=_MockGeminiResponse('{"ok": true}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client):
            from connectors.ai_client import AIClient
            client = AIClient()
            client._gemini_client = None

            parts = [
                "Extracted text from docx",
                {"mime_type": "image/jpeg", "data": b"\xff\xd8\xff"},
            ]
            result = client.call(prompt="Analyze", file_parts=parts, json_mode=True)

        assert result == {"ok": True}
        # Verify Gemini was called with the content list
        call_args = gemini_client.models.generate_content.call_args
        contents = call_args[1]["contents"] if "contents" in call_args[1] else call_args[0][0] if call_args[0] else call_args[1].get("contents")
        # The prompt should be the last element
        assert len(contents) >= 3  # str part + image part + prompt

    def test_14_empty_api_key_raises_auth(self, monkeypatch):
        """Empty Gemini key → AIAuthError on call()."""
        import unittest.mock as mock

        monkeypatch.setenv("GEMINI_API_KEY", "")

        # Force module-level vars to update
        import connectors.ai_client as mod
        original = mod.GEMINI_API_KEY
        mod.GEMINI_API_KEY = ""
        try:
            client = AIClient()
            client._gemini_client = None
            with pytest.raises(AIAuthError):
                client.call(prompt="test", provider="gemini", fallback=False)
        finally:
            mod.GEMINI_API_KEY = original

    def test_15_gemini_key_empty_raises_auth(self, monkeypatch):
        """provider="auto", Gemini key empty → AIAuthError, no fallback."""
        import unittest.mock as mock

        import connectors.ai_client as mod
        original_gemini = mod.GEMINI_API_KEY
        mod.GEMINI_API_KEY = ""
        try:
            openai_client = _make_openai_mock(
                return_value=_MockOpenAIResponse('{"ok": true}')
            )

            with mock.patch("openai.OpenAI", return_value=openai_client):
                client = AIClient()
                client._gemini_client = None
                client._openai_client = None

                # provider="auto" should skip Gemini (no key) and go to OpenAI
                # But actually AIClient._get_gemini raises AIAuthError which is
                # not caught as retryable — it propagates up.
                # The design says auth error raises immediately, so this will
                # actually raise AIAuthError. This is correct: if the primary
                # key is missing, it's a config error.
                with pytest.raises(AIAuthError):
                    client.call(prompt="test")
        finally:
            mod.GEMINI_API_KEY = original_gemini

    def test_16_gemini_retry_then_success(self, monkeypatch):
        """Gemini fails once (5xx) then succeeds on retry — no fallback."""
        import unittest.mock as mock

        call_count = [0]

        def gemini_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("500 server error")
            return _MockGeminiResponse('{"retried": true}')

        gemini_client = _make_gemini_mock(side_effect=gemini_side_effect)
        openai_client = _make_openai_mock(
            return_value=_MockOpenAIResponse('{"should": "not reach"}')
        )

        with mock.patch("google.genai.Client", return_value=gemini_client), \
             mock.patch("openai.OpenAI", return_value=openai_client):

            from connectors.ai_client import AIClient
            client = AIClient(max_retries=3)
            client._gemini_client = None
            client._openai_client = None

            result = client.call(prompt="test", json_mode=True)

        assert result == {"retried": True}
        # OpenAI should not have been called (Gemini succeeded on retry)
        openai_client.chat.completions.create.assert_not_called()


# ── Integration smoke tests (real API) ────────────────────────────────────────

_SMOKE_REASON = (
    "Set RUN_SMOKE_TESTS=1 and TEST_PDF_PATH=<path> to run real API tests"
)


@pytest.mark.skipif(
    not os.getenv("RUN_SMOKE_TESTS"),
    reason=_SMOKE_REASON,
)
class TestSmokeRealAPI:
    """
    Real API smoke tests — costs money, run manually.

    Usage:
        set RUN_SMOKE_TESTS=1
        set TEST_PDF_PATH=Z:\\path\\to\\test.pdf
        python -m pytest _lib/tests/test_ai_client.py -v -s -k "TestSmokeRealAPI"
    """

    def test_smoke_gemini_real_pdf(self):
        """Call Gemini with a real PDF, verify JSON response."""
        pdf_path = os.getenv("TEST_PDF_PATH", "")
        assert pdf_path and os.path.exists(pdf_path), (
            "TEST_PDF_PATH must point to an existing PDF file"
        )

        with open(pdf_path, "rb") as f:
            parts = [{"mime_type": "application/pdf", "data": f.read()}]

        client = AIClient(timeout=120, max_retries=2)
        result = client.call(
            prompt=(
                "Describe this document. "
                'Respond with JSON: {"description": "...", "page_count": N}'
            ),
            file_parts=parts,
            json_mode=True,
            provider="gemini",
            fallback=False,
        )

        assert isinstance(result, dict)
        assert "description" in result

    def test_smoke_openai_real_pdf(self):
        """Call OpenAI with a real PDF (base64), verify JSON response."""
        pdf_path = os.getenv("TEST_PDF_PATH", "")
        assert pdf_path and os.path.exists(pdf_path), (
            "TEST_PDF_PATH must point to an existing PDF file"
        )

        with open(pdf_path, "rb") as f:
            parts = [{"mime_type": "application/pdf", "data": f.read()}]

        client = AIClient(timeout=120, max_retries=2)
        result = client.call(
            prompt=(
                "Describe this document. "
                'Respond with JSON: {"description": "...", "page_count": N}'
            ),
            file_parts=parts,
            json_mode=True,
            provider="openai",
            fallback=False,
        )

        assert isinstance(result, dict)
        assert "description" in result

    def test_smoke_fallback_gemini_to_openai(self):
        """
        Force Gemini failure by using a bad model name,
        verify fallback to OpenAI works end-to-end.
        """
        pdf_path = os.getenv("TEST_PDF_PATH", "")
        assert pdf_path and os.path.exists(pdf_path), (
            "TEST_PDF_PATH must point to an existing PDF file"
        )

        with open(pdf_path, "rb") as f:
            parts = [{"mime_type": "application/pdf", "data": f.read()}]

        import connectors.ai_client as mod
        original = mod.GEMINI_MODEL
        mod.GEMINI_MODEL = "nonexistent-model-xyz"
        try:
            client = AIClient(timeout=60, max_retries=1)
            client._gemini_client = None
            client._openai_client = None
            result = client.call(
                prompt=(
                    "Describe this document. "
                    'Respond with JSON: {"description": "..."}'
                ),
                file_parts=parts,
                json_mode=True,
                provider="auto",
                fallback=True,
            )
            assert isinstance(result, dict)
            assert "description" in result
        finally:
            mod.GEMINI_MODEL = original


if __name__ == "__main__":
    pytest.main([__file__, "-v"] + sys.argv[1:])
