"""Lazy MNE wrappers used by generated EEG pipeline code.

Install with ``pip install 'analysis-gui[eeg]'`` (or ``[neural]`` — both extras
currently pull MNE).  This module must import without MNE: every MNE symbol is
loaded inside the helpers.  Code generation ``compile()``s the call sites
without MNE installed; runtime raises :class:`MissingDependencyError` with a
pip hint.

Numpy filter / PSD stay in :mod:`analysis_gui.neural.signals`.  These helpers
cover montage, ICA, and epoching / ERP only.
"""

from importlib import import_module
from typing import Any, Optional

import numpy as np
import pandas as pd

from .errors import MissingDependencyError, NeuralError
from .signals import _as_array, _restore

EEG_EXTRA = "analysis-gui[eeg]"
EEG_INSTALL = f"pip install mne   (or pip install '{EEG_EXTRA}')"


def _require_mne():
    """Import MNE, or raise a pip-install hint."""
    try:
        return import_module("mne")
    except ImportError as exc:
        raise MissingDependencyError(
            "EEG ICA, epoching, and montage nodes require mne. "
            f"Install it with: {EEG_INSTALL}"
        ) from exc


def _as_raw(data: Any, sampling_rate: float, montage: Optional[str] = None):
    """Build an ``mne.io.RawArray`` from a samples × channels table."""
    mne = _require_mne()
    array, _index, columns = _as_array(data)
    if array.size == 0:
        raise NeuralError("No samples to convert to MNE Raw")
    names = [str(name) for name in columns]
    info = mne.create_info(ch_names=names, sfreq=float(sampling_rate), ch_types="eeg")
    raw = mne.io.RawArray(np.asarray(array).T, info, verbose="error")
    if montage:
        try:
            raw.set_montage(montage, on_missing="ignore", verbose="error")
        except Exception:
            pass
    return raw, names


def _raw_to_frame(raw: Any, columns) -> pd.DataFrame:
    values = np.asarray(raw.get_data()).T
    names = list(raw.ch_names) if getattr(raw, "ch_names", None) else columns
    frame = _restore(values, None, names)
    sfreq = getattr(getattr(raw, "info", None), "get", lambda *_: None)("sfreq")
    if sfreq is None:
        info = getattr(raw, "info", None)
        if isinstance(info, dict):
            sfreq = info.get("sfreq")
        else:
            try:
                sfreq = info["sfreq"]
            except Exception:
                sfreq = None
    if sfreq is not None:
        frame.attrs["sampling_rate"] = float(sfreq)
    frame.attrs["signal_type"] = "eeg"
    return frame


def set_montage(
    data: Any,
    montage: str = "standard_1020",
    sampling_rate: float = 250.0,
) -> pd.DataFrame:
    """Attach a standard MNE montage and return the samples × channels table.

    Channel positions live on the MNE Raw object; the DataFrame values are
    unchanged.  ``montage`` is an MNE montage name such as ``standard_1020``.
    """
    raw, columns = _as_raw(data, sampling_rate, montage=montage)
    try:
        raw.set_montage(str(montage), on_missing="ignore", verbose="error")
    except Exception as exc:
        raise NeuralError(f"Could not set montage {montage!r}: {exc}") from exc
    return _raw_to_frame(raw, columns)


def fit_ica(
    data: Any,
    n_components: int = 0,
    sampling_rate: float = 250.0,
    montage: str = "",
) -> pd.DataFrame:
    """Fit MNE ICA and apply it, returning cleaned samples × channels.

    ``n_components <= 0`` lets MNE choose.  No automatic EOG rejection: that
    needs named EOG channels which a generic table may not have.
    """
    mne = _require_mne()
    raw, columns = _as_raw(data, sampling_rate, montage=montage or None)
    kwargs = {"random_state": 97}
    n_comp = int(n_components or 0)
    if n_comp > 0:
        kwargs["n_components"] = n_comp
    ica = mne.preprocessing.ICA(**kwargs)
    ica.fit(raw, verbose="error")
    cleaned = ica.apply(raw.copy(), verbose="error")
    return _raw_to_frame(cleaned, columns)


def epoch_erp(
    data: Any,
    sampling_rate: float = 250.0,
    tmin: float = -0.2,
    tmax: float = 0.5,
    event_id: int = 1,
    event_column: str = "",
    montage: str = "",
) -> pd.DataFrame:
    """Epoch around events and return the evoked (ERP) average.

    If ``event_column`` names a column of sample indices or 0/1 markers, those
    become events.  Otherwise a single event is placed at the midpoint so the
    node still produces an evoked table on a continuous recording.
    """
    mne = _require_mne()
    raw, columns = _as_raw(data, sampling_rate, montage=montage or None)
    events = _events_from_data(data, raw, event_column, int(event_id))
    epochs = mne.Epochs(
        raw,
        events,
        event_id=int(event_id),
        tmin=float(tmin),
        tmax=float(tmax),
        baseline=(None, 0),
        preload=True,
        verbose="error",
    )
    evoked = epochs.average()
    times = np.asarray(evoked.times)
    values = np.asarray(evoked.data).T
    names = list(evoked.ch_names) if getattr(evoked, "ch_names", None) else columns
    frame = pd.DataFrame(values, columns=list(names))
    frame.insert(0, "time", times)
    frame.attrs["signal_type"] = "eeg"
    frame.attrs["sampling_rate"] = float(sampling_rate)
    return frame


def _events_from_data(data: Any, raw: Any, event_column: str, event_id: int) -> Any:
    """Build an MNE events array (N × 3) from a column or a midpoint sample."""
    n_times = int(raw.n_times)
    samples: list = []
    if event_column and isinstance(data, pd.DataFrame) and event_column in data.columns:
        series = data[event_column]
        numeric = pd.to_numeric(series, errors="coerce")
        unique = numeric.dropna().unique()
        # 0/1 stim track vs a list of sample indices.
        if set(unique.tolist()) <= {0, 1, 0.0, 1.0}:
            samples = [int(i) for i, flag in enumerate(numeric) if flag == 1]
        else:
            samples = [
                int(v) for v in numeric.dropna().tolist() if 0 <= int(v) < n_times
            ]
    if not samples:
        samples = [max(n_times // 2, 0)]
    events = np.column_stack(
        [
            np.asarray(samples, dtype=int),
            np.zeros(len(samples), dtype=int),
            np.full(len(samples), event_id, dtype=int),
        ]
    )
    return events
