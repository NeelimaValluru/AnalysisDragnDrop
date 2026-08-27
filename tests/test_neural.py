"""Neural recording loaders, type-specific analyses, and validation."""

import io
import json
import re

import numpy as np
import pandas as pd
import pytest

from analysis_gui import cli
from analysis_gui.neural import (
    SIGNAL_TYPES,
    MissingDependencyError,
    NeuralAnalyzer,
    NeuralError,
    bandpass_filter,
    delta_f_over_f,
    detect_threshold_events,
    isi_histogram,
    load_neural,
    psth,
    welch_psd,
)
from analysis_gui.pipeline import (
    NEURAL_SIGNAL_TYPES,
    NODE_KINDS,
    PORT_DATA_KINDS,
    CodeGenerator,
    Node,
    PipelineGraph,
    describe_node_kinds,
)

PASSTHROUGH = re.compile(r"^output_0 = data$", re.M)

NEURAL_KINDS = (
    "neural_loader_eeg",
    "neural_loader_lfp",
    "neural_loader_spike",
    "neural_loader_calcium",
    "preprocessor_neural_filter",
    "analyzer_neural_spectrum",
    "analyzer_neural_spike",
    "analyzer_neural_calcium",
)


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


class TestNeuralKinds:
    def test_every_new_kind_is_declared(self):
        for kind in NEURAL_KINDS:
            assert kind in NODE_KINDS
            node = Node.create_from_kind(kind)
            assert NODE_KINDS[kind].in_palette is True
            assert node.ports.inputs or node.node_type.value == "data_loader"

    def test_signal_type_metadata_parallels_provider(self):
        eeg = Node.create_from_kind("neural_loader_eeg")
        spike = Node.create_from_kind("neural_loader_spike")
        lfp = Node.create_from_kind("neural_loader_lfp")
        calcium = Node.create_from_kind("neural_loader_calcium")

        assert eeg.metadata["signal_type"] == "eeg"
        assert spike.metadata["signal_type"] == "spike"
        assert lfp.metadata["signal_type"] == "lfp"
        assert calcium.metadata["signal_type"] == "calcium"
        assert eeg.metadata["file_format"] == "csv"

    def test_loader_output_data_kinds_are_typed(self):
        assert Node.create_neural_loader("eeg").output_ports[0].data_kind == "eeg"
        assert Node.create_neural_loader("spike").output_ports[0].data_kind == "spike"
        assert Node.create_neural_loader("lfp").output_ports[0].data_kind == "lfp"
        assert (
            Node.create_neural_loader("calcium").output_ports[0].data_kind == "calcium"
        )

    def test_csv_loader_is_still_table(self):
        assert Node.create_data_loader("csv").output_ports[0].data_kind == "table"

    def test_neural_analyzers_accept_any(self):
        """CSV tables and typed neural loaders can both feed these nodes."""
        for kind in (
            "preprocessor_neural_filter",
            "analyzer_neural_spectrum",
            "analyzer_neural_spike",
            "analyzer_neural_calcium",
        ):
            inputs = Node.create_from_kind(kind).input_ports
            assert len(inputs) == 1
            assert inputs[0].data_kind == "any"
            assert inputs[0].required is True

    def test_unknown_signal_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown neural signal type"):
            Node.create_neural_loader("meg")

    def test_describe_lists_neural_kinds_and_data_kinds(self):
        code, out, _ = run_cli("describe", "--json")
        payload = json.loads(out)
        kinds = {item["kind"]: item for item in payload["node_kinds"]}

        assert code == 0
        for kind in NEURAL_KINDS:
            assert kinds[kind]["in_palette"] is True
        assert kinds["neural_loader_eeg"]["metadata"]["signal_type"] == "eeg"
        assert "eeg" in payload["port_data_kinds"]
        assert "spike" in payload["port_data_kinds"]
        assert "lfp" in payload["port_data_kinds"]
        assert "calcium" in payload["port_data_kinds"]
        assert payload["port_data_kinds"] == list(PORT_DATA_KINDS)
        assert tuple(NEURAL_SIGNAL_TYPES) == SIGNAL_TYPES

    def test_describe_kind_matches_factory(self):
        described = {item["kind"]: item for item in describe_node_kinds()}
        for kind in NEURAL_KINDS:
            node = Node.create_from_kind(kind)
            assert described[kind]["ports"] == node.ports.to_dict()
            assert described[kind]["metadata"] == node.metadata


