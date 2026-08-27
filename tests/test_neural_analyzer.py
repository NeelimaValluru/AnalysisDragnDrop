"""Tests for neural analyzer module."""

import pytest
from analysis_gui.neural import NeuralAnalyzer


def test_neural_analyzer_init():
    """Test NeuralAnalyzer initialization."""
    analyzer = NeuralAnalyzer()
    assert analyzer.model is None
    assert analyzer.model_path is None


def test_load_model_sets_path():
    """Test that load_model sets the model path."""
    analyzer = NeuralAnalyzer()
    test_path = "/path/to/model.h5"
    analyzer.load_model(test_path)
    assert analyzer.model_path == test_path
