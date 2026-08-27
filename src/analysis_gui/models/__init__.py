"""AI model integration for Claude and GPT API calls.

SDKs (``anthropic``, ``openai``) are imported lazily on first call so that
``import analysis_gui.models`` succeeds even when they are not installed.
API keys are read from ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY``.
"""

from enum import Enum
from typing import Optional

from .claude import ClaudeClient
from .dispatch import client_for, complete, preview_for_prompt, run_model_call
from .errors import (
    MissingAPIKeyError,
    MissingSDKError,
    ModelError,
    UnknownProviderError,
)
from .gpt import GPTClient

__all__ = [
    "ClaudeClient",
    "GPTClient",
    "MissingAPIKeyError",
    "MissingSDKError",
    "ModelConfig",
    "ModelError",
    "ModelIntegration",
    "ModelProvider",
    "UnknownProviderError",
    "client_for",
    "complete",
    "preview_for_prompt",
    "run_model_call",
]


class ModelProvider(Enum):
    """Available model providers."""

    CLAUDE = "claude"
    GPT = "gpt"
    OPEN_WEIGHTS = "open_weights"


class ModelConfig:
    """Configuration for an AI model."""

    def __init__(
        self,
        provider: ModelProvider,
        model_name: str,
        api_key: Optional[str] = None,
    ):
        self.provider = provider
        self.model_name = model_name
        self.api_key = api_key


class ModelIntegration:
    """Optional holder for pre-configured clients.

    Prefer :func:`complete` or :func:`run_model_call`, which read API keys
    from the environment.  This class is kept so existing call sites that
    pass a key explicitly still work.
    """

    def __init__(self):
        self.claude_client: Optional[ClaudeClient] = None
        self.gpt_client: Optional[GPTClient] = None

    def configure_claude(self, api_key: str):
        """Configure Claude with an explicit API key."""
        self.claude_client = ClaudeClient(api_key=api_key)

    def configure_gpt(self, api_key: str):
        """Configure GPT with an explicit API key."""
        self.gpt_client = GPTClient(api_key=api_key)

    def call_model(self, provider: ModelProvider, prompt: str, model_name: str) -> str:
        """Call a model, using a configured client if one exists."""
        value = provider.value if isinstance(provider, ModelProvider) else provider
        if value == ModelProvider.CLAUDE.value and self.claude_client is not None:
            return self.claude_client.complete(prompt, model=model_name)
        if value == ModelProvider.GPT.value and self.gpt_client is not None:
            return self.gpt_client.complete(prompt, model=model_name)
        return complete(value, prompt, model=model_name)
