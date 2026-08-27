"""Type-specific neural analyses implemented with numpy/pandas.

Heavy libraries (MNE, Neo, SpikeInterface, TensorFlow) are not imported here.
Generated pipelines call these helpers; the functions accept a DataFrame or
an array and return a DataFrame.
"""

from typing import Any

import numpy as np
import pandas as pd

from .errors import NeuralError


def _as_array(data: Any):
    """Return ``(samples × channels array, index, columns)``."""
    if data is None:
        raise NeuralError("No data to analyze")
    if isinstance(data, pd.DataFrame):
        numeric = data.select_dtypes(include=(np.number,))
        if numeric.empty:
            numeric = data.apply(pd.to_numeric, errors="coerce")
        return numeric.to_numpy(dtype=float), numeric.index, list(numeric.columns)
    if isinstance(data, pd.Series):
        return (
            data.to_numpy(dtype=float).reshape(-1, 1),
            data.index,
            [str(data.name or "ch0")],
        )
    array = np.asarray(data, dtype=float)
    if array.ndim == 0:
        array = array.reshape(1, 1)
    elif array.ndim == 1:
        array = array.reshape(-1, 1)
    elif array.ndim > 2:
        array = array.reshape(array.shape[0], -1)
    columns = [f"ch{i}" for i in range(array.shape[1])]
    return array, None, columns


def _restore(values: np.ndarray, index, columns) -> pd.DataFrame:
    if columns is None or len(columns) != values.shape[1]:
        columns = [f"ch{i}" for i in range(values.shape[1])]
    return pd.DataFrame(values, index=index, columns=list(columns))


def _spike_times(data: Any, time_column: str = "time"):
    """Return (times array, optional unit-id array)."""
    if isinstance(data, pd.DataFrame):
        if time_column in data.columns:
            times = np.asarray(data[time_column], dtype=float)
        else:
            times = np.asarray(data.iloc[:, 0], dtype=float)
        units = None
        if "unit" in data.columns:
            units = np.asarray(data["unit"])
        elif data.shape[1] >= 2 and time_column in data.columns:
            other = [c for c in data.columns if c != time_column][0]
            units = np.asarray(data[other])
        return times, units
    array = np.asarray(data)
    if array.ndim == 2 and array.shape[1] >= 2:
        return np.asarray(array[:, 0], dtype=float), array[:, 1]
    return np.asarray(array, dtype=float).reshape(-1), None


def bandpass_filter(
    data: Any,
    sampling_rate: float = 250.0,
    low_hz: float = 1.0,
    high_hz: float = 40.0,
    notch_hz: float = 0.0,
) -> pd.DataFrame:
    """FFT band-pass a samples × channels recording, with optional notch.

    Frequencies outside ``[low_hz, high_hz]`` are zeroed. When ``notch_hz`` is
    positive, a ±1 Hz bin around that frequency is also removed (line noise).
    """
    sampling_rate = float(sampling_rate)
    if sampling_rate <= 0:
        raise NeuralError("sampling_rate must be positive")
    low_hz = float(low_hz)
    high_hz = float(high_hz)
    if low_hz < 0 or high_hz <= low_hz:
        raise NeuralError("Band-pass requires 0 <= low_hz < high_hz")

    array, index, columns = _as_array(data)
    n_samples = array.shape[0]
    if n_samples < 2:
        return _restore(array, index, columns)

    nyquist = sampling_rate / 2.0
    high_hz = min(high_hz, nyquist)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / sampling_rate)
    spectrum = np.fft.rfft(array, axis=0)
    keep = (freqs >= low_hz) & (freqs <= high_hz)
    notch_hz = float(notch_hz or 0.0)
    if notch_hz > 0:
        keep &= ~((freqs >= notch_hz - 1.0) & (freqs <= notch_hz + 1.0))
    spectrum[~keep] = 0
    filtered = np.fft.irfft(spectrum, n=n_samples, axis=0)
    return _restore(np.real(filtered), index, columns)


