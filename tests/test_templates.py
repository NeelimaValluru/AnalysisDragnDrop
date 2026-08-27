"""Shipped ``templates/*.pipeline`` files validate and codegen-compile."""

import json
from pathlib import Path

from analysis_gui import cli
from analysis_gui.pipeline import CodeGenerator, PipelineGraph

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "templates"
TINY = Path(__file__).resolve().parent / "fixtures" / "tiny_table.csv"

TEMPLATE_NAMES = (
    "eeg_psd.pipeline",
    "si_spike_sort.pipeline",
    "csv_cluster.pipeline",
)


def run_cli(*argv):
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


class TestTemplates:
    def test_each_template_validates_and_compiles(self):
        found = [TEMPLATES / name for name in TEMPLATE_NAMES]
        assert all(path.is_file() for path in found), found
        for path in found:
            code, out, err = run_cli("validate", str(path))
            payload = json.loads(out)
            assert payload["valid"] is True, (path.name, payload.get("findings"), err)
            assert code == 0
            graph = PipelineGraph.from_file(str(path))
            generated = CodeGenerator(graph).generate()
            compile(generated, str(path), "exec")

    def test_csv_cluster_runs_on_tiny_fixture(self, tmp_path):
        if not TINY.is_file():
            return
        dest = tmp_path / "data.csv"
        dest.write_text(TINY.read_text())
        source = json.loads((TEMPLATES / "csv_cluster.pipeline").read_text())
        pipeline = tmp_path / "csv_cluster.pipeline"
        pipeline.write_text(json.dumps(source))
        code, out, err = run_cli("run", str(pipeline))
        payload = json.loads(out)
        assert code == 0, err
        assert payload["exit_code"] == 0
        assert (tmp_path / "csv_cluster.run.json").is_file()
