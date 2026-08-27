"""Errors raised by neural (neuroscience) helpers."""


class NeuralError(Exception):
    """A neural loading or analysis step could not be completed."""


class MissingDependencyError(NeuralError):
    """An optional package (mne, neo, ...) is not installed."""
