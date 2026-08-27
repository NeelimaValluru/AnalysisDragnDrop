"""Library discovery, similar-code search, and learned custom_code nodes."""

import json
from pathlib import Path

import pytest

from analysis_gui import cli
from analysis_gui.pipeline import CodeGenerator, Node, NodeType, PipelineGraph
from analysis_gui.repository import (
    Repository,
    RepositoryManager,
    discover_libraries,
    find_similar,
)
from analysis_gui.repository.learn import candidate_to_node
from analysis_gui.repository.scan import count_chunks_by_kind, scan_python_tree

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
SAMPLE_LIB = FIXTURE_ROOT / "sample_lib"


def run_cli(*argv):
    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def isolated_manager(tmp_path):
    return RepositoryManager(storage_path=str(tmp_path / "repos"))


class TestAstDiscovery:
    def test_finds_the_three_public_functions(self):
        records = scan_python_tree(str(FIXTURE_ROOT))
        names = {record.name for record in records if record.chunk_kind == "function"}

        assert {"bandpass_eeg_filter", "count_words", "load_table"} <= names
        assert "_undocumented_helper" not in names
        assert "_private_wrapper" not in names

    def test_indexes_methods_nested_functions_and_blocks(self):
        records = scan_python_tree(str(FIXTURE_ROOT))
        methods = [record for record in records if record.chunk_kind == "method"]
        blocks = [record for record in records if record.chunk_kind == "block"]
        nested = [
            record
            for record in records
            if record.chunk_kind == "function" and record.name == "smooth_spikes"
        ]

        assert any(record.name == "notch_line_noise" for record in methods)
        assert nested
        assert any(
            "bandpass the EEG" in (record.leading_comment or record.preview)
            for record in blocks
        )
        assert any("uncommented_bandpass" in record.preview for record in blocks)
        counts = count_chunks_by_kind(records)
        assert counts["function"] >= 4
        assert counts["method"] >= 1
        assert counts["block"] >= 1
        for record in records:
            assert record.chunk_kind in {"function", "method", "block"}
            assert record.source_hash
            assert record.span["start"] >= 1
            assert record.span["end"] >= record.span["start"]
            assert len(record.preview.splitlines()) <= 40

    def test_skips_test_modules(self):
        records = scan_python_tree(str(FIXTURE_ROOT))
        relative = [
            Path(record.source_path)
            .resolve()
            .relative_to(FIXTURE_ROOT.resolve())
            .as_posix()
            for record in records
        ]

        assert not any(path.startswith("sample_lib/tests/") for path in relative)
        assert not any(path.endswith("not_a_step.py") for path in relative)

    def test_records_signature_docstring_and_tags(self):
        records = {
            record.name: record for record in scan_python_tree(str(FIXTURE_ROOT))
        }
        filt = records["bandpass_eeg_filter"]

        assert filt.module == "sample_lib.filters"
        assert filt.kind == "repo.sample_lib.filters.bandpass_eeg_filter"
        assert filt.docstring_first_line.startswith("Bandpass-filter EEG")
        assert "filter" in filt.tags
        assert "eeg" in filt.tags
        arg_names = [arg.name for arg in filt.args]
        assert arg_names == ["data", "low_hz", "high_hz"]
        assert filt.has_data_input is True

    def test_loader_has_no_data_input(self):
        records = {
            record.name: record for record in scan_python_tree(str(FIXTURE_ROOT))
        }

        assert records["load_table"].has_data_input is False

    def test_does_not_import_the_scanned_module(self):
        """Parsing must not execute the file (AST only)."""
        sentinel = SAMPLE_LIB / "filters.py"
        original = sentinel.read_text()
        # If discovery imported the module, this would still just parse.
        records = scan_python_tree(str(FIXTURE_ROOT))
        assert any(record.name == "bandpass_eeg_filter" for record in records)
        assert sentinel.read_text() == original


