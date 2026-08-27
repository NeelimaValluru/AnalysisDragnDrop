"""Errors raised by model clients."""


class ModelError(Exception):
    """A model call could not be completed."""


class MissingSDKError(ModelError):
    """The provider's Python package is not installed."""


class MissingAPIKeyError(ModelError):
    """The provider's API key environment variable is unset."""


class UnknownProviderError(ModelError):
    """``metadata.provider`` does not name a supported client."""
