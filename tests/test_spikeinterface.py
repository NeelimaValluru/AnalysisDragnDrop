"""SpikeInterface stage nodes: kinds, codegen, validation, lazy import."""

import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from analysis_gui import cli
from analysis_gui.neural.errors import MissingDependencyError, NeuralError
from analysis_gui.neural import spikeinterface_nodes as si_nodes
from analysis_gui.pipeline import (
    NODE_KINDS,
    CodeGenerator,
    Node,
    PipelineGraph,
    describe_node_kinds,
)

PASSTHROUGH = __import__("re").compile(r"^output_0 = data$", __import__("re").M)

SI_KINDS = (
    "neural_si_recording",
    "preprocessor_neural_si",
    "analyzer_neural_si_sort",
    "analyzer_neural_si_analyze",
    "analyzer_neural_si_metrics",
    "analyzer_neural_si_curate",
    "analyzer_neural_si_export",
    "analyzer_neural_si_compare",
)

PALETTE_SI_KINDS = SI_KINDS

SRC = Path(__file__).resolve().parents[1] / "src" / "analysis_gui"


def run_cli(*argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def write_pipeline(tmp_path, data, name="p.pipeline"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def _code_for(node: Node) -> str:
    graph = PipelineGraph()
    graph.add_node(node)
    return CodeGenerator(graph).generate()


def _assert_real_codegen(code: str, *needles: str) -> None:
    compile(code, "<generated>", "exec")
    assert PASSTHROUGH.search(code) is None, code
    for needle in needles:
        assert needle in code, f"expected {needle!r} in:\n{code}"


class TestSiKinds:
    def test_every_stage_kind_is_declared(self):
        for kind in SI_KINDS:
            assert kind in NODE_KINDS
            node = Node.create_from_kind(kind)
            assert node.metadata["signal_type"] == "spike"
            assert node.metadata["backend"] == "spikeinterface"
            assert node.metadata.get("si_stage")

    def test_compare_is_in_the_palette(self):
        assert NODE_KINDS["analyzer_neural_si_compare"].in_palette is True
        for kind in PALETTE_SI_KINDS:
            assert NODE_KINDS[kind].in_palette is True

    def test_numpy_neural_nodes_still_exist(self):
        assert "preprocessor_neural_filter" in NODE_KINDS
        assert "analyzer_neural_spike" in NODE_KINDS
        assert "neural_loader_spike" in NODE_KINDS

    def test_recording_ports_are_spike_not_table(self):
        node = Node.create_si_recording()
        assert node.output_ports[0].data_kind == "spike"
        assert node.input_ports == ()

    def test_preprocess_rejects_eeg_by_data_kind(self):
        recording = Node.create_si_recording().output_ports[0]
        preprocess = Node.create_preprocessor("si_preprocess").input_ports[0]
        eeg = Node.create_neural_loader("eeg").output_ports[0]
        assert preprocess.accepts(recording)
        assert not preprocess.accepts(eeg)

    def test_analyze_has_recording_and_sorting_inputs(self):
        ports = Node.create_analyzer("si_analyze").ports
        assert [p.name for p in ports.inputs] == ["recording", "sorting"]
        assert all(p.data_kind == "spike" and p.required for p in ports.inputs)

    def test_describe_lists_si_kinds(self):
        code, out, _ = run_cli("describe", "--json")
        payload = json.loads(out)
        kinds = {item["kind"]: item for item in payload["node_kinds"]}

        assert code == 0
        for kind in PALETTE_SI_KINDS:
            assert kinds[kind]["in_palette"] is True
        assert kinds["analyzer_neural_si_compare"]["in_palette"] is True
        assert kinds["neural_si_recording"]["metadata"]["signal_type"] == "spike"
        assert kinds["neural_si_recording"]["metadata"]["backend"] == "spikeinterface"

    def test_describe_kind_matches_factory(self):
        described = {item["kind"]: item for item in describe_node_kinds()}
        for kind in SI_KINDS:
            node = Node.create_from_kind(kind)
            assert described[kind]["ports"] == node.ports.to_dict()
            assert described[kind]["metadata"] == node.metadata


class TestSiCodegen:
    def test_recording_calls_load_si_recording(self):
        node = Node.create_si_recording()
        node.parameters["file_path"].value = "session.nwb"
        node.parameters["format"].value = "nwb"
        code = _code_for(node)
        _assert_real_codegen(
            code,
            "from analysis_gui.neural.spikeinterface_nodes import load_si_recording",
            "load_si_recording('session.nwb'",
            "format='nwb'",
            "analysis-gui[spike]",
        )
        assert "load_neural" not in code
        assert "pd.read_csv" not in code

    def test_preprocess_calls_preprocess_si(self):
        node = Node.create_preprocessor("si_preprocess")
        node.parameters["method"].value = "whiten"
        code = _code_for(node)
        _assert_real_codegen(code, "preprocess_si(data", "method='whiten'")

    def test_sort_emits_run_si_sorter(self):
        node = Node.create_analyzer("si_sort")
        node.parameters["sorter_name"].value = "kilosort4"
        code = _code_for(node)
        _assert_real_codegen(code, "run_si_sorter(data", "sorter_name='kilosort4'")

    def test_analyze_uses_named_ports(self):
        graph = PipelineGraph()
        rec = graph.add_node(Node.create_si_recording())
        filt = graph.add_node(Node.create_preprocessor("si_preprocess"))
        sort = graph.add_node(Node.create_analyzer("si_sort"))
        analyze = graph.add_node(Node.create_analyzer("si_analyze"))
        graph.add_edge(rec, filt, source_port="output", target_port="data")
        graph.add_edge(filt, sort, source_port="output", target_port="data")
        graph.add_edge(filt, analyze, source_port="output", target_port="recording")
        graph.add_edge(sort, analyze, source_port="output", target_port="sorting")
        code = CodeGenerator(graph).generate()
        _assert_real_codegen(
            code,
            "create_si_analyzer(",
            "load_si_recording(",
            "preprocess_si(",
            "run_si_sorter(",
        )
        assert "create_si_analyzer(" in code
        assert "_sorting" in code
        assert "_recording" in code

    def test_metrics_curate_export_compile(self):
        for kind, needle in (
            ("si_metrics", "compute_si_metrics"),
            ("si_curate", "curate_si"),
            ("si_export", "export_si"),
            ("si_compare", "compare_si_sorters"),
        ):
            code = _code_for(Node.create_analyzer(kind))
            _assert_real_codegen(code, needle)

    def test_si_pipeline_does_not_import_sklearn_analyzer(self):
        code = _code_for(Node.create_analyzer("si_sort"))
        compile(code, "<generated>", "exec")
        assert "sklearn" not in code


class TestSiValidation:
    def test_eeg_to_si_preprocess_is_incompatible(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_neural_loader("eeg"))
        prep = graph.add_node(Node.create_preprocessor("si_preprocess"))
        graph.add_edge(loader, prep, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)
        codes = [f["code"] for f in payload["findings"]]

        assert code == 1
        assert "incompatible_data_kind" in codes
        assert "incompatible_signal_type" in codes

    def test_si_recording_to_preprocess_is_valid(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_si_recording())
        prep = graph.add_node(Node.create_preprocessor("si_preprocess"))
        graph.add_edge(loader, prep, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0, payload["findings"]
        assert payload["findings"] == []

    def test_si_recording_to_normalize_is_incompatible_data_kind(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_si_recording())
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(loader, normalize, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        finding = json.loads(out)["findings"][0]

        assert code == 1
        assert finding["code"] == "incompatible_data_kind"


class TestSiHelpersLazy:
    def test_module_does_not_import_spikeinterface_at_top_level(self):
        source = (SRC / "neural" / "spikeinterface_nodes.py").read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("spikeinterface")
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("spikeinterface")

    def test_pipeline_package_still_omits_spikeinterface(self):
        pipeline_dir = SRC / "pipeline"
        for module in sorted(pipeline_dir.glob("*.py")):
            for line in module.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")):
                    assert "spikeinterface" not in stripped, module.name

    def test_missing_si_is_a_pip_error(self, monkeypatch):
        def boom(name):
            raise ImportError("no spikeinterface")

        monkeypatch.setattr(si_nodes, "import_module", boom)
        with pytest.raises(MissingDependencyError, match=r"analysis-gui\[spike\]"):
            si_nodes.load_si_recording("rec.bin")

    def test_load_dispatches_known_formats(self, monkeypatch):
        extractors = SimpleNamespace(
            read_binary=MagicMock(return_value="binary-rec"),
            read_nwb_recording=MagicMock(return_value="nwb-rec"),
            read_spikeglx=MagicMock(return_value="glx-rec"),
            read_openephys=MagicMock(return_value="oe-rec"),
            read_intan=MagicMock(return_value="intan-rec"),
            read_blackrock=MagicMock(return_value="br-rec"),
            read_neuralynx=MagicMock(return_value="nlx-rec"),
            read_mearec=MagicMock(return_value="mea-rec"),
            read_bids=MagicMock(return_value="bids-rec"),
        )

        def fake_import(name):
            if name == "spikeinterface":
                return MagicMock()
            if name == "spikeinterface.extractors":
                return extractors
            raise ImportError(name)

        monkeypatch.setattr(si_nodes, "import_module", fake_import)

        assert (
            si_nodes.load_si_recording("a.bin", format="binary", num_channels=64)
            == "binary-rec"
        )
        extractors.read_binary.assert_called()
        assert si_nodes.load_si_recording("a.nwb", format="nwb") == "nwb-rec"
        assert si_nodes.load_si_recording("/glx", format="spikeglx") == "glx-rec"
        assert si_nodes.load_si_recording("/oe", format="openephys") == "oe-rec"
        assert si_nodes.load_si_recording("x.rhd", format="intan") == "intan-rec"
        assert (
            si_nodes.load_si_recording(
                "/br", format="binary", custom_format="blackrock"
            )
            == "br-rec"
        )
        assert si_nodes.load_si_recording("/nlx", format="neuralynx") == "nlx-rec"
        assert si_nodes.load_si_recording("/mea", format="mearec") == "mea-rec"
        assert si_nodes.load_si_recording("/bids", format="bids") == "bids-rec"

    def test_unknown_format_errors(self, monkeypatch):
        extractors = SimpleNamespace()

        def fake_import(name):
            if name in ("spikeinterface", "spikeinterface.extractors"):
                return extractors if "extractors" in name else MagicMock()
            raise ImportError(name)

        monkeypatch.setattr(si_nodes, "import_module", fake_import)
        with pytest.raises(
            NeuralError, match="Unknown SpikeInterface recording format"
        ):
            si_nodes.load_si_recording("x", format="not_a_format")

    def test_preprocess_and_sorter_dispatch(self, monkeypatch):
        bandpass = MagicMock(return_value="filtered")
        run_sorter = MagicMock(return_value="sorting")
        prep = SimpleNamespace(
            bandpass_filter=bandpass,
            whiten=MagicMock(return_value="w"),
            correct_motion=MagicMock(return_value="moved"),
        )
        sorters = SimpleNamespace(run_sorter=run_sorter)

        def fake_import(name):
            if name == "spikeinterface":
                return MagicMock()
            if name == "spikeinterface.preprocessing":
                return prep
            if name == "spikeinterface.sorters":
                return sorters
            raise ImportError(name)

        monkeypatch.setattr(si_nodes, "import_module", fake_import)
        assert si_nodes.preprocess_si("rec", method="bandpass_filter") == "filtered"
        bandpass.assert_called_once()
        kwargs = bandpass.call_args.kwargs
        assert kwargs["freq_min"] == 300.0
        assert si_nodes.run_si_sorter("rec", sorter_name="simple") == "sorting"
        run_sorter.assert_called_once_with("simple", "rec", folder="si_sorter_output")
        assert si_nodes.preprocess_si("rec", method="correct_motion") == "moved"

    def test_curate_keeps_units_passing_thresholds(self):
        frame = pd.DataFrame(
            {
                "snr": [8.0, 2.0],
                "isi_violations_ratio": [0.01, 0.5],
                "presence_ratio": [0.95, 0.2],
                "firing_rate": [5.0, 0.01],
            },
            index=["u1", "u2"],
        )
        analyzer = MagicMock()
        analyzer.get_extension.return_value.get_data.return_value = frame
        analyzer.select_units.return_value = "kept"
        assert si_nodes.curate_si(analyzer) == "kept"
        analyzer.select_units.assert_called_once_with(["u1"])

    def test_export_phy_and_nwb_dispatch(self, monkeypatch):
        exporters = SimpleNamespace(
            export_to_phy=MagicMock(), export_report=MagicMock()
        )
        neuroconv = SimpleNamespace(write_sorting_analyzer=MagicMock())
        widgets = SimpleNamespace(plot_sorting_summary=MagicMock())

        def fake_import(name):
            if name == "spikeinterface":
                return MagicMock()
            if name == "spikeinterface.exporters":
                return exporters
            if name == "spikeinterface.widgets":
                return widgets
            if name == "neuroconv.tools.spikeinterface":
                return neuroconv
            raise ImportError(name)

        monkeypatch.setattr(si_nodes, "import_module", fake_import)
        assert (
            si_nodes.export_si("an", method="phy", output_path="phy_out") == "phy_out"
        )
        exporters.export_to_phy.assert_called_once_with("an", "phy_out")
        assert (
            si_nodes.export_si("an", method="nwb", output_path="out.nwb") == "out.nwb"
        )
        neuroconv.write_sorting_analyzer.assert_called_once()
        assert si_nodes.export_si("an", method="sortingview") == "sortingview"
        widgets.plot_sorting_summary.assert_called()
        assert si_nodes.export_si("an", method="report", output_path="rep") == "rep"
        exporters.export_report.assert_called()
