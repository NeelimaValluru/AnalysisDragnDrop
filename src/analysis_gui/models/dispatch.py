"""Dispatch a model-call node (or a provider name) to the right client."""

from enum import Enum
from typing import Any, Optional, Union

from .claude import ClaudeClient
from .errors import ModelError, UnknownProviderError
from .gpt import GPTClient

#: Cap how much upstream data is stuffed into a prompt. A million-row table
#: must not go to the API; a short preview is enough for the model to see
#: the shape and a sample of values.
DEFAULT_CONTEXT_CHARS = 8000
DEFAULT_CONTEXT_ROWS = 20


def preview_for_prompt(
    value: Any,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    head_rows: int = DEFAULT_CONTEXT_ROWS,
) -> str:
    """Render ``value`` as a short string suitable for a model prompt.

    DataFrames (anything with ``head`` and ``to_string``) contribute a
    ``head().to_string()`` sample plus a row count. Everything else is
    ``str()``-ed. The result is truncated to ``max_chars``.
    """
    if value is None:
        return ""

    head = getattr(value, "head", None)
    if callable(head):
        preview = head(head_rows)
        to_string = getattr(preview, "to_string", None)
        text = to_string() if callable(to_string) else str(preview)
        try:
            n_rows = len(value)
        except TypeError:
            n_rows = None
        if n_rows is not None and n_rows > head_rows:
            text = f"{text}\n... ({n_rows} rows total)"
    else:
        text = str(value)

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... [truncated]"
    return text


def _provider_value(provider: Any) -> str:
    """Normalize a provider enum or string to a lowercase id."""
    if isinstance(provider, Enum):
        provider = provider.value
    if not isinstance(provider, str) or not provider.strip():
        raise UnknownProviderError(
            f"Unknown model provider: {provider!r}. Expected 'claude' or 'gpt'."
        )
    return provider.strip().lower()


def client_for(provider: Any) -> Union[ClaudeClient, GPTClient]:
    """Return a client for ``provider`` (``'claude'`` or ``'gpt'``)."""
    value = _provider_value(provider)
    if value == "claude":
        return ClaudeClient()
    if value == "gpt":
        return GPTClient()
    if value in {"open_weights", "ollama"}:
        raise ModelError(
            "Open-weights / Ollama calls are not implemented yet. "
            "Use a Claude or GPT node, or run a local model from a custom-code node."
        )
    raise UnknownProviderError(
        f"Unknown model provider: {value!r}. Expected 'claude' or 'gpt'."
    )


def complete(
    provider: Any,
    prompt: str,
    model: Optional[str] = None,
    context: Any = None,
    **kwargs: Any,
) -> str:
    """Call the named provider and return the response text.

    This is the function generated pipeline scripts import.  ``provider`` is
    the same string stored on a node as ``metadata.provider``.

    ``context`` is optional upstream data: a DataFrame preview, a string, or
    any other value.  It is rendered with :func:`preview_for_prompt` and
    appended to ``prompt``.
    """
    if context is not None and context != "":
        rendered = preview_for_prompt(context)
        if rendered:
            prompt = f"{prompt}\n\n{rendered}" if prompt else rendered
    return client_for(provider).complete(prompt, model=model, **kwargs)


def run_model_call(node: Any, input_text: Optional[Any] = None) -> str:
    """Invoke a ``MODEL_CALL`` node against the provider in its metadata.

    Args:
        node: A pipeline node with ``metadata['provider']`` and ``prompt`` /
            ``model`` parameters (duck-typed; no import of the pipeline
            package is required).
        input_text: Optional upstream value.  Strings are appended as-is
            (after truncation); DataFrames and other objects are previewed.

    Returns:
        The model's reply text.
    """
    metadata = getattr(node, "metadata", None) or {}
    provider = metadata.get("provider")
    get_value = getattr(node, "get_parameter_value", None)
    if callable(get_value):
        prompt = get_value("prompt", "") or ""
        model = get_value("model")
    else:
        prompt = ""
        model = None

    return complete(provider, prompt, model=model, context=input_text)
