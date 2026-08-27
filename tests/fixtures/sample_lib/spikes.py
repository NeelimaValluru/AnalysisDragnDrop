"""Spike train summaries."""

import numpy as np


def spike_psth(spike_times, bin_size: float = 0.01):
    """Compute a PSTH histogram from spike times."""
    counts, _edges = np.histogram(spike_times, bins=50)
    return counts
