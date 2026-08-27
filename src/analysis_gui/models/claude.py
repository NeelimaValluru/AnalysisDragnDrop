"""Anthropic Claude client (Messages API)."""

from typing import Any, Optional

from .base import require_env, require_package
from .errors import ModelError

#: Current balanced default (Claude Sonnet 5, 2026). Overridable per call.
DEFAULT_MODEL = "claude-sonnet-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_MAX_TOKENS = 1024

#: Retired palette / codegen IDs mapped onto current API models.
_MODEL_ALIASES = {
    "claude-3-opus": "claude-opus-5",
    "claude-3-sonnet": "claude-sonnet-5",
    "claude-3-haiku": "claude-haiku-4-5",
    "claude-3-opus-20240229": "claude-opus-5",
    "claude-3-sonnet-20240229": "claude-sonnet-5",
    "claude-3-haiku-20240307": "claude-haiku-4-5",
}


def _import_anthropic():
    """Import the Anthropic SDK, or raise :class:`MissingSDKError`."""
    return require_package("anthropic")


def resolve_model(model: Optional[str]) -> str:
    """Return the API model id, applying known aliases."""
    chosen = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return _MODEL_ALIASES.get(chosen, chosen)


def _text_from_message(message: Any) -> str:
    """Extract concatenated text blocks from a Messages API response."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        return ""

    parts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
            continue
        if isinstance(block, dict):
            if block.get("type", "text") == "text" and block.get("text"):
                parts.append(block["text"])
    return "".join(parts)


class ClaudeClient:
    """Call Claude via the Anthropic Messages API.

    The ``anthropic`` package is imported on first :meth:`complete`, so
    ``import analysis_gui.models`` succeeds even when the SDK is absent.
    The API key is read from ``ANTHROPIC_API_KEY`` at call time unless
    ``api_key`` was passed to the constructor.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.default_model = model or DEFAULT_MODEL

    def complete(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> str:
        """Send ``prompt`` to Claude and return the response text.

        Args:
            prompt: User message content.
            model: Model id; falls back to the client default, then
                :data:`DEFAULT_MODEL`.
            **kwargs: Extra arguments forwarded to ``messages.create``
                (e.g. ``temperature``). ``max_tokens`` defaults to 1024.

        Returns:
            The concatenated text of the reply.

        Raises:
            MissingAPIKeyError: ``ANTHROPIC_API_KEY`` is unset and no key
                was supplied to the constructor.
            MissingSDKError: the ``anthropic`` package is not installed.
            ModelError: the API returned no text content.
        """
        api_key = self.api_key or require_env(API_KEY_ENV, "Claude")
        anthropic = _import_anthropic()
        client = anthropic.Anthropic(api_key=api_key)
        max_tokens = kwargs.pop("max_tokens", DEFAULT_MAX_TOKENS)
        message = client.messages.create(
            model=resolve_model(model or self.default_model),
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        text = _text_from_message(message)
        if not text:
            raise ModelError("Claude returned an empty response")
        return text

    def call(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> str:
        """Alias of :meth:`complete` kept for the previous stub API."""
        return self.complete(prompt, model=model, **kwargs)