class TestNeuralCodegen:
    def test_eeg_loader_calls_load_neural(self):
        node = Node.create_neural_loader("eeg")
        node.parameters["file_path"].value = "session.edf"
        node.parameters["file_format"].value = "edf"
        code = _code_for(node)
        _assert_real_codegen(
            code,
            "from analysis_gui.neural import load_neural",
            "load_neural('session.edf'",
            "signal_type='eeg'",
            "file_format='edf'",
        )
        assert "pd.read_csv" not in code

    def test_spike_loader_passes_time_column(self):
        code = _code_for(Node.create_neural_loader("spike"))
        _assert_real_codegen(
            code, "signal_type='spike'", "time_column='time'", "unit_column='unit'"
        )

    def test_filter_is_not_a_passthrough(self):
        node = Node.create_preprocessor("neural_filter")
        node.parameters["low_hz"].value = 8
        node.parameters["high_hz"].value = 12
        node.parameters["notch_hz"].value = 60
        code = _code_for(node)
        _assert_real_codegen(
            code,
            "bandpass_filter(data",
            "low_hz=8",
            "high_hz=12",
            "notch_hz=60",
        )

    def test_spectrum_calls_welch(self):
        node = Node.create_analyzer("neural_spectrum")
        node.parameters["nperseg"].value = 128
        code = _code_for(node)
        _assert_real_codegen(code, "welch_psd(data", "nperseg=128")

    def test_spike_psth_default(self):
        code = _code_for(Node.create_analyzer("neural_spike"))
        _assert_real_codegen(code, "psth(data", "bin_size=0.05")
        assert "isi_histogram" not in code

    def test_spike_isi(self):
        node = Node.create_analyzer("neural_spike")
        node.parameters["method"].value = "isi"
        node.parameters["n_bins"].value = 20
        code = _code_for(node)
        _assert_real_codegen(code, "isi_histogram(data", "n_bins=20")
        assert "psth(" not in code

    def test_calcium_dff_default(self):
        code = _code_for(Node.create_analyzer("neural_calcium"))
        _assert_real_codegen(code, "delta_f_over_f(data", "baseline_percentile=10.0")
        assert "detect_threshold_events" not in code

    def test_calcium_events(self):
        node = Node.create_analyzer("neural_calcium")
        node.parameters["method"].value = "events"
        node.parameters["threshold"].value = 2.5
        code = _code_for(node)
        _assert_real_codegen(code, "detect_threshold_events(data", "threshold=2.5")
        assert "delta_f_over_f" not in code

    def test_csv_normalize_pipeline_still_uses_read_csv(self):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(loader, normalize)
        code = CodeGenerator(graph).generate()
        compile(code, "<generated>", "exec")
        assert "pd.read_csv(" in code
        assert "load_neural" not in code


