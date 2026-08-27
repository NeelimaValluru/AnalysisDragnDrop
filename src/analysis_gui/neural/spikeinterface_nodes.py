"""Thin SpikeInterface wrappers used by generated pipeline code.

SpikeInterface is an optional extra (``pip install 'analysis-gui[spike]'``).
This module must import without it: every SI symbol is loaded inside the
helpers via :func:`importlib.import_module`.

Coverage (SI stages this wraps)
--------------------------------
* **extractors** — ``read_binary``, ``read_nwb_recording``, ``read_spikeglx``,
  ``read_openephys``, ``read_intan``, ``read_blackrock``, ``read_neuralynx``,
  ``read_mearec``, ``read_bids``. Any other ``format`` string is passed
  through as ``spikeinterface.extractors.read_<format>``. Neuropixels-as-a-format
  is not a separate reader; SpikeGLX / Open Ephys files from NP probes use those
  extractors. A ``.nwb`` suffix or a BIDS root (``dataset_description.json``)
  is inferred when ``format`` is still the default.
* **preprocessing** — ``bandpass_filter``, ``highpass_filter``,
  ``notch_filter``, ``common_reference``, ``whiten``, ``phase_shift``,
  ``blank_saturation``, ``correct_motion`` (SI motion correction).
* **sorters** — ``run_sorter(name, recording, ...)``. Sorter *binaries*
  (GPU Kilosort, MATLAB mountainsort, …) are not vendored.
* **postprocessing** — ``spikeinterface.core.create_sorting_analyzer``
  (WaveformExtractor was removed after SI 0.100).
* **qualitymetrics** — ``SortingAnalyzer.compute("quality_metrics")``.
* **curation** — threshold ``select_units`` on the metrics table.
* **exporters** — ``export_to_phy``; NWB via neuroconv; ``export_report``;
  SortingView as an emitted ``plot_sorting_summary(..., backend="sortingview")``
  call (no embedded webview).
* **comparison** — ``compare_two_sorters``.

Not wrapped: SpikeInterface-GUI, and every external sorter binary SI can launch.
``file_path`` may be a local path or a URI (see :mod:`analysis_gui.utils.uris`).
"""

from importlib import import_module
from inspect import signature
from typing import Any, Iterable, List, Optional, Sequence, Union

from .errors import MissingDependencyError, NeuralError

SPIKEINTERFACE_EXTRA = "analysis-gui[spike]"
SPIKEINTERFACE_INSTALL = (
    "pip install spikeinterface   " f"(or pip install '{SPIKEINTERFACE_EXTRA}')"
)

#: Formats the recording node lists in its dropdown. Anything else is a
#: passthrough ``read_<format>`` name on ``spikeinterface.extractors``.
SI_RECORDING_FORMATS = (
    "binary",
    "nwb",
    "spikeglx",
    "openephys",
    "intan",
    "blackrock",
    "neuralynx",
    "mearec",
    "bids",
)

SI_PREPROCESS_METHODS = (
    "bandpass_filter",
    "highpass_filter",
    "notch_filter",
    "common_reference",
    "whiten",
    "phase_shift",
    "blank_saturation",
    "correct_motion",
)

#: Names passed to ``spikeinterface.sorters.run_sorter``. ``simple`` is the
#: built-in threshold sorter; GPU Kilosort is not bundled.
SI_SORTER_NAMES = (
    "kilosort4",
    "mountainsort5",
    "spykingcircus2",
    "tridesclous",
    "herdingspikes",
    "simple",
)

SI_ANALYZER_EXTENSIONS = (
    "random_spikes",
    "waveforms",
    "templates",
    "noise_levels",
)

SI_QUALITY_METRICS = (
    "snr",
    "isi_violation",
    "presence_ratio",
    "firing_rate",
    "isolation_distance",
)

SI_EXPORT_METHODS = ("phy", "nwb", "sortingview", "report")

_ISI_COLUMNS = ("isi_violations_ratio", "isi_violation", "isi_violations")


def require_spikeinterface():
    """Import ``spikeinterface``, or raise a pip-install hint."""
    try:
        return import_module("spikeinterface")
    except ImportError as exc:
        raise MissingDependencyError(
            "SpikeInterface nodes require spikeinterface. "
            f"Install it with: {SPIKEINTERFACE_INSTALL}"
        ) from exc


def _import_si(module: str):
    """Import a SpikeInterface submodule after checking the package exists."""
    require_spikeinterface()
    try:
        return import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(
            f"SpikeInterface submodule {module!r} could not be imported. "
            f"Install it with: {SPIKEINTERFACE_INSTALL}"
        ) from exc


def _parse_name_list(
    value: Optional[Union[str, Sequence[str]]], default: Sequence[str]
) -> List[str]:
    """Split a comma-separated parameter into names, or return ``default``."""
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        names = [part.strip() for part in value.split(",")]
        return [name for name in names if name] or list(default)
    return [str(item) for item in value]


