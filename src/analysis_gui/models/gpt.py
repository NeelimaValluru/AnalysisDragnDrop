"""OpenAI GPT client (Chat Completions API)."""

from typing import Any, Optional

from .base import require_env, require_package
from .errors import ModelError

#: Widely available default (GPT-4.1, 2026). Overridable per call.
DEFAULT_MODEL = "gpt-4.1"
API_KEY_ENV = "OPENAI_API_KEY"

#: Older palette IDs that still work as aliases on the OpenAI API, mapped
#: onto a current chat model when the original id has been retired.
_MODEL_ALIASES = {
    "gpt-3.5-turbo": "gpt-4.1-mini",
}


def _import_openai():
    """Import the OpenAI SDK, or raise :class:`MissingSDKError`."""
    return require_package("openai")


def resolve_model(model: Optional[str]) -> str:
    """Return the API model id, applying known aliases."""
    chosen = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return _MODEL_ALIASES.get(chosen, chosen)


def _text_from_response(response: Any) -> str:
    """Extract the first choice's message content from Chat Completions."""
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")
    if message is None:
        return ""

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content or ""


class GPTClient:
    """Call GPT via OpenAI Chat Completions.

    The ``openai`` package is imported on first :meth:`complete`, so
    ``import analysis_gui.models`` succeeds even when the SDK is absent.
    The API key is read from ``OPENAI_API_KEY`` at call time unless
    ``api_key`` was passed to the constructor.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.default_model = model or DEFAULT_MODEL

    def complete(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> str:
        """Send ``prompt`` to GPT and return the response text.

        Args:
            prompt: User message content.
            model: Model id; falls back to the client default, then
                :data:`DEFAULT_MODEL`.
            **kwargs: Extra arguments forwarded to
                ``chat.completions.create`` (e.g. ``temperature``).

        Returns:
            The first choice's message content.

        Raises:
            MissingAPIKeyError: ``OPENAI_API_KEY`` is unset and no key
                was supplied to the constructor.
            MissingSDKError: the ``openai`` package is not installed.
            ModelError: the API returned no text content.
        """
        api_key = self.api_key or require_env(API_KEY_ENV, "GPT")
        openai = _import_openai()
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=resolve_model(model or self.default_model),
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        text = _text_from_response(response)
        if not text:
            raise ModelError("GPT returned an empty response")
        return text

    def call(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> str:
        """Alias of :meth:`complete` kept for the previous stub API."""
        return self.complete(prompt, model=model, **kwargs)
