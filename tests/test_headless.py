"""Guard the headless property of the engine.

The VS Code extension shells out to Python over SSH, in Codespaces and in web
VS Code, where PyQt6 is absent and there is no display. A single stray
``from .ui import ...`` in the pipeline package or the CLI would break every
remote user, so this runs the imports in a subprocess with PyQt6 blocked.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

PROBE = """
import importlib.abc
import json
import sys


class BlockOptionalDeps(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            "PyQt6",
            "anthropic",
            "openai",
            "tensorflow",
            "mne",
            "neo",
            "spikeinterface",
        )
        if fullname in blocked or any(fullname.startswith(name + ".") for name in blocked):
            raise ImportError(f"{fullname} is unavailable in this environment")
        return None


sys.meta_path.insert(0, BlockOptionalDeps())

import analysis_gui
import analysis_gui.cli
import analysis_gui.pipeline
import analysis_gui.neural
import analysis_gui.neural.spikeinterface_nodes
import analysis_gui.repository
from analysis_gui.pipeline import CodeGenerator, Node, PipelineGraph
from analysis_gui.repository.scan import scan_python_tree

graph = PipelineGraph()
loader = graph.add_node(Node.create_data_loader("csv"))
normalize = graph.add_node(Node.create_preprocessor("normalize"))
graph.add_edge(loader, normalize)
claude = graph.add_node(Node.create_model_call("claude"))
code = CodeGenerator(graph).generate()
eeg = Node.create_neural_loader("eeg")
filter_node = Node.create_preprocessor("neural_filter")
scanned = scan_python_tree("/nonexistent-analysis-gui-library")

print(json.dumps({
    "leaked": sorted(
        name for name in sys.modules
        if name.startswith("PyQt6")
        or name.startswith("analysis_gui.ui")
        or name == "anthropic"
        or name.startswith("anthropic.")
        or name == "openai"
        or name.startswith("openai.")
        or name.startswith("analysis_gui.models")
        or name == "tensorflow"
        or name.startswith("tensorflow.")
        or name == "mne"
        or name.startswith("mne.")
        or name == "spikeinterface"
        or name.startswith("spikeinterface.")
        or name == "neo"
        or name.startswith("neo.")
    ),
    "generated": bool(code),
    "calls_complete": "from analysis_gui.models import complete" in code,
    "neural_kind": eeg.metadata.get("signal_type"),
    "filter_label": filter_node.label,
    "scanned": scanned,
}))
"""


def _run_probe():
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
    )
    return result


def test_engine_imports_and_runs_without_pyqt6():
    result = _run_probe()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["leaked"] == []
    assert payload["generated"] is True
    assert payload["calls_complete"] is True
    assert payload["neural_kind"] == "eeg"
    assert payload["filter_label"] == "Neural Filter"
    assert payload["scanned"] == []


def test_cli_module_does_not_import_the_ui():
    tree = ast.parse((SRC_DIR / "analysis_gui" / "cli.py").read_text())
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)

    banned = {".ui", ".main", "PyQt6"}
    for name in imported:
        assert not any(
            name == bad or name.startswith(f"{bad}.") for bad in banned
        ), f"cli.py must not import {name}"


def test_pipeline_package_only_imports_stdlib_and_siblings():
    pipeline_dir = SRC_DIR / "analysis_gui" / "pipeline"
    forbidden = (
        "PyQt6",
        "pandas",
        "numpy",
        "tensorflow",
        "sklearn",
        "matplotlib",
        "anthropic",
        "openai",
        "analysis_gui.models",
        "analysis_gui.neural",
        "mne",
        "neo",
        "spikeinterface",
    )

    for module in sorted(pipeline_dir.glob("*.py")):
        for line in module.read_text().splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            assert not any(
                name in stripped for name in forbidden
            ), f"{module.name} imports a heavy dependency: {stripped}"
