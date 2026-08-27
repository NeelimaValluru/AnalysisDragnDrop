"""MNE-depth EEG nodes: codegen without mne; runtime lazy-imports."""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from analysis_gui.neural.errors import MissingDependencyError
from analysis_gui.neural import mne_nodes
from analysis_gui.pipeline import NODE_KINDS, CodeGenerator, Node, PipelineGraph

PASSTHROUGH = __import__("re").compile(r"^output_0 = data$", __import__("re").M)

MNE_KINDS = (
    "preprocessor_neural_montage",
    "preprocessor_neural_ica",
    "analyzer_neural_epochs",
)


def _code_for(node: Node) -> str:
    graph = PipelineGraph()
    graph.add_node(node)
    return CodeGenerator(graph).generate()


class TestMneKinds:
    def test_kinds_are_in_the_palette(self):
        for kind in MNE_KINDS:
            assert kind in NODE_KINDS
            assert NODE_KINDS[kind].in_palette is True
            node = Node.create_from_kind(kind)
            code = _code_for(node)
            compile(code, "<generated>", "exec")
            assert PASSTHROUGH.search(code) is None
            assert "analysis-gui[eeg]" in code or "mne_nodes" in code


class TestMneCodegen:
    def test_montage_calls_set_montage(self):
        code = _code_for(Node.create_preprocessor("neural_montage"))
        compile(code, "<generated>", "exec")
        assert "set_montage(" in code
        assert "from analysis_gui.neural.mne_nodes import set_montage" in code

    def test_ica_calls_fit_ica(self):
        code = _code_for(Node.create_preprocessor("neural_ica"))
        compile(code, "<generated>", "exec")
        assert "fit_ica(" in code

    def test_epochs_calls_epoch_erp(self):
        code = _code_for(Node.create_analyzer("neural_epochs"))
        compile(code, "<generated>", "exec")
        assert "epoch_erp(" in code
        assert "import mne" not in code


class TestMneRuntime:
    def test_missing_mne_has_pip_hint(self, monkeypatch):
        def boom(name):
            raise ImportError("no mne")

        monkeypatch.setattr(mne_nodes, "import_module", boom)
        data = pd.DataFrame(np.random.randn(50, 4), columns=list("ABCD"))
        with pytest.raises(MissingDependencyError, match="analysis-gui\\[eeg\\]"):
            mne_nodes.set_montage(data)

    def test_set_montage_with_fake_mne(self, monkeypatch):
        raw = MagicMock()
        raw.get_data.return_value = np.ones((3, 20))
        raw.ch_names = ["A", "B", "C"]
        raw.info = {"sfreq": 250.0}

        ica_mod = MagicMock()
        fake_mne = MagicMock()
        fake_mne.io.RawArray.return_value = raw
        fake_mne.create_info.return_value = {}
        fake_mne.preprocessing = ica_mod

        monkeypatch.setattr(mne_nodes, "_require_mne", lambda: fake_mne)
        data = pd.DataFrame(np.ones((20, 3)), columns=list("ABC"))
        out = mne_nodes.set_montage(data, montage="standard_1020", sampling_rate=250)
        assert out.shape[0] == 20
        raw.set_montage.assert_called()