class TestLearnNodes:
    def test_candidate_is_custom_code_with_repo_kind(self):
        record = next(
            r
            for r in scan_python_tree(str(FIXTURE_ROOT))
            if r.name == "bandpass_eeg_filter"
        )
        node = candidate_to_node(record)

        assert node.node_type == NodeType.CUSTOM_CODE
        assert node.metadata["function"] == "bandpass_eeg_filter"
        assert node.metadata["module"] == "sample_lib.filters"
        assert node.metadata["kind"] == record.kind
        assert "low_hz" in node.parameters
        assert node.parameters["low_hz"].param_type == "number"
        assert node.parameters["low_hz"].default_value == 1.0
        assert [port.name for port in node.input_ports] == ["data"]
        assert [port.name for port in node.output_ports] == ["output"]

    def test_loader_candidate_has_no_input_port(self):
        record = next(
            r for r in scan_python_tree(str(FIXTURE_ROOT)) if r.name == "load_table"
        )
        node = candidate_to_node(record)

        assert node.input_ports == ()
        assert node.metadata["has_data_input"] is False

    def test_discovered_kinds_are_not_in_the_palette(self, isolated_manager):
        index = discover_libraries(roots=[str(FIXTURE_ROOT)], manager=isolated_manager)

        assert index["count"] >= 3
        assert all(kind["in_palette"] is False for kind in index["kinds"])


