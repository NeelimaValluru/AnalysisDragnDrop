"""Resolve data locations to a local filesystem path.

Loaders (CSV, neural, SpikeInterface recordings) accept a ``file_path`` that
is a local path **or** a URI.  This module is imported lazily by those
loaders; it uses only the standard library at import time.

Supported schemes (honest list)
-------------------------------
* **local path** — returned as-is (relative paths stay relative).
* **file://** — converted with :mod:`urllib`; no network.
* **http:// and https://** — downloaded with :mod:`urllib.request` to a temp
  file.  Optional; needs network at run time.
* **s3://** — ``boto3`` if installed (``pip install boto3`` or
  ``analysis-gui[s3]``).  Not a hard dependency.
* **gs://** — ``google.cloud.storage`` if installed
  (``pip install google-cloud-storage`` or ``analysis-gui[gcs]``).

NWB / BIDS are **not** URI schemes.  A path ending in ``.nwb`` or a directory
that looks like a BIDS root (``dataset_description.json``) is still a local
path; neural and SI loaders inspect the suffix / layout after resolution.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Optional
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname, urlretrieve

SUPPORTED_SCHEMES = ("file", "http", "https", "s3", "gs")


class UriError(ValueError):
    """The URI could not be resolved to a local path."""


def is_data_uri(path: str) -> bool:
    """True when ``path`` uses a scheme this module knows about."""
    if not path or not isinstance(path, str):
        return False
    scheme = urlparse(path.strip()).scheme.lower()
    return scheme in SUPPORTED_SCHEMES


def looks_like_nwb(path: str) -> bool:
    """True when ``path`` names a Neurodata Without Borders file."""
    if not path:
        return False
    cleaned = path.split("?", 1)[0].rstrip("/").lower()
    return cleaned.endswith(".nwb")


def looks_like_bids(path: str) -> bool:
    """True when ``path`` is a directory with ``dataset_description.json``."""
    if not path or not os.path.isdir(path):
        return False
    return os.path.isfile(os.path.join(path, "dataset_description.json"))


def sha256_file(path: str) -> Optional[str]:
    """SHA-256 hex digest of a readable file, or ``None``."""
    try:
        if not os.path.isfile(path):
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def resolve_data_uri(path: str, cwd: Optional[str] = None) -> str:
    """Return a local filesystem path for ``path``.

    ``cwd`` is prepended to relative local paths (and used as the download
    directory for remote schemes when possible).  Remote downloads go to a
    NamedTemporaryFile that is not deleted (the caller / OS cleans up).
    """
    if not path or not isinstance(path, str):
        raise UriError("file_path is empty")
    raw = path.strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()

    if not scheme or scheme not in SUPPORTED_SCHEMES:
        return _local_path(raw, cwd)

    if scheme == "file":
        local = url2pathname(unquote(parsed.path))
        if os.name == "nt" and local.startswith("\\") and parsed.netloc:
            local = f"//{parsed.netloc}{local}"
        return local

    if scheme in ("http", "https"):
        return _download_http(raw)

    if scheme == "s3":
        return _download_s3(parsed)

    if scheme == "gs":
        return _download_gcs(parsed)

    raise UriError(f"Unsupported URI scheme {scheme!r} in {path!r}")


def _local_path(raw: str, cwd: Optional[str]) -> str:
    if os.path.isabs(raw) or cwd is None:
        return raw
    return os.path.join(cwd, raw)


def _download_http(url: str) -> str:
    suffix = os.path.splitext(urlparse(url).path)[1] or ".dat"
    handle = tempfile.NamedTemporaryFile(
        prefix="analysis-gui-", suffix=suffix, delete=False
    )
    handle.close()
    try:
        urlretrieve(url, handle.name)
    except Exception as exc:
        raise UriError(f"Could not download {url}: {exc}") from exc
    return handle.name


def _download_s3(parsed) -> str:
    try:
        import boto3
    except ImportError as exc:
        raise UriError(
            "s3:// URIs require boto3. Install it with: "
            "pip install boto3   (or pip install 'analysis-gui[s3]')"
        ) from exc
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise UriError(f"Invalid s3 URI: s3://{bucket}/{key}")
    suffix = os.path.splitext(key)[1] or ".dat"
    handle = tempfile.NamedTemporaryFile(
        prefix="analysis-gui-s3-", suffix=suffix, delete=False
    )
    handle.close()
    try:
        boto3.client("s3").download_file(bucket, key, handle.name)
    except Exception as exc:
        raise UriError(f"Could not download s3://{bucket}/{key}: {exc}") from exc
    return handle.name


def _download_gcs(parsed) -> str:
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise UriError(
            "gs:// URIs require google-cloud-storage. Install it with: "
            "pip install google-cloud-storage   "
            "(or pip install 'analysis-gui[gcs]')"
        ) from exc
    bucket_name = parsed.netloc
    blob_name = parsed.path.lstrip("/")
    if not bucket_name or not blob_name:
        raise UriError(f"Invalid gs URI: gs://{bucket_name}/{blob_name}")
    suffix = os.path.splitext(blob_name)[1] or ".dat"
    handle = tempfile.NamedTemporaryFile(
        prefix="analysis-gui-gs-", suffix=suffix, delete=False
    )
    handle.close()
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        bucket.blob(blob_name).download_to_filename(handle.name)
    except Exception as exc:
        raise UriError(
            f"Could not download gs://{bucket_name}/{blob_name}: {exc}"
        ) from exc
    return handle.name