class TestNeuralValidation:
    def test_psth_on_eeg_is_incompatible_signal_type(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_neural_loader("eeg"))
        spike = graph.add_node(Node.create_analyzer("neural_spike"))
        graph.add_edge(loader, spike, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)
        codes = [f["code"] for f in payload["findings"]]

        assert code == 1
        assert "incompatible_signal_type" in codes
        finding = next(
            f for f in payload["findings"] if f["code"] == "incompatible_signal_type"
        )
        assert finding["severity"] == "error"
        assert finding["node_id"] == spike
        assert "eeg" in finding["message"]

    def test_eeg_to_normalize_is_incompatible_data_kind(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_neural_loader("eeg"))
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(loader, normalize, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 1
        finding = payload["findings"][0]
        assert finding["code"] == "incompatible_data_kind"
        assert finding["severity"] == "error"
        assert "eeg" in finding["message"]
        assert "table" in finding["message"]

    def test_csv_to_neural_filter_is_allowed(self, tmp_path):
        """Analyzers accept ``any``, so a CSV table can be treated as EEG."""
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        filt = graph.add_node(Node.create_preprocessor("neural_filter"))
        graph.add_edge(loader, filt, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["findings"] == []

    def test_eeg_filter_spectrum_is_valid(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_neural_loader("eeg"))
        filt = graph.add_node(Node.create_preprocessor("neural_filter"))
        spec = graph.add_node(Node.create_analyzer("neural_spectrum"))
        graph.add_edge(loader, filt, source_port="output", target_port="data")
        graph.add_edge(filt, spec, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0, payload["findings"]
        assert payload["findings"] == []

    def test_calcium_events_reject_lfp(self, tmp_path):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_neural_loader("lfp"))
        calcium = graph.add_node(Node.create_analyzer("neural_calcium"))
        graph.add_edge(loader, calcium, source_port="output", target_port="data")
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        codes = [f["code"] for f in json.loads(out)["findings"]]

        assert code == 1
        assert "incompatible_signal_type" in codes


class TestNeuralHelpers:
    def test_load_neural_csv_eeg(self, tmp_path):
        path = tmp_path / "eeg.csv"
        path.write_text("ch0,ch1\n0.1,0.2\n0.3,0.4\n")
        frame = load_neural(str(path), signal_type="eeg", file_format="csv")
        assert frame.shape == (2, 2)
        assert frame.attrs["signal_type"] == "eeg"

    def test_load_neural_npy_spike(self, tmp_path):
        path = tmp_path / "spikes.npy"
        np.save(path, np.array([0.01, 0.05, 0.12]))
        frame = load_neural(str(path), signal_type="spike", file_format="npy")
        assert "time" in frame.columns
        assert list(frame["time"]) == pytest.approx([0.01, 0.05, 0.12])

    def test_load_edf_without_mne_is_a_pip_error(self, tmp_path, monkeypatch):
        import analysis_gui.neural.io as io_mod

        def boom(name):
            raise ImportError("no mne")

        monkeypatch.setattr(io_mod, "import_module", boom)
        with pytest.raises(MissingDependencyError, match="pip install mne"):
            load_neural("x.edf", signal_type="eeg", file_format="edf")

    def test_unknown_signal_type_errors(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text("a\n1\n")
        with pytest.raises(NeuralError, match="Unknown signal type"):
            load_neural(str(path), signal_type="meg")

    def test_bandpass_keeps_in_band_sine(self):
        rate = 250.0
        t = np.arange(0, 2.0, 1.0 / rate)
        signal = np.sin(2 * np.pi * 10.0 * t)
        filtered = bandpass_filter(signal, sampling_rate=rate, low_hz=8.0, high_hz=12.0)
        rms = float(np.sqrt(np.mean(filtered.to_numpy() ** 2)))
        assert rms > 0.3

        blocked = bandpass_filter(signal, sampling_rate=rate, low_hz=30.0, high_hz=40.0)
        blocked_rms = float(np.sqrt(np.mean(blocked.to_numpy() ** 2)))
        assert blocked_rms < rms / 3

    def test_welch_peaks_near_tone(self):
        rate = 250.0
        t = np.arange(0, 4.0, 1.0 / rate)
        signal = np.sin(2 * np.pi * 20.0 * t)
        psd = welch_psd(signal, sampling_rate=rate, nperseg=256)
        peak_hz = float(psd.loc[psd.iloc[:, 1].idxmax(), "frequency"])
        assert abs(peak_hz - 20.0) < 2.0

    def test_psth_counts_spikes(self):
        times = pd.DataFrame({"time": [0.01, 0.02, 0.11, 0.12, 0.13]})
        hist = psth(times, bin_size=0.1, t_start=0.0, t_end=0.2)
        assert hist["count"].sum() == 5
        assert list(hist["count"]) == [2.0, 3.0]

    def test_isi_histogram_has_mass(self):
        times = np.array([0.0, 0.1, 0.2, 0.3, 0.4])
        hist = isi_histogram(times, n_bins=10, max_isi=0.2)
        assert hist["count"].sum() == 4

    def test_delta_f_over_f_zero_at_baseline(self):
        traces = np.ones((50, 2)) * 10.0
        traces[30:35] = 20.0
        dff = delta_f_over_f(traces, baseline_percentile=10.0)
        assert dff.iloc[:10].mean().mean() == pytest.approx(0.0, abs=1e-9)
        assert dff.iloc[30:35].mean().mean() == pytest.approx(1.0, abs=1e-9)

    def test_threshold_events_find_transients(self):
        traces = np.zeros((100, 1))
        traces[40] = 10.0
        traces[70] = 10.0
        events = detect_threshold_events(traces, threshold=3.0, sampling_rate=10.0)
        assert len(events) == 2
        assert list(events["time"]) == pytest.approx([4.0, 7.0])

    def test_neural_analyzer_stub_still_loads_a_path(self):
        analyzer = NeuralAnalyzer()
        analyzer.load_model("/tmp/model.h5")
        assert analyzer.model_path == "/tmp/model.h5"