def _call_extractor(reader: Any, path: str, **extra: Any) -> Any:
    """Call an extractor, preferring folder_path / file_path from its signature."""
    try:
        params = signature(reader).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = dict(extra)
    if "folder_path" in params:
        kwargs.setdefault("folder_path", path)
    elif "file_path" in params:
        kwargs.setdefault("file_path", path)
    elif "file_paths" in params:
        kwargs.setdefault("file_paths", path)
    else:
        return reader(path, **extra)
    return reader(**kwargs)


def load_si_recording(
    path: str,
    format: str = "binary",
    sampling_rate: float = 30000.0,
    num_channels: int = 0,
    dtype: str = "int16",
    custom_format: str = "",
) -> Any:
    """Load a SpikeInterface recording.

    Known ``format`` values dispatch to the matching ``read_*`` helper.
    ``custom_format`` (or an unknown ``format`` string) is tried as
    ``spikeinterface.extractors.read_<name>``.  ``path`` may be a local path
    or a URI.  A ``.nwb`` suffix or BIDS root is inferred when ``format`` is
    still ``binary``.
    """
    from ..utils.uris import looks_like_bids, looks_like_nwb, resolve_data_uri

    path = resolve_data_uri(path)
    se = _import_si("spikeinterface.extractors")
    fmt = (custom_format or format or "binary").strip().lower().lstrip(".")
    if fmt in ("binary", "") and looks_like_nwb(path):
        fmt = "nwb"
    elif fmt in ("binary", "") and looks_like_bids(path):
        fmt = "bids"
    if fmt == "binary":
        kwargs: dict = {
            "file_paths": path,
            "sampling_frequency": float(sampling_rate),
            "dtype": dtype,
        }
        channels = int(num_channels or 0)
        if channels > 0:
            kwargs["num_channels"] = channels
        return se.read_binary(**kwargs)
    if fmt == "nwb":
        return se.read_nwb_recording(file_path=path)

    named = {
        "spikeglx": "read_spikeglx",
        "openephys": "read_openephys",
        "intan": "read_intan",
        "blackrock": "read_blackrock",
        "neuralynx": "read_neuralynx",
        "mearec": "read_mearec",
        "bids": "read_bids",
    }
    reader_name = named.get(fmt, f"read_{fmt}")
    reader = getattr(se, reader_name, None)
    if reader is None:
        raise NeuralError(
            f"Unknown SpikeInterface recording format {fmt!r}. "
            f"Known dropdown values: {', '.join(SI_RECORDING_FORMATS)}. "
            "Other extractors can be selected by setting format/custom_format "
            "to the suffix of spikeinterface.extractors.read_<format> "
            "(for example 'blackrock', 'mearec', 'neuralynx')."
        )
    return _call_extractor(reader, path)


def preprocess_si(
    recording: Any,
    method: str = "bandpass_filter",
    freq_min: float = 300.0,
    freq_max: float = 6000.0,
    notch_freq: float = 3000.0,
    notch_q: float = 30.0,
    reference: str = "global",
) -> Any:
    """Apply one ``spikeinterface.preprocessing`` method to ``recording``."""
    prep = _import_si("spikeinterface.preprocessing")
    name = (method or "bandpass_filter").strip()
    if name in ("motion", "motion_correction"):
        name = "correct_motion"
    func = getattr(prep, name, None)
    if func is None and name == "correct_motion":
        motion = _import_si("spikeinterface.preprocessing.motion")
        func = getattr(motion, "correct_motion", None)
    if func is None:
        raise NeuralError(
            f"Unknown SpikeInterface preprocessing method {method!r}. "
            f"Expected one of: {', '.join(SI_PREPROCESS_METHODS)}"
        )
    if name == "bandpass_filter":
        return func(recording, freq_min=float(freq_min), freq_max=float(freq_max))
    if name == "highpass_filter":
        return func(recording, freq_min=float(freq_min))
    if name == "notch_filter":
        return func(recording, freq=float(notch_freq), q=float(notch_q))
    if name == "common_reference":
        return func(recording, reference=reference)
    return func(recording)


def run_si_sorter(
    recording: Any,
    sorter_name: str = "simple",
    folder: str = "si_sorter_output",
) -> Any:
    """Run ``spikeinterface.sorters.run_sorter`` (does not vendor sorter binaries)."""
    sorters = _import_si("spikeinterface.sorters")
    name = (sorter_name or "simple").strip()
    kwargs = {}
    if folder:
        kwargs["folder"] = folder
    return sorters.run_sorter(name, recording, **kwargs)


def create_si_analyzer(
    sorting: Any,
    recording: Any,
    extensions: Optional[Union[str, Sequence[str]]] = None,
) -> Any:
    """Build a ``SortingAnalyzer`` and compute waveform/template extensions.

    Uses :func:`spikeinterface.core.create_sorting_analyzer` (the replacement
    for the removed ``WaveformExtractor``).
    """
    core = _import_si("spikeinterface.core")
    analyzer = core.create_sorting_analyzer(
        sorting=sorting, recording=recording, format="memory"
    )
    names = _parse_name_list(extensions, SI_ANALYZER_EXTENSIONS)
    if names:
        analyzer.compute(list(names))
    return analyzer


