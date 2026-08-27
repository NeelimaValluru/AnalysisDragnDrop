"""Run receipts, environment pins, and ``*.run.json`` files."""

import hashlib
import io
import json
from pathlib import Path

from analysis_gui import cli
from analysis_gui.pipeline import Node, PipelineGraph
from analysis_gui.pipeline.receipt import (
    RECEIPT_SCHEMA_VERSION,
    canonical_json_hash,
    compare_environment,
    snapshot_environment,
)


def run_cli(*argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def write_pipeline(tmp_path, data, name="p.pipeline"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


class TestRunReceipt:
    def test_writes_run_json_beside_the_pipeline(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("a,b\n1,2\n3,4\n")
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "data.csv"
        graph.add_node(loader)
        path = write_pipeline(tmp_path, graph.to_dict(), name="demo.pipeline")

        code, out, err = run_cli("run", path)
        payload = json.loads(out)
        receipt_file = tmp_path / "demo.run.json"

        assert code == 0, err
        assert receipt_file.is_file()
        on_disk = json.loads(receipt_file.read_text())
        assert payload["receipt_schema_version"] == RECEIPT_SCHEMA_VERSION
        assert len(payload["graph_hash"]) == 64
        assert payload["graph_hash"] == canonical_json_hash(
            json.loads(Path(path).read_text())
        )
        assert len(payload["generated_code_hash"]) == 64
        assert payload["started_at"]
        assert payload["finished_at"]
        assert payload["interpreter_version"]
        assert payload["environment"]["analysis_gui"]
        assert payload["environment"]["python"]
        assert "spike" in payload["environment"]["extras"]
        hashed = payload["input_files"]
        assert hashed
        assert hashed[0]["uri"] == "data.csv"
        assert hashed[0]["sha256"] == hashlib.sha256(csv.read_bytes()).hexdigest()
        assert "git_commit" in payload
        assert payload["model_summaries"] == []
        assert receipt_file.as_posix() in payload["output_paths"]
        assert on_disk["generated_code_hash"] == payload["generated_code_hash"]

    def test_receipt_flag_overrides_path(self, tmp_path):
        csv = tmp_path / "data.csv"
        csv.write_text("a,b\n1,2\n")
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "data.csv"
        graph.add_node(loader)
        path = write_pipeline(tmp_path, graph.to_dict())
        custom = tmp_path / "custom-receipt.json"

        code, out, err = run_cli("run", path, "--receipt", str(custom))
        payload = json.loads(out)

        assert code == 0, err
        assert custom.is_file()
        assert payload["receipt_path"] == str(custom.resolve())


class TestPinnedEnvironment:
    def test_mismatch_warns_by_default(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "data.csv"
        graph.add_node(loader)
        graph.environment = {"python": "2.7", "analysis_gui": "9.9.9"}
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, err = run_cli("run", path)
        payload = json.loads(out)

        assert code == 0, err
        assert payload["environment_strict"] is False
        assert payload["environment_warnings"]
        assert "warning: environment:" in err

    def test_strict_env_fails(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b\n1,2\n")
        graph = PipelineGraph()
        loader = Node.create_data_loader("csv")
        loader.parameters["file_path"].value = "data.csv"
        graph.add_node(loader)
        graph.environment = {"python": "2.7"}
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, err = run_cli("run", path, "--strict-env")
        payload = json.loads(out)

        assert code == 1
        assert payload["error"]["code"] == "environment_mismatch"
        assert "python" in payload["error"]["message"]

    def test_requires_alias_and_absent_is_compatible(self, tmp_path):
        data = {
            "version": 1,
            "requires": {"python": "2.7", "strict": True},
            "nodes": {},
            "edges": [],
        }
        graph = PipelineGraph.from_dict(data)
        assert graph.environment["python"] == "2.7"
        empty = PipelineGraph.from_dict({"nodes": {}, "edges": []})
        assert empty.environment == {}

    def test_compare_environment_matches_prefix(self):
        actual = snapshot_environment()
        assert compare_environment({}, actual) == []
        assert compare_environment({"python": actual["python"]}, actual) == []
        major_minor = ".".join(actual["python"].split(".")[:2])
        assert compare_environment({"python": major_minor}, actual) == []