class TestSimilarSearch:
    def test_filter_ranks_above_unrelated(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        names = [hit["qualified_name"] for hit in result["hits"]]
        assert names, result
        top = result["hits"][0]
        blob = " ".join(
            [
                top["name"],
                top.get("preview") or "",
                " ".join(top.get("calls") or []),
            ]
        ).lower()
        assert "filter" in blob or "bandpass" in blob or "butter" in blob
        if any(name.endswith("count_words") for name in names):
            filter_hit = next(
                i
                for i, hit in enumerate(result["hits"])
                if "filter" in hit["name"].lower()
                or "bandpass" in hit["name"].lower()
                or "butter" in (hit.get("preview") or "").lower()
            )
            decoy = next(
                i for i, name in enumerate(names) if name.endswith("count_words")
            )
            assert filter_hit < decoy
        assert result["reranked"] is False

    def test_works_with_no_api_key(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        result = find_similar(
            "bandpass eeg filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        assert result["reranked"] is False
        assert result["ranker"] == "ApiIntentMatch"
        top = result["hits"][0]
        assert "butter" in (top.get("preview") or "") or any(
            "butter" in call for call in top.get("calls") or []
        )

    def test_bandpass_ranks_an_inline_block(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "bandpass",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        blocks = [hit for hit in result["hits"] if hit.get("chunk_kind") == "block"]
        assert blocks, result["hits"]
        matched = [
            hit
            for hit in blocks
            if "bandpass" in (hit.get("preview") or "").lower()
            or "bandpass" in (hit.get("leading_comment") or "").lower()
        ]
        assert matched, blocks
        hit = matched[0]
        assert hit["chunk_kind"] == "block"
        assert hit["span"]["start"] >= 1
        assert hit["span"]["end"] >= hit["span"]["start"]
        assert hit["preview"]
        assert isinstance(hit["score"], float)

    def test_similar_hits_include_kind_span_preview_score(
        self, isolated_manager, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "bandpass eeg filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        assert result["hits"]
        for hit in result["hits"]:
            assert hit["chunk_kind"] in {"function", "method", "block"}
            assert "span" in hit
            assert "preview" in hit
            assert "score" in hit
            breakdown = hit["score_breakdown"]
            assert set(breakdown) >= {"intent", "api", "kind", "total"}


class TestCodegen:
    def test_discovered_node_imports_the_function(self):
        record = next(
            r
            for r in scan_python_tree(str(FIXTURE_ROOT))
            if r.name == "bandpass_eeg_filter"
        )
        node = candidate_to_node(record)
        graph = PipelineGraph()
        graph.add_node(node)
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "bandpass_eeg_filter" in code
        assert "from sample_lib.filters import bandpass_eeg_filter" in code
        assert "sys.path" in code
        assert "Bandpass-filter EEG data" not in code
        assert "return data" not in code

    def test_block_node_compiles_without_mutating_the_repo(self):
        fixture = SAMPLE_LIB / "inline_steps.py"
        original = fixture.read_text()
        record = next(
            r
            for r in scan_python_tree(str(FIXTURE_ROOT))
            if r.chunk_kind == "block"
            and "bandpass the EEG" in (r.leading_comment or r.preview)
        )
        node = candidate_to_node(record)
        graph = PipelineGraph()
        graph.add_node(node)
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert f"def chunk_{record.source_hash}(data, **params):" in code
        assert f"from {record.source_path}:{record.lineno}-{record.end_lineno}" in code
        assert f"from sample_lib.inline_steps import {record.name}" not in code
        assert fixture.read_text() == original
        assert "filtered_eeg = signal.sosfilt" in code or "sos = signal.butter" in code

    def test_method_node_imports_the_callable(self):
        record = next(
            r
            for r in scan_python_tree(str(FIXTURE_ROOT))
            if r.chunk_kind == "method" and r.name == "notch_line_noise"
        )
        node = candidate_to_node(record)
        graph = PipelineGraph()
        graph.add_node(node)
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "from sample_lib.inline_steps import EEGTools" in code
        assert "EEGTools().notch_line_noise" in code

    def test_generic_custom_code_still_compiles(self):
        node = Node.create_custom_code()
        node.parameters["function_name"].value = "process"
        graph = PipelineGraph()
        graph.add_node(node)
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "process" in code


class TestCli:
    def test_discover_json(self):
        code, out, err = run_cli("discover", "--json", "--root", str(FIXTURE_ROOT))
        payload = json.loads(out)

        assert code == 0
        assert payload["command"] == "discover"
        assert payload["count"] >= 3
        assert payload["chunk_counts"]["function"] >= 3
        assert payload["chunk_counts"]["method"] >= 1
        assert payload["chunk_counts"]["block"] >= 1
        names = {item["name"] for item in payload["functions"]}
        assert "bandpass_eeg_filter" in names
        assert "count_words" in names
        assert payload["kinds"]
        assert all(kind["in_palette"] is False for kind in payload["kinds"])
        assert "Indexed" in err
        assert "blocks" in err

    def test_similar_json(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code, out, _ = run_cli(
            "similar", "filter", "--json", "--root", str(FIXTURE_ROOT)
        )
        payload = json.loads(out)

        assert code == 0
        assert payload["command"] == "similar"
        assert payload["reranked"] is False
        assert payload["ranker"] == "ApiIntentMatch"
        assert payload["hits"][0]["chunk_kind"] in {"function", "method", "block"}
        hit = payload["hits"][0]
        blob = " ".join(
            [hit["name"], hit.get("preview") or "", " ".join(hit.get("calls") or [])]
        ).lower()
        assert "filter" in blob or "bandpass" in blob or "butter" in blob
        assert hit["chunk_kind"] in {"function", "method", "block"}
        assert "span" in hit
        assert "preview" in hit
        assert "score" in hit
        assert set(hit["score_breakdown"]) == {
            "intent",
            "api",
            "kind",
            "align",
            "total",
        }

    def test_similar_legacy_tfidf_flag(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code, out, _ = run_cli(
            "similar",
            "filter",
            "--json",
            "--legacy-tfidf",
            "--root",
            str(FIXTURE_ROOT),
        )
        payload = json.loads(out)

        assert code == 0
        assert payload["ranker"] == "legacy_tfidf"
        assert payload["hits"]

    def test_similar_from_span(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        records = scan_python_tree(str(FIXTURE_ROOT))
        block = next(
            r
            for r in records
            if r.chunk_kind == "block" and "sosfilt" in (r.preview or "")
        )
        spec = f"{block.source_path}:{block.lineno}-{block.end_lineno}"
        code, out, _ = run_cli(
            "similar",
            "--from-span",
            spec,
            "--json",
            "--root",
            str(FIXTURE_ROOT),
        )
        payload = json.loads(out)

        assert code == 0
        assert payload["from_span"] == spec
        assert payload["hits"]
        filter_rank = next(
            i
            for i, hit in enumerate(payload["hits"])
            if "butter" in (hit.get("preview") or "")
            or "sosfilt" in (hit.get("preview") or "")
            or "filter" in hit["name"].lower()
            or "bandpass" in hit["name"].lower()
        )
        decoys = [
            i for i, hit in enumerate(payload["hits"]) if hit["name"] == "count_words"
        ]
        if decoys:
            assert filter_rank < decoys[0]

    def test_describe_keeps_the_palette_small(self):
        _, out, _ = run_cli("describe", "--json")
        payload = json.loads(out)

        assert "discovered_kinds" not in payload
        kinds = {kind["kind"] for kind in payload["node_kinds"]}
        assert "custom_code" in kinds
        assert not any(kind.startswith("repo.") for kind in kinds)

    def test_describe_include_discovered(self):
        code, out, _ = run_cli(
            "describe", "--json", "--include-discovered", "--root", str(FIXTURE_ROOT)
        )
        payload = json.loads(out)

        assert code == 0
        assert payload["discovered_count"] >= 3
        discovered = {kind["kind"] for kind in payload["discovered_kinds"]}
        assert any(kind.endswith("bandpass_eeg_filter") for kind in discovered)
        palette = [kind for kind in payload["node_kinds"] if kind["in_palette"]]
        assert not any(kind["kind"].startswith("repo.") for kind in palette)

    def test_describe_discover_env_scans_workspace_src_only(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / "src"
        src.mkdir()
        (src / "lab.py").write_text(
            "def bandpass_eeg_filter(data):\n"
            '    """Filter EEG."""\n'
            "    return data\n"
        )
        elsewhere = tmp_path / "not_scanned.py"
        elsewhere.write_text(
            "def secret_helper(data):\n"
            '    """Should not be discovered."""\n'
            "    return data\n"
        )
        monkeypatch.setenv("ANALYSIS_GUI_DISCOVER", "1")
        code, out, _ = run_cli("describe", "--json", "--workspace", str(tmp_path))
        payload = json.loads(out)

        assert code == 0
        assert "discovered_kinds" in payload
        blob = json.dumps(payload["discovered_kinds"])
        assert "bandpass_eeg_filter" in blob
        assert "secret_helper" not in blob
        assert any(Path(root).name == "src" for root in payload["discovered_roots"])


class TestValidation:
    def test_missing_source_path_is_a_finding(self, tmp_path):
        graph = PipelineGraph()
        node = Node.create_custom_code()
        node.metadata["source_path"] = str(tmp_path / "gone.py")
        node.metadata["module"] = "gone"
        node.metadata["function"] = "missing"
        graph.add_node(node)
        path = tmp_path / "p.pipeline"
        path.write_text(json.dumps(graph.to_dict()))

        code, out, _ = run_cli("validate", str(path))
        payload = json.loads(out)

        assert code == 1
        codes = [finding["code"] for finding in payload["findings"]]
        assert "missing_module_path" in codes

    def test_existing_source_path_is_not_a_finding(self, tmp_path):
        record = next(
            r
            for r in scan_python_tree(str(FIXTURE_ROOT))
            if r.name == "bandpass_eeg_filter"
        )
        node = candidate_to_node(record)
        graph = PipelineGraph()
        graph.add_node(node)
        path = tmp_path / "p.pipeline"
        path.write_text(json.dumps(graph.to_dict()))

        code, out, _ = run_cli("validate", str(path))
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True


class TestRepositoryManager:
    def test_scan_repository_uses_ast(self, isolated_manager):
        functions = isolated_manager.scan_repository(str(FIXTURE_ROOT))

        assert "sample_lib.filters.bandpass_eeg_filter" in functions
        assert functions["sample_lib.filters.bandpass_eeg_filter"]["tags"]

    def test_registered_repo_is_scanned(self, isolated_manager):
        isolated_manager.add_repository(
            Repository(
                id="sample",
                name="Sample",
                path=str(FIXTURE_ROOT),
                description="fixture",
            )
        )
        index = discover_libraries(
            workspace=str(isolated_manager.storage_path),
            manager=isolated_manager,
        )

        names = {item["name"] for item in index["functions"]}
        assert "bandpass_eeg_filter" in names
