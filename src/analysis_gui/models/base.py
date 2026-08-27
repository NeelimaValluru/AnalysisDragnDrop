"""Shared helpers for provider clients."""

import os
from importlib import import_module
from typing import Optional

from .errors import MissingAPIKeyError, MissingSDKError


def require_env(var_name: str, provider_label: str) -> str:
    """Return ``var_name`` from the environment, or raise a clear error."""
    value = os.environ.get(var_name, "")
    if value is None or not str(value).strip():
        raise MissingAPIKeyError(
            f"No API key for {provider_label}. Set the {var_name} environment variable."
        )
    return str(value).strip()


def require_package(module_name: str, pip_name: Optional[str] = None):
    """Import ``module_name``, or raise a pip-install hint."""
    pip_name = pip_name or module_name
    try:
        return import_module(module_name)
    except ImportError as exc:
        raise MissingSDKError(
            f"The {pip_name} package is required for this model call. "
            f"Install it with: pip install {pip_name}"
        ) from exc
