"""Tests for the headless CLI (change 3)."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from analysis_gui import __version__, cli
from analysis_gui.pipeline import SCHEMA_VERSION, Node, PipelineGraph

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


def run_cli(*argv):
    """Run the CLI in-process, returning (exit_code, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def write_pipeline(tmp_path, data, name="p.pipeline"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture
def valid_pipeline(tmp_path):
    graph = PipelineGraph()
    loader = graph.add_node(Node.create_data_loader("csv"))
    normalize = graph.add_node(Node.create_preprocessor("normalize"))
    graph.add_edge(loader, normalize)
    return write_pipeline(tmp_path, graph.to_dict())


@pytest.fixture
def cyclic_pipeline(tmp_path):
    graph = PipelineGraph()
    a = graph.add_node(Node.create_preprocessor("normalize"))
    b = graph.add_node(Node.create_preprocessor("normalize"))
    graph.add_edge(a, b)
    graph.add_edge(b, a)
    return write_pipeline(tmp_path, graph.to_dict(), name="cycle.pipeline")


def assert_envelope(payload):
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["analysis_gui_version"] == __version__


class TestDescribe:
    def test_lists_every_node_kind(self):
        code, out, _ = run_cli("describe", "--json")
        payload = json.loads(out)

        assert code == 0
        assert_envelope(payload)

        kinds = {kind["kind"]: kind for kind in payload["node_kinds"]}
        assert "data_loader" in kinds
        assert "preprocessor_split" in kinds
        assert "model_gpt" in kinds

        clustering = kinds["analyzer_clustering"]
        algorithm = next(
            p for p in clustering["parameters"] if p["name"] == "algorithm"
        )
        assert algorithm["default_value"] == "kmeans"
        assert algorithm["value"] is None
        assert algorithm["options"] == ["kmeans", "dbscan", "hierarchical"]

    def test_feature_select_is_in_the_palette(self):
        _, out, _ = run_cli("describe", "--json")
        kinds = {kind["kind"]: kind for kind in json.loads(out)["node_kinds"]}

        assert kinds["preprocessor_feature_select"]["in_palette"] is True
        assert kinds["data_loader"]["in_palette"] is True

    def test_every_described_kind_is_constructible(self):
        _, out, _ = run_cli("describe", "--json")

        for kind in json.loads(out)["node_kinds"]:
            node = Node.create_from_kind(kind["kind"])
            assert node.node_type.value == kind["node_type"]
            assert sorted(node.parameters) == sorted(
                p["name"] for p in kind["parameters"]
            )

    def test_json_is_the_default(self):
        _, out, _ = run_cli("describe")
        assert json.loads(out)["command"] == "describe"


class TestValidate:
    def test_valid_pipeline(self, valid_pipeline):
        code, out, err = run_cli("validate", valid_pipeline, "--json")
        payload = json.loads(out)

        assert code == 0
        assert_envelope(payload)
        assert payload["valid"] is True
        assert payload["findings"] == []
        assert payload["node_count"] == 2
        assert payload["edge_count"] == 1
        assert payload["file_schema_version"] == SCHEMA_VERSION
        assert err == ""

    def test_cycle_is_reported(self, cyclic_pipeline):
        code, out, err = run_cli("validate", cyclic_pipeline, "--json")
        payload = json.loads(out)

        assert code == 1
        assert payload["valid"] is False
        assert [f["code"] for f in payload["findings"]] == ["cycle_detected"]
        assert "error:" in err

    def test_empty_pipeline_is_reported(self, tmp_path):
        path = write_pipeline(tmp_path, {"version": 1, "nodes": {}, "edges": []})
        code, out, _ = run_cli("validate", path)

        assert code == 1
        assert json.loads(out)["findings"][0]["code"] == "empty_pipeline"

    def test_dangling_edge_is_reported(self, tmp_path, valid_pipeline):
        data = json.loads(Path(valid_pipeline).read_text())
        data["edges"].append({"source": list(data["nodes"])[0], "target": "ghost"})
        path = write_pipeline(tmp_path, data, name="dangling.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 1
        finding = payload["findings"][0]
        assert finding["code"] == "dangling_edge"
        assert finding["node_id"] == "ghost"
        assert finding["edge_index"] == 1

    def test_unknown_node_type_is_reported(self, tmp_path, valid_pipeline):
        data = json.loads(Path(valid_pipeline).read_text())
        node_id = list(data["nodes"])[0]
        data["nodes"][node_id]["node_type"] = "quantum_loader"
        path = write_pipeline(tmp_path, data, name="badtype.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 1
        assert payload["findings"][0]["code"] == "unknown_node_type"
        assert payload["findings"][0]["node_id"] == node_id

    def test_node_id_mismatch_is_reported(self, tmp_path, valid_pipeline):
        data = json.loads(Path(valid_pipeline).read_text())
        node_id = list(data["nodes"])[0]
        data["nodes"][node_id]["id"] = "somebody-else"
        path = write_pipeline(tmp_path, data, name="mismatch.pipeline")

        code, out, _ = run_cli("validate", path)

        assert code == 1
        assert json.loads(out)["findings"][0]["code"] == "node_id_mismatch"

    def test_self_loop_is_reported(self, tmp_path, valid_pipeline):
        data = json.loads(Path(valid_pipeline).read_text())
        node_id = list(data["nodes"])[0]
        data["edges"] = [{"source": node_id, "target": node_id}]
        path = write_pipeline(tmp_path, data, name="selfloop.pipeline")

        code, out, _ = run_cli("validate", path)

        assert code == 1
        assert "self_loop" in [f["code"] for f in json.loads(out)["findings"]]

    def test_duplicate_edge_is_a_warning(self, tmp_path, valid_pipeline):
        data = json.loads(Path(valid_pipeline).read_text())
        data["edges"].append(dict(data["edges"][0]))
        path = write_pipeline(tmp_path, data, name="dupe.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True
        assert payload["summary"]["warnings"] == 1

    def test_legacy_document_validates(self, tmp_path):
        graph = PipelineGraph()
        a = graph.add_node(Node.create_data_loader())
        b = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(a, b)
        data = graph.to_dict()
        del data["version"]
        data["edges"] = [[a, b]]
        path = write_pipeline(tmp_path, data, name="legacy.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True
        assert payload["file_schema_version"] == 0

    def test_missing_file(self, tmp_path):
        code, out, err = run_cli("validate", str(tmp_path / "nope.pipeline"))
        payload = json.loads(out)

        assert code == 1
        assert_envelope(payload)
        assert payload["error"]["code"] == "file_not_found"
        assert "error:" in err

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "broken.pipeline"
        path.write_text("{not json")

        code, out, _ = run_cli("validate", str(path))

        assert code == 1
        assert json.loads(out)["error"]["code"] == "invalid_json"


class TestCodegen:
    def test_writes_code_to_stdout(self, valid_pipeline):
        code, out, err = run_cli("codegen", valid_pipeline)

        assert code == 0
        assert out.startswith("import numpy as np")
        assert "pd.read_csv(" in out
        compile(out, "<generated>", "exec")
        assert err == ""

    def test_json_envelope_carries_code(self, valid_pipeline):
        code, out, _ = run_cli("codegen", valid_pipeline, "--json")
        payload = json.loads(out)

        assert code == 0
        assert_envelope(payload)
        assert payload["status"] == "ok"
        assert "pd.read_csv(" in payload["code"]
        assert payload["output_path"] is None
        assert payload["line_count"] > 0

    def test_writes_output_file_and_json_receipt(self, valid_pipeline, tmp_path):
        target = tmp_path / "out.py"
        code, out, err = run_cli("codegen", valid_pipeline, "-o", str(target))
        payload = json.loads(out)

        assert code == 0
        assert payload["output_path"] == str(target)
        assert "pd.read_csv(" in target.read_text()
        assert str(target) in err

    def test_invalid_pipeline_fails(self, cyclic_pipeline):
        code, out, err = run_cli("codegen", cyclic_pipeline)
        payload = json.loads(out)

        assert code == 1
        assert payload["error"]["code"] == "codegen_failed"
        assert "cycle" in payload["error"]["message"].lower()
        assert "error:" in err

    def test_unwritable_output_path(self, valid_pipeline, tmp_path):
        target = tmp_path / "missing-dir" / "out.py"
        code, out, _ = run_cli("codegen", valid_pipeline, "-o", str(target))

        assert code == 1
        assert json.loads(out)["error"]["code"] == "write_failed"


class TestProcessBehavior:
    """The extension shells out, so check the real process contract too."""

    def test_module_entry_point_exit_codes(self, valid_pipeline, cyclic_pipeline):
        ok = subprocess.run(
            [sys.executable, "-m", "analysis_gui.cli", "validate", valid_pipeline],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
        )
        assert ok.returncode == 0
        assert json.loads(ok.stdout)["valid"] is True

        bad = subprocess.run(
            [sys.executable, "-m", "analysis_gui.cli", "validate", cyclic_pipeline],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
        )
        assert bad.returncode == 1
        assert json.loads(bad.stdout)["valid"] is False

    def test_usage_error_exits_two(self):
        result = subprocess.run(
            [sys.executable, "-m", "analysis_gui.cli", "not-a-command"],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 2


class TestRun:
    """Generate-and-execute. No model-call nodes: those would hit live APIs."""

    @staticmethod
    def write_csv(directory, name="data.csv"):
        path = Path(directory) / name
        path.write_text("a,b\n1,2\n3,4\n")
        return path

    @staticmethod
    def loader_pipeline(tmp_path, csv_name="data.csv", extra_nodes=None):
        TestRun.write_csv(tmp_path, csv_name)
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = csv_name
        loader_id = graph.add_node(loader)
        previous = loader_id
        extra_nodes = extra_nodes or []
        for node in extra_nodes:
            node_id = graph.add_node(node)
            graph.add_edge(previous, node_id)
            previous = node_id
        return write_pipeline(tmp_path, graph.to_dict(), name="run.pipeline")

    def test_runs_and_emits_json_receipt(self, tmp_path):
        path = self.loader_pipeline(tmp_path)
        code, out, err = run_cli("run", path)
        payload = json.loads(out)

        assert code == 0
        assert_envelope(payload)
        assert payload["status"] == "ok"
        assert payload["command"] == "run"
        assert payload["exit_code"] == 0
        assert payload["cwd"] == str(tmp_path.resolve())
        assert payload["saved_figures"] == []
        assert "Loaded data shape" in err
        assert "Running generated pipeline" in err

    def test_relative_csv_resolves_against_pipeline_directory(self, tmp_path):
        """cwd is the pipeline file's directory, not the process cwd."""
        path = self.loader_pipeline(tmp_path, csv_name="local.csv")
        code, out, err = run_cli("run", path)

        assert code == 0, err
        assert json.loads(out)["exit_code"] == 0

    def test_missing_csv_exits_nonzero(self, tmp_path):
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "does-not-exist.csv"
        graph.add_node(loader)
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, err = run_cli("run", path)
        payload = json.loads(out)

        assert code == 1
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "pipeline_failed"
        assert payload["exit_code"] != 0
        assert "error:" in err

    def test_invalid_pipeline_fails_before_executing(self, cyclic_pipeline):
        code, out, err = run_cli("run", cyclic_pipeline)
        payload = json.loads(out)

        assert code == 1
        assert payload["error"]["code"] == "codegen_failed"
        assert "cycle" in payload["error"]["message"].lower()
        assert "error:" in err

    def test_visualizer_saves_png_instead_of_blocking(self, tmp_path):
        viz = Node.create_visualizer()
        path = self.loader_pipeline(tmp_path, extra_nodes=[viz])

        code, out, err = run_cli("run", path)
        payload = json.loads(out)

        assert code == 0, err
        assert payload["saved_figures"]
        figure = Path(payload["saved_figures"][0])
        assert figure.exists()
        assert figure.suffix == ".png"
        assert figure.parent == tmp_path.resolve()
        assert "Saved figure to" in err

    def test_cwd_override(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        self.write_csv(data_dir, "data.csv")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "data.csv"
        graph.add_node(loader)
        path = write_pipeline(elsewhere, graph.to_dict(), name="p.pipeline")

        code, out, err = run_cli("run", path, "--cwd", str(data_dir))
        payload = json.loads(out)

        assert code == 0, err
        assert payload["cwd"] == str(data_dir.resolve())

    def test_module_entry_point(self, tmp_path):
        path = self.loader_pipeline(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "analysis_gui.cli", "run", path],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["command"] == "run"
        assert payload["exit_code"] == 0
