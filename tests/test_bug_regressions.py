"""Regression tests for the three confirmed bugs (change 1).

1. ``QDrag`` was referenced in ``ui/main_window.py`` without being imported.
2. ``_generate_data_loader`` called ``.default_value`` on a plain string when
   the ``delimiter`` parameter was missing.
3. ``pandas`` was missing from the declared dependencies.
"""

import ast
import re
from pathlib import Path

from analysis_gui.pipeline import CodeGenerator, Node, PipelineGraph

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = REPO_ROOT / "src" / "analysis_gui" / "ui"


def _loader_pipeline(node):
    graph = PipelineGraph()
    graph.add_node(node)
    return graph


class TestQtNamesAreImported:
    """Bug 1: a Qt class was used in the UI without importing it."""

    @staticmethod
    def _undefined_qt_names(source: str):
        """Return Qt-looking global names used by a module but never bound."""
        tree = ast.parse(source)

        bound = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set()
        used = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    bound.add(node.id)
                elif re.match(r"^Q[A-Z]", node.id):
                    used.add(node.id)

        return used - bound

    def test_ui_modules_do_not_reference_unimported_qt_classes(self):
        modules = sorted(UI_DIR.rglob("*.py"))
        assert modules, "expected to find UI modules to scan"

        for module in modules:
            undefined = self._undefined_qt_names(module.read_text())
            assert not undefined, f"{module.name} uses unimported Qt names: {undefined}"

    def test_main_window_no_longer_references_qdrag(self):
        source = (UI_DIR / "main_window.py").read_text()
        assert "QDrag" not in source


class TestDataLoaderDelimiter:
    """Bug 2: a missing parameter fell through to a raw string default."""

    def test_generates_without_delimiter_parameter(self):
        """Used to raise AttributeError: str has no attribute default_value."""
        node = Node.create_data_loader("csv")
        del node.parameters["delimiter"]

        code = CodeGenerator(_loader_pipeline(node)).generate()

        assert "pd.read_csv('data.csv', delimiter=',')" in code
        compile(code, "<generated>", "exec")

    def test_generates_without_any_parameters(self):
        node = Node.create_data_loader("csv")
        node.parameters.clear()

        code = CodeGenerator(_loader_pipeline(node)).generate()

        assert "pd.read_csv('data.csv', delimiter=',')" in code
        compile(code, "<generated>", "exec")

    def test_non_csv_loader_omits_delimiter(self):
        node = Node.create_data_loader("json")
        del node.parameters["delimiter"]

        code = CodeGenerator(_loader_pipeline(node)).generate()

        assert "delimiter=" not in code

    def test_delimiter_override_is_used(self):
        node = Node.create_data_loader("csv")
        node.parameters["delimiter"].value = ";"

        code = CodeGenerator(_loader_pipeline(node)).generate()

        assert "delimiter=';'" in code

    def test_generated_loader_code_is_syntactically_valid(self):
        """The delimiter used to be appended after the closing paren."""
        node = Node.create_data_loader("csv")
        node.parameters["file_path"].value = "sales.csv"

        code = CodeGenerator(_loader_pipeline(node)).generate()

        compile(code, "<generated>", "exec")
        assert "pd.read_csv('sales.csv', delimiter=',')" in code


class TestDeclaredDependencies:
    """Bug 3: generated code imports pandas, so pandas must be declared."""

    def test_pyproject_declares_pandas(self):
        assert "pandas>=1.1.0" in (REPO_ROOT / "pyproject.toml").read_text()

    def test_setup_py_declares_pandas(self):
        assert "pandas>=1.1.0" in (REPO_ROOT / "setup.py").read_text()

    def test_console_scripts_include_headless_cli(self):
        assert (
            'analysis-gui-cli = "analysis_gui.cli:main"'
            in (REPO_ROOT / "pyproject.toml").read_text()
        )
        assert (
            "analysis-gui-cli=analysis_gui.cli:main"
            in (REPO_ROOT / "setup.py").read_text()
        )