def compute_si_metrics(
    analyzer: Any,
    metric_names: Optional[Union[str, Sequence[str]]] = None,
) -> dict:
    """Compute quality metrics on a ``SortingAnalyzer``.

    Returns ``{"analyzer": analyzer, "metrics": dataframe}`` so a node with
    two output ports can address either.
    """
    names = _parse_name_list(metric_names, SI_QUALITY_METRICS)
    if "isolation_distance" in names:
        try:
            analyzer.compute("principal_components")
        except Exception:
            names = [name for name in names if name != "isolation_distance"]
    analyzer.compute("quality_metrics", metric_names=list(names))
    extension = analyzer.get_extension("quality_metrics")
    metrics = extension.get_data() if extension is not None else None
    return {"analyzer": analyzer, "metrics": metrics}


def _metric_series(frame: Any, names: Iterable[str]):
    """Return the first matching column from a metrics table, or ``None``."""
    if frame is None:
        return None
    columns = getattr(frame, "columns", ())
    for name in names:
        if name in columns:
            return frame[name]
    return None


def curate_si(
    analyzer: Any,
    snr_min: float = 5.0,
    isi_violations_max: float = 0.2,
    presence_ratio_min: float = 0.9,
    firing_rate_min: float = 0.1,
    isolation_distance_min: float = 0.0,
) -> Any:
    """Keep units that pass quality-metric thresholds."""
    extension = analyzer.get_extension("quality_metrics")
    if extension is None:
        packed = compute_si_metrics(analyzer)
        analyzer = packed["analyzer"]
        frame = packed["metrics"]
    else:
        frame = extension.get_data()

    if frame is None or getattr(frame, "empty", False):
        return analyzer

    keep = None
    snr = _metric_series(frame, ("snr",))
    if snr is not None:
        keep = snr >= float(snr_min)
    isi = _metric_series(frame, _ISI_COLUMNS)
    if isi is not None:
        mask = isi <= float(isi_violations_max)
        keep = mask if keep is None else keep & mask
    presence = _metric_series(frame, ("presence_ratio",))
    if presence is not None:
        mask = presence >= float(presence_ratio_min)
        keep = mask if keep is None else keep & mask
    rate = _metric_series(frame, ("firing_rate",))
    if rate is not None:
        mask = rate >= float(firing_rate_min)
        keep = mask if keep is None else keep & mask
    if float(isolation_distance_min) > 0:
        isolation = _metric_series(frame, ("isolation_distance",))
        if isolation is not None:
            mask = isolation >= float(isolation_distance_min)
            keep = mask if keep is None else keep & mask

    if keep is None:
        return analyzer
    kept_ids = list(frame.index[keep])
    return analyzer.select_units(kept_ids)


def export_si(
    analyzer: Any,
    method: str = "phy",
    output_path: str = "si_export",
) -> str:
    """Export a ``SortingAnalyzer`` to Phy or NWB.

    Phy uses ``spikeinterface.exporters.export_to_phy``. NWB is no longer in
    SI's exporters module; this tries ``neuroconv.tools.spikeinterface``.
    """
    kind = (method or "phy").strip().lower()
    path = output_path or "si_export"
    if kind == "phy":
        exporters = _import_si("spikeinterface.exporters")
        exporters.export_to_phy(analyzer, path)
        return path
    if kind == "nwb":
        try:
            neuroconv_si = import_module("neuroconv.tools.spikeinterface")
        except ImportError as exc:
            raise MissingDependencyError(
                "NWB export is not in spikeinterface.exporters; it lives in "
                "neuroconv. Install it with: pip install neuroconv"
            ) from exc
        writer = getattr(neuroconv_si, "write_sorting_analyzer", None)
        if writer is None:
            raise NeuralError(
                "neuroconv.tools.spikeinterface.write_sorting_analyzer is missing"
            )
        writer(analyzer, nwbfile_path=path)
        return path
    if kind in ("sortingview", "figurl"):
        widgets = _import_si("spikeinterface.widgets")
        plot = getattr(widgets, "plot_sorting_summary", None)
        if plot is None:
            raise NeuralError(
                "spikeinterface.widgets.plot_sorting_summary is missing; "
                "upgrade spikeinterface or install sortingview"
            )
        plot(analyzer, backend="sortingview")
        return "sortingview"
    if kind in ("report", "export_report"):
        exporters = _import_si("spikeinterface.exporters")
        report = getattr(exporters, "export_report", None)
        if report is None:
            raise NeuralError("spikeinterface.exporters.export_report is missing")
        report(analyzer, output_folder=path)
        return path
    raise NeuralError(
        f"Unknown SpikeInterface export method {method!r}. "
        f"Expected one of: {', '.join(SI_EXPORT_METHODS)}"
    )


def compare_si_sorters(
    sorting1: Any,
    sorting2: Any,
    delta_time: float = 0.4,
    match_score: float = 0.5,
) -> Any:
    """Compare two sortings via ``spikeinterface.comparison.compare_two_sorters``."""
    comparison = _import_si("spikeinterface.comparison")
    return comparison.compare_two_sorters(
        sorting1=sorting1,
        sorting2=sorting2,
        delta_time=float(delta_time),
        match_score=float(match_score),
    )
