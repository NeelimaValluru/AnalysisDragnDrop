"""Data loading helpers used by generated pipelines and the CLI.

CSV loading goes through pandas.  ``file_path`` may be a local path or a URI
(see :mod:`analysis_gui.utils.uris`).
"""

from .uris import resolve_data_uri


def load_csv(filepath: str, delimiter: str = ","):
    """Load a CSV, resolving URIs to a local path first."""
    import pandas as pd

    return pd.read_csv(resolve_data_uri(filepath), delimiter=delimiter)
