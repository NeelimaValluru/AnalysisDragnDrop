"""Neuroscience helpers used by generated pipeline code.

This package is for *neural recordings* (EEG, spike trains, LFP, calcium),
not the TensorFlow model-file stub in :mod:`analysis_gui.neural.analyzer`.

Helpers are imported the same way generated model-call code imports
``analysis_gui.models.complete``: the pipeline package itself never imports
this module, so the headless CLI stays free of pandas-at-import in
``analysis_gui.pipeline``. Optional readers (MNE for EDF/FIF, SpikeInterface
for spike-sorting stages) are lazy and raise a ``pip install`` error at
call time.
"""

from .analyzer import NeuralAnalyzer
from .errors import MissingDependencyError, NeuralError
from .io import FILE_FORMATS, SIGNAL_TYPES, load_neural
from .mne_nodes import epoch_erp, fit_ica, set_montage
from .signals import (
    bandpass_filter,
    delta_f_over_f,
    detect_threshold_events,
    isi_histogram,
    psth,
    welch_psd,
)

__all__ = [
    "FILE_FORMATS",
    "MissingDependencyError",
    "NeuralAnalyzer",
    "NeuralError",
    "SIGNAL_TYPES",
    "bandpass_filter",
    "delta_f_over_f",
    "detect_threshold_events",
    "epoch_erp",
    "fit_ica",
    "isi_histogram",
    "load_neural",
    "psth",
    "set_montage",
    "welch_psd",
]