def welch_psd(
    data: Any,
    sampling_rate: float = 250.0,
    nperseg: int = 256,
) -> pd.DataFrame:
    """Welch power spectral density, one column per channel.

    The returned table has a ``frequency`` column (Hz) and one power column
    per input channel. Implemented with numpy so headless tests need no SciPy.
    """
    sampling_rate = float(sampling_rate)
    if sampling_rate <= 0:
        raise NeuralError("sampling_rate must be positive")

    array, _, columns = _as_array(data)
    n_samples = array.shape[0]
    if n_samples < 2:
        raise NeuralError("Welch PSD needs at least 2 samples")

    nperseg = int(nperseg)
    if nperseg < 2:
        nperseg = 2
    nperseg = min(nperseg, n_samples)
    hop = max(nperseg // 2, 1)
    window = np.hanning(nperseg)
    # Density scaling: window power, one-sided spectrum, Hz.
    scale = np.sum(window**2) * sampling_rate
    freqs = np.fft.rfftfreq(nperseg, d=1.0 / sampling_rate)
    acc = None
    n_windows = 0
    start = 0
    while start + nperseg <= n_samples:
        segment = array[start : start + nperseg] * window[:, np.newaxis]
        spec = np.fft.rfft(segment, axis=0)
        power = (np.abs(spec) ** 2) / scale
        if acc is None:
            acc = power
        else:
            acc += power
        n_windows += 1
        start += hop
    if n_windows == 0 or acc is None:
        raise NeuralError("Welch PSD could not form a window")
    acc = acc / n_windows
    if acc.shape[0] > 2:
        acc[1:-1] *= 2.0  # one-sided
    frame = pd.DataFrame(acc, columns=list(columns))
    frame.insert(0, "frequency", freqs)
    return frame


def _finite_spikes(data: Any, time_column: str = "time"):
    """Return finite spike times and matching unit ids (or ``None``)."""
    times, units = _spike_times(data, time_column=time_column)
    times = np.asarray(times, dtype=float)
    finite = np.isfinite(times)
    times = times[finite]
    if units is None:
        return times, None
    return times, np.asarray(units)[finite]


def psth(
    data: Any,
    bin_size: float = 0.05,
    t_start: float = 0.0,
    t_end: float = 0.0,
    time_column: str = "time",
) -> pd.DataFrame:
    """Peri-stimulus time histogram (spike counts per bin).

    ``t_end <= 0`` means "use the last spike time". When unit ids are present,
    each unit gets its own count column.
    """
    bin_size = float(bin_size)
    if bin_size <= 0:
        raise NeuralError("bin_size must be positive")

    times, units = _finite_spikes(data, time_column=time_column)
    if times.size == 0:
        return pd.DataFrame({"bin_center": [], "count": []})

    start = float(t_start)
    stop = float(t_end) if float(t_end) > 0 else float(np.max(times))
    if stop <= start:
        stop = start + bin_size
    n_bins = max(int(np.ceil((stop - start) / bin_size)), 1)
    edges = start + np.arange(n_bins + 1, dtype=float) * bin_size
    centers = 0.5 * (edges[:-1] + edges[1:])

    if units is None:
        counts, _ = np.histogram(times, bins=edges)
        return pd.DataFrame({"bin_center": centers, "count": counts.astype(float)})

    frame = pd.DataFrame({"bin_center": centers})
    for unit in pd.unique(units):
        counts, _ = np.histogram(times[units == unit], bins=edges)
        frame[f"unit_{unit}"] = counts.astype(float)
    return frame


def isi_histogram(
    data: Any,
    n_bins: int = 50,
    max_isi: float = 0.0,
    time_column: str = "time",
) -> pd.DataFrame:
    """Histogram of inter-spike intervals.

    ``max_isi <= 0`` uses the largest observed interval.
    """
    times, units = _finite_spikes(data, time_column=time_column)
    if times.size < 2 and units is None:
        return pd.DataFrame({"isi": [], "count": []})

    if units is None:
        intervals = np.diff(np.sort(times))
    else:
        pieces = []
        for unit in pd.unique(units):
            unit_times = np.sort(times[units == unit])
            if unit_times.size >= 2:
                pieces.append(np.diff(unit_times))
        intervals = np.concatenate(pieces) if pieces else np.array([], dtype=float)

    if intervals.size == 0:
        return pd.DataFrame({"isi": [], "count": []})

    cap = float(max_isi) if float(max_isi) > 0 else float(np.max(intervals))
    n_bins = max(int(n_bins), 1)
    counts, edges = np.histogram(intervals, bins=n_bins, range=(0.0, cap))
    centers = 0.5 * (edges[:-1] + edges[1:])
    return pd.DataFrame({"isi": centers, "count": counts.astype(float)})


def delta_f_over_f(
    data: Any,
    baseline_percentile: float = 10.0,
) -> pd.DataFrame:
    """ΔF/F fluorescence: ``(F - F0) / F0`` with F0 a per-channel percentile."""
    array, index, columns = _as_array(data)
    pct = float(baseline_percentile)
    if not 0.0 <= pct <= 100.0:
        raise NeuralError("baseline_percentile must be between 0 and 100")
    baseline = np.percentile(array, pct, axis=0)
    baseline = np.where(np.abs(baseline) < 1e-12, 1e-12, baseline)
    dff = (array - baseline) / baseline
    return _restore(dff, index, columns)


def detect_threshold_events(
    data: Any,
    threshold: float = 3.0,
    sampling_rate: float = 30.0,
) -> pd.DataFrame:
    """Rising-edge events where a channel exceeds ``mean + threshold * std``.

    Returns one row per event with ``time``, ``channel`` and ``amplitude``.
    """
    array, _, columns = _as_array(data)
    k = float(threshold)
    rate = float(sampling_rate) if float(sampling_rate) > 0 else 1.0
    mean = np.nanmean(array, axis=0)
    std = np.nanstd(array, axis=0)
    std = np.where(std < 1e-12, 1.0, std)
    cut = mean + k * std
    above = array > cut[np.newaxis, :]
    rising = np.diff(above.astype(int), axis=0, prepend=0) == 1
    rows = []
    for ch, name in enumerate(columns):
        idx = np.flatnonzero(rising[:, ch])
        for i in idx:
            rows.append(
                {
                    "time": i / rate,
                    "channel": name,
                    "amplitude": float(array[i, ch]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["time", "channel", "amplitude"])
    return pd.DataFrame(rows)
