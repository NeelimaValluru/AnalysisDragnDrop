"""Scientific query-expansion and API-equivalence tables for ApiIntentMatch.

This module is **data**, not a model.  Matching, alignment, and query parsing
all read from here so synonyms are not copied into twenty call sites.

Entries include SpikeInterface-style names (``bandpass_filter``,
``common_reference``, ``whiten``, ``Kilosort``, ``quality_metrics``,
``ISI violation``, ``waveforms``, ``templates``, ``export_to_phy``,
``spikeinterface.preprocessing``, ``sorter``, ``PSTH``, ``raster``) so a
text query can hit that vocabulary before those nodes exist in a corpus.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# Analysis words / identifiers → canonical callees (behavior-view expansion).
# Order in each tuple is the preferred call sequence for alignment queries.
OPERATION_APIS: Dict[str, Tuple[str, ...]] = {
    "bandpass": ("signal.butter", "signal.sosfilt"),
    "bandpass_filter": ("signal.butter", "signal.sosfilt", "bandpass_filter"),
    "lowpass": ("signal.butter", "signal.sosfilt"),
    "highpass": ("signal.butter", "signal.sosfilt"),
    "notch": ("signal.iirnotch", "signal.sosfilt"),
    "filter": ("signal.butter", "signal.sosfilt", "signal.filtfilt"),
    "filtfilt": ("signal.filtfilt",),
    "butter": ("signal.butter",),
    "sosfilt": ("signal.sosfilt",),
    "welch": ("signal.welch",),
    "psd": ("signal.welch", "signal.periodogram"),
    "periodogram": ("signal.periodogram",),
    "fft": ("np.fft.rfft", "np.fft.fft"),
    "psth": ("np.histogram", "np.bincount"),
    "histogram": ("np.histogram",),
    "raster": ("plt.eventplot",),
    "plot": ("plt.plot", "plt.show"),
    "common_reference": ("common_reference",),
    "whiten": ("whiten",),
    "kilosort": ("Kilosort", "sorter"),
    "sorter": ("sorter", "Kilosort"),
    "quality_metrics": ("quality_metrics",),
    "isi": ("isi_violations", "quality_metrics"),
    "violation": ("isi_violations",),
    "isi_violations": ("isi_violations", "quality_metrics"),
    "waveforms": ("waveforms",),
    "templates": ("templates",),
    "export_to_phy": ("export_to_phy",),
    "phy": ("export_to_phy",),
    "spikeinterface": ("spikeinterface.preprocessing",),
    "preprocessing": ("spikeinterface.preprocessing",),
}

# Multi-word phrases looked up in the raw query (lowercase).
OPERATION_PHRASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("isi violation", ("isi_violations", "quality_metrics")),
    ("quality metrics", ("quality_metrics",)),
    ("common reference", ("common_reference",)),
    ("export to phy", ("export_to_phy",)),
    ("spike interface", ("spikeinterface.preprocessing",)),
)

# Alignment substitution groups: names in a group score as near-matches.
# Canonicalization already maps numpy.mean → np.mean; groups cover leftovers
# such as SpikeInterface bandpass_filter ≈ scipy.signal.butter.
API_EQUIV_GROUPS: Tuple[FrozenSet[str], ...] = (
    frozenset({"np.mean", "numpy.mean", "mean"}),
    frozenset({"signal.butter", "bandpass_filter", "butter"}),
    frozenset({"signal.sosfilt", "sosfilt", "signal.filtfilt", "filtfilt"}),
    frozenset({"common_reference", "cmr", "car"}),
    frozenset({"plt.plot", "plot", "plt.show"}),
    frozenset({"plt.eventplot", "raster"}),
    frozenset({"np.histogram", "histogram", "psth"}),
    frozenset({"Kilosort", "kilosort", "sorter"}),
    frozenset({"export_to_phy", "phy"}),
    frozenset({"isi_violations", "isi"}),
    frozenset({"spikeinterface.preprocessing", "preprocessing"}),
)

# Data-kind hints used by the kind prior (eeg / spike / lfp / calcium / table).
KIND_HINTS: Dict[str, Tuple[str, ...]] = {
    "eeg": ("eeg", "electroencephal", "electroencephalogram", "scalp"),
    "spike": (
        "spike",
        "spikes",
        "psth",
        "raster",
        "isi",
        "spiketrain",
        "kilosort",
        "spikeinterface",
        "phy",
        "sorter",
        "waveforms",
        "templates",
    ),
    "lfp": ("lfp", "localfield", "localfieldpotential"),
    "calcium": ("calcium", "gcamp", "ca2"),
    "table": ("table", "dataframe", "csv"),
}
