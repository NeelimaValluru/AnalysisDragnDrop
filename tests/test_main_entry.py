"""Tests for the ``analysis-gui`` desktop entry point.

The entry point takes an optional pipeline file so an editor extension can
launch the desktop app on the file the user right-clicked. Its argument
handling has to survive on a machine with no display and no Qt, so the paths
that must not need PyQt6 (``--help``, ``--version``, a missing file) are
exercised in a subprocess with PyQt6 blocked, following the pattern in
``test_headless.py``.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from analysis_gui import __version__, main as main_module
from analysis_gui.pipeline import SCHEMA_VERSION, Node, PipelineGraph

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

#: Runs ``analysis_gui.main.main()`` with PyQt6 made unimportable, so a
#: subprocess exit code reflects what a user on a headless box would see.
PROBE = """
import importlib.abc
import sys


class BlockPyQt6(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PyQt6" or fullname.startswith("PyQt6."):
            raise ImportError("PyQt6 is unavailable in this environment")
        return None


sys.meta_path.insert(0, BlockPyQt6())

from analysis_gui.main import main

sys.exit(main())
"""


def run_without_pyqt6(*argv):
    """Run the entry point in a subprocess where PyQt6 cannot be imported."""
    return subprocess.run(
        [sys.executable, "-c", PROBE, *argv],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
    )


def fake_run_gui(store):
    """A stand-in for run_gui that records the file it was asked to open."""

    def run_gui(pipeline_path=None, stderr=None):
        store["path"] = pipeline_path
        return 0

    return run_gui


@pytest.fixture
def valid_pipeline(tmp_path):
    graph = PipelineGraph()
    loader = graph.add_node(Node.create_data_loader("csv"))
    normalize = graph.add_node(Node.create_preprocessor("normalize"))
    graph.add_edge(loader, normalize)

    path = tmp_path / "good.pipeline"
    path.write_text(json.dumps(graph.to_dict()))
    return path


class TestArgumentParsing:
    def test_no_argument_means_no_file(self):
        args = main_module.build_parser().parse_args([])
        assert args.pipeline is None

    def test_positional_file_is_accepted(self):
        args = main_module.build_parser().parse_args(["/tmp/x.pipeline"])
        assert args.pipeline == "/tmp/x.pipeline"

    def test_unknown_flag_is_a_usage_error(self):
        with pytest.raises(SystemExit) as excinfo:
            main_module.build_parser().parse_args(["--nope"])
        assert excinfo.value.code == 2

    def test_second_positional_is_a_usage_error(self):
        with pytest.raises(SystemExit) as excinfo:
            main_module.build_parser().parse_args(["a.pipeline", "b.pipeline"])
        assert excinfo.value.code == 2


class TestFileCheck:
    def test_valid_file_passes(self, valid_pipeline):
        assert main_module.check_pipeline_file(str(valid_pipeline)) is None

    def test_missing_file_is_reported(self, tmp_path):
        problem = main_module.check_pipeline_file(str(tmp_path / "nope.pipeline"))
        assert problem is not None
        assert "No such pipeline file" in problem

    def test_directory_is_reported(self, tmp_path):
        problem = main_module.check_pipeline_file(str(tmp_path))
        assert problem is not None
        assert "Not a file" in problem

    def test_unreadable_file_is_reported(self, tmp_path, valid_pipeline):
        valid_pipeline.chmod(0o000)
        try:
            problem = main_module.check_pipeline_file(str(valid_pipeline))
        finally:
            valid_pipeline.chmod(0o644)

        if problem is None:  # running as root, where the chmod means nothing
            pytest.skip("filesystem permissions are not enforced for this user")
        assert "Could not read" in problem

    def test_malformed_json_is_reported(self, tmp_path):
        path = tmp_path / "broken.pipeline"
        path.write_text("{not json")

        problem = main_module.check_pipeline_file(str(path))

        assert problem is not None
        assert "not a valid .pipeline file" in problem

    def test_json_that_is_not_an_object_is_reported(self, tmp_path):
        path = tmp_path / "list.pipeline"
        path.write_text("[1, 2, 3]")

        problem = main_module.check_pipeline_file(str(path))

        assert problem is not None
        assert "must be a JSON object" in problem

    def test_structurally_broken_document_is_reported(self, tmp_path, valid_pipeline):
        data = json.loads(valid_pipeline.read_text())
        node_id = list(data["nodes"])[0]
        data["nodes"][node_id]["node_type"] = "not-a-real-type"
        path = tmp_path / "bad-type.pipeline"
        path.write_text(json.dumps(data))

        problem = main_module.check_pipeline_file(str(path))

        assert problem is not None
        assert "not a valid .pipeline file" in problem

    def test_empty_pipeline_is_openable(self, tmp_path):
        """An empty pipeline is unrunnable, not unopenable."""
        path = tmp_path / "empty.pipeline"
        path.write_text(json.dumps({"version": SCHEMA_VERSION, "nodes": {}}))

        assert main_module.check_pipeline_file(str(path)) is None

    def test_user_home_is_expanded(self, monkeypatch, tmp_path, valid_pipeline):
        monkeypatch.setenv("HOME", str(tmp_path))
        launched = {}
        monkeypatch.setattr(main_module, "run_gui", fake_run_gui(launched))

        assert main_module.main([f"~/{valid_pipeline.name}"]) == 0
        assert launched["path"] == str(valid_pipeline)


class TestLaunchWiring:
    """The GUI cannot run here, so check what main() hands to it."""

    def test_no_argument_launches_with_no_file(self, monkeypatch):
        launched = {}
        monkeypatch.setattr(main_module, "run_gui", fake_run_gui(launched))

        assert main_module.main([]) == 0
        assert launched["path"] is None

    def test_valid_file_is_passed_to_the_gui(self, monkeypatch, valid_pipeline):
        launched = {}
        monkeypatch.setattr(main_module, "run_gui", fake_run_gui(launched))

        assert main_module.main([str(valid_pipeline)]) == 0
        assert launched["path"] == str(valid_pipeline)

    def test_bad_file_never_reaches_the_gui(self, monkeypatch, tmp_path, capsys):
        def explode(*args, **kwargs):
            raise AssertionError("run_gui must not be called for a bad file")

        monkeypatch.setattr(main_module, "run_gui", explode)

        assert main_module.main([str(tmp_path / "nope.pipeline")]) == 1
        assert "error: No such pipeline file" in capsys.readouterr().err


class TestWithoutPyQt6:
    """These paths run before Qt is imported and must work headless."""

    def test_help_needs_no_display(self):
        result = run_without_pyqt6("--help")

        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("usage: analysis-gui")
        assert "PIPELINE" in result.stdout
        assert result.stderr == ""

    def test_version_needs_no_display(self):
        result = run_without_pyqt6("--version")

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (
            f"analysis-gui {__version__} (schema v{SCHEMA_VERSION})"
        )

    def test_missing_file_exits_one_with_a_message(self, tmp_path):
        result = run_without_pyqt6(str(tmp_path / "nope.pipeline"))

        assert result.returncode == 1
        assert result.stdout == ""
        assert "No such pipeline file" in result.stderr
        assert "Traceback" not in result.stderr

    def test_malformed_file_exits_one_with_a_message(self, tmp_path):
        path = tmp_path / "broken.pipeline"
        path.write_text("{not json")

        result = run_without_pyqt6(str(path))

        assert result.returncode == 1
        assert "not a valid .pipeline file" in result.stderr
        assert "Traceback" not in result.stderr

    def test_usage_error_exits_two(self):
        result = run_without_pyqt6("--definitely-not-a-flag")

        assert result.returncode == 2
        assert "usage: analysis-gui" in result.stderr

    def test_valid_file_gets_past_the_check_and_fails_on_qt(self, valid_pipeline):
        """A readable pipeline reaches the GUI import, which is what is missing."""
        result = run_without_pyqt6(str(valid_pipeline))

        assert result.returncode == 1
        assert "the desktop GUI is unavailable" in result.stderr
        assert "PyQt6" in result.stderr
        assert "Traceback" not in result.stderr


class TestModuleStaysImportableHeadless:
    def test_main_does_not_import_pyqt6_at_module_scope(self):
        tree = ast.parse((SRC_DIR / "analysis_gui" / "main.py").read_text())
        imported = set()

        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = "." * node.level + (node.module or "")
                imported.add(base)
                imported.update(f"{base}.{alias.name}" for alias in node.names)

        banned = {".ui", "PyQt6"}
        for name in imported:
            assert not any(
                name == bad or name.startswith(f"{bad}.") for bad in banned
            ), f"main.py must not import {name} at module scope"
