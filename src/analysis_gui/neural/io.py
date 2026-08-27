"""Load EEG, LFP, spike, and calcium recordings into tables.

CSV and NumPy files are handled with pandas/numpy. EDF/FIF go through MNE,
imported lazily so ``import analysis_gui.neural`` works without it.  NWB files
and BIDS roots are routed to SpikeInterface's ``read_nwb`` / ``read_bids``
when that extra is installed.

``path`` may be a local path or a URI (``file://``, ``http(s)://``, ``s3://``,
``gs://`` — see :mod:`analysis_gui.utils.uris`).
"""

from importlib import import_module
from typing import Any

import numpy as np
import pandas as pd

from .errors import MissingDependencyError, NeuralError

SIGNAL_TYPES = ("eeg", "spike", "lfp", "calcium")

#: File formats the loader accepts. EDF/FIF require MNE at call time;
#: NWB/BIDS require SpikeInterface.
FILE_FORMATS = ("csv", "npy", "edf", "fif", "nwb", "bids")


def _require_mne():
    """Import MNE, or raise a pip-install hint."""
    try:
        return import_module("mne")
    except ImportError as exc:
        raise MissingDependencyError(
            "Reading EDF/FIF neural files requires mne. "
            "Install it with: pip install mne   (or pip install 'analysis-gui[neural]')"
        ) from exc


def _as_frame(values: Any, columns=None) -> pd.DataFrame:
    """Wrap an array-like as a samples × channels DataFrame."""
    array = np.asarray(values)
    if array.ndim == 0:
        array = array.reshape(1, 1)
    elif array.ndim == 1:
        array = array.reshape(-1, 1)
    elif array.ndim > 2:
        array = array.reshape(array.shape[0], -1)
    if columns is None:
        if array.shape[1] == 1:
            columns = ["ch0"]
        else:
            columns = [f"ch{i}" for i in range(array.shape[1])]
    return pd.DataFrame(array, columns=list(columns))


def _load_csv(path: str, delimiter: str = ",") -> pd.DataFrame:
    return pd.read_csv(path, delimiter=delimiter)


def _load_npy(path: str) -> pd.DataFrame:
    loaded = np.load(path, allow_pickle=False)
    return _as_frame(loaded)


def _load_mne(path: str, file_format: str) -> pd.DataFrame:
    mne = _require_mne()
    if file_format == "edf":
        raw = mne.io.read_raw_edf(path, preload=True, verbose="error")
    elif file_format == "fif":
        raw = mne.io.read_raw_fif(path, preload=True, verbose="error")
    else:
        raise NeuralError(f"Unsupported MNE format: {file_format!r}")
    data = raw.get_data()
    # MNE is channels × time; the pipeline uses samples × channels.
    names = list(raw.ch_names) if getattr(raw, "ch_names", None) else None
    return _as_frame(np.asarray(data).T, columns=names)


def _finalize_spike(
    frame: pd.DataFrame, time_column: str, unit_column: str
) -> pd.DataFrame:
    """Name spike timestamp / unit columns so PSTH and ISI can find them."""
    out = frame.copy()
    if time_column in out.columns:
        pass
    elif out.shape[1] >= 1:
        out = out.rename(columns={out.columns[0]: time_column})
    else:
        raise NeuralError("Spike data has no timestamp column")

    if unit_column and unit_column not in out.columns and out.shape[1] >= 2:
        # Second column is treated as unit id when the named column is absent.
        other = [c for c in out.columns if c != time_column][0]
        out = out.rename(columns={other: unit_column})
    return out


def load_neural(
    path: str,
    signal_type: str = "eeg",
    file_format: str = "csv",
    sampling_rate: float = 250.0,
    delimiter: str = ",",
    time_column: str = "time",
    unit_column: str = "unit",
) -> pd.DataFrame:
    """Load a neural recording as a pandas DataFrame.

    EEG, LFP and calcium are samples × channels. Spike data is one row per
    event, with a timestamp column (and an optional unit-id column).

    ``path`` may be a local path or a URI (see :mod:`analysis_gui.utils.uris`).
    A ``.nwb`` suffix or a BIDS root (``dataset_description.json``) is routed
    to SpikeInterface even when ``file_format`` is still the default ``csv``.

    ``sampling_rate`` is stored on ``DataFrame.attrs`` so downstream helpers
    can read it; generated pipelines still pass it explicitly.

    Raises:
        NeuralError: Unknown ``signal_type`` or ``file_format``.
        MissingDependencyError: EDF/FIF requested but MNE is not installed,
            or NWB/BIDS requested but SpikeInterface is not installed.
    """
    kind = (signal_type or "eeg").strip().lower()
    if kind not in SIGNAL_TYPES:
        raise NeuralError(
            f"Unknown signal type {signal_type!r}. "
            f"Expected one of: {', '.join(SIGNAL_TYPES)}"
        )

    local_path = _resolve_path(path)
    fmt = _infer_neural_format(local_path, file_format)
    if fmt == "csv":
        frame = _load_csv(local_path, delimiter=delimiter)
    elif fmt == "npy":
        frame = _load_npy(local_path)
    elif fmt in ("edf", "fif"):
        frame = _load_mne(local_path, fmt)
    elif fmt in ("nwb", "bids"):
        frame = _load_nwb_or_bids(local_path, fmt, sampling_rate)
    else:
        raise NeuralError(
            f"Unknown neural file format {file_format!r}. "
            f"Expected one of: {', '.join(FILE_FORMATS)}"
        )

    if kind == "spike":
        frame = _finalize_spike(frame, time_column, unit_column)

    frame.attrs["signal_type"] = kind
    frame.attrs["sampling_rate"] = float(sampling_rate)
    return frame


def _resolve_path(path: str) -> str:
    from ..utils.uris import resolve_data_uri

    return resolve_data_uri(path)


def _infer_neural_format(path: str, file_format: str) -> str:
    from ..utils.uris import looks_like_bids, looks_like_nwb

    fmt = (file_format or "csv").strip().lower().lstrip(".")
    if looks_like_nwb(path):
        return "nwb"
    if looks_like_bids(path):
        return "bids"
    return fmt


def _load_nwb_or_bids(path: str, fmt: str, sampling_rate: float) -> pd.DataFrame:
    """Load NWB/BIDS via SpikeInterface, falling back to MNE for NWB EEG."""
    try:
        from .spikeinterface_nodes import load_si_recording

        recording = load_si_recording(path, format=fmt, sampling_rate=sampling_rate)
        traces = np.asarray(recording.get_traces())
        return _as_frame(traces)
    except MissingDependencyError:
        if fmt == "nwb":
            return _load_mne_nwb(path)
        raise


def _load_mne_nwb(path: str) -> pd.DataFrame:
    mne = _require_mne()
    reader = getattr(getattr(mne, "io", None), "read_raw_nwb", None)
    if reader is None:
        raise MissingDependencyError(
            "Reading NWB neural files needs spikeinterface or mne.io.read_raw_nwb. "
            "Install with: pip install 'analysis-gui[spike]'  or  'analysis-gui[eeg]'"
        )
    raw = reader(path, preload=True, verbose="error")
    data = raw.get_data()
    names = list(raw.ch_names) if getattr(raw, "ch_names", None) else None
    return _as_frame(np.asarray(data).T, columns=names)
