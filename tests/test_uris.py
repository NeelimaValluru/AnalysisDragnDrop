"""URI resolution and NWB/BIDS path heuristics."""

from pathlib import Path

import pytest

from analysis_gui.pipeline import CodeGenerator, Node, PipelineGraph
from analysis_gui.utils.uris import (
    is_data_uri,
    looks_like_bids,
    looks_like_nwb,
    resolve_data_uri,
    sha256_file,
)
from analysis_gui.neural.io import load_neural
from analysis_gui.utils.data_loader import load_csv


def test_file_uri_and_local_path(tmp_path):
    target = tmp_path / "table.csv"
    target.write_text("a,b\n1,2\n")
    assert resolve_data_uri(str(target)) == str(target)
    uri = target.as_uri()
    assert is_data_uri(uri)
    assert Path(resolve_data_uri(uri)).resolve() == target.resolve()
    assert sha256_file(str(target))


def test_looks_like_nwb_and_bids(tmp_path):
    assert looks_like_nwb("session.nwb")
    assert looks_like_nwb(str(tmp_path / "rec.NWB".lower()) if False else "rec.nwb")
    assert not looks_like_bids(str(tmp_path))
    (tmp_path / "dataset_description.json").write_text("{}")
    assert looks_like_bids(str(tmp_path))


def test_csv_codegen_wraps_uri():
    node = Node.create_data_loader("csv")
    node.parameters["file_path"].value = "https://example.invalid/data.csv"
    graph = PipelineGraph()
    graph.add_node(node)
    code = CodeGenerator(graph).generate()
    compile(code, "<generated>", "exec")
    assert "resolve_data_uri(" in code
    assert "pd.read_csv(" in code


def test_csv_codegen_keeps_plain_paths():
    node = Node.create_data_loader("csv")
    node.parameters["file_path"].value = "data.csv"
    graph = PipelineGraph()
    graph.add_node(node)
    code = CodeGenerator(graph).generate()
    assert "pd.read_csv('data.csv', delimiter=',')" in code
    assert "resolve_data_uri" not in code


def test_load_csv_via_file_uri(tmp_path):
    path = tmp_path / "tiny.csv"
    path.write_text("x,y\n1,2\n")
    frame = load_csv(path.as_uri())
    assert list(frame.columns) == ["x", "y"]
    assert len(frame) == 1


def test_s3_requires_optional_extra():
    try:
        import boto3  # noqa: F401
    except ImportError:
        with pytest.raises(Exception, match="boto3"):
            resolve_data_uri("s3://bucket/key.csv")
        return
    pytest.skip("boto3 is installed")


def test_load_neural_resolves_file_uri(tmp_path):
    path = tmp_path / "eeg.csv"
    path.write_text("ch0,ch1\n0.1,0.2\n0.3,0.4\n")
    frame = load_neural(path.as_uri(), signal_type="eeg", file_format="csv")
    assert frame.shape == (2, 2)
    assert frame.attrs["signal_type"] == "eeg"
