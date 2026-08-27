"""Module-level analysis steps that are not wrapped in ``def``.

Discovery should index the class method, the comment-led filter block,
and the uncommented assignment sequence that calls scipy.signal.
"""

import numpy as np
from scipy import signal


class EEGTools:
    def notch_line_noise(self, data, freq: float = 60.0):
        """Notch-filter mains hum from EEG recordings."""
        return data


raw_eeg = np.zeros(512)

# bandpass the EEG
low_hz = 1.0
high_hz = 40.0
sos = signal.butter(4, [low_hz, high_hz], btype="bandpass", fs=250.0, output="sos")
filtered_eeg = signal.sosfilt(sos, raw_eeg)

low = 2.0
high = 30.0
sos2 = signal.butter(4, [low, high], btype="band", fs=250.0, output="sos")
uncommented_bandpass = signal.sosfilt(sos2, raw_eeg)


def _private_wrapper(data):
    def smooth_spikes(traces):
        """Smooth spike trains with a moving average."""
        return traces

    return smooth_spikes(data)
