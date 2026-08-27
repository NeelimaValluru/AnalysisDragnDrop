"""ApiIntentMatch: dual-view retrieval over intent, API n-grams, and data kind."""

from pathlib import Path

import pytest

from analysis_gui.repository import RepositoryManager, discover_libraries, find_similar
from analysis_gui.repository.matching import (
    RANKER_NAME,
    ApiIntentIndex,
    canonicalize_call,
    infer_data_kinds,
)
from analysis_gui.repository.scan import DiscoveredFunction
from analysis_gui.repository.similar import rank_query, run_similar

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def isolated_manager(tmp_path):
    return RepositoryManager(storage_path=str(tmp_path / "repos"))


def _chunk(
    i,
    *,
    name="fn",
    docstring="",
    calls=None,
    tags=None,
    preview="",
    leading_comment="",
    chunk_kind="function",
    module="mod",
):
    calls = list(calls or [])
    tags = list(tags or [])
    return DiscoveredFunction(
        name=name,
        qualified_name=(
            f"{module}.{name}_{i}" if chunk_kind != "block" else f"{module}:{i}-{i + 4}"
        ),
        module=module,
        source_path=f"/tmp/synthetic_{module}.py",
        library_root="/tmp",
        lineno=max(i, 1),
        end_lineno=max(i, 1) + 4,
        docstring=docstring,
        docstring_first_line=docstring.splitlines()[0] if docstring else "",
        tags=tags,
        chunk_kind=chunk_kind,
        preview=preview,
        source_hash=f"{i:012x}"[:12],
        leading_comment=leading_comment,
        calls=calls,
    )


class TestApiIntentMatchNovelty:
    def test_unnamed_butter_block_ranks_first_for_bandpass_eeg_filter(
        self, isolated_manager, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "bandpass eeg filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        assert result["hits"]
        top = result["hits"][0]
        preview = (top.get("preview") or "").lower()
        calls = " ".join(top.get("calls") or [])
        assert top["chunk_kind"] == "block"
        assert "butter" in preview or "butter" in calls
        assert "sosfilt" in preview or "sosfilt" in calls

    def test_decoy_named_bandpass_ranks_below_butter_block(
        self, isolated_manager, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "bandpass eeg filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        names = [hit["name"] for hit in result["hits"]]
        butter = next(
            hit
            for hit in result["hits"]
            if hit["chunk_kind"] == "block"
            and (
                "sosfilt" in (hit.get("preview") or "")
                or any("sosfilt" in c for c in hit.get("calls") or [])
            )
        )
        decoy = next(
            hit for hit in result["hits"] if hit["name"] == "bandpass_eeg_filter"
        )
        butter_rank = result["hits"].index(butter)
        decoy_rank = result["hits"].index(decoy)
        assert butter_rank < decoy_rank, names[:8]
        assert butter["score_breakdown"]["api"] > decoy["score_breakdown"]["api"]

    def test_spike_psth_preferred_over_eeg_filter(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "spike psth",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        assert result["hits"]
        top = result["hits"][0]
        blob = " ".join(
            [
                top["name"],
                top.get("description") or "",
                " ".join(top.get("tags") or []),
                top.get("preview") or "",
            ]
        ).lower()
        assert "psth" in blob or "spike" in blob
        assert "bandpass_eeg_filter" not in top["qualified_name"]
        eeg = [
            hit
            for hit in result["hits"]
            if hit["name"] == "bandpass_eeg_filter"
            or "butter" in (hit.get("preview") or "")
        ]
        if eeg:
            assert result["hits"].index(top) < result["hits"].index(eeg[0])

    def test_score_breakdown_keys_present(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = find_similar(
            "bandpass eeg filter",
            roots=[str(FIXTURE_ROOT)],
            manager=isolated_manager,
        )

        assert result["ranker"] == RANKER_NAME
        for hit in result["hits"]:
            breakdown = hit["score_breakdown"]
            assert set(breakdown) == {"intent", "api", "kind", "align", "total"}
            assert abs(breakdown["total"] - hit["score"]) < 1e-6
            for key in ("intent", "api", "kind", "align", "total"):
                assert isinstance(breakdown[key], float)


class TestCandidateGeneration:
    def test_selective_query_examines_far_fewer_than_corpus(self):
        records = []
        for i in range(3000):
            records.append(
                _chunk(
                    i,
                    name=f"unit_{i}",
                    docstring=f"compute metric alpha_{i} on series",
                    calls=[f"lib.metric_{i}"],
                )
            )
        records.append(
            _chunk(
                9001,
                name="chunk_deadbeef12",
                docstring="",
                leading_comment="bandpass the EEG",
                calls=["signal.butter", "signal.sosfilt"],
                tags=["bandpass", "eeg"],
                chunk_kind="block",
                preview=(
                    "# bandpass the EEG\n"
                    "sos = signal.butter(4, [1, 40], btype='bandpass', fs=250, output='sos')\n"
                    "filtered_eeg = signal.sosfilt(sos, raw_eeg)\n"
                ),
            )
        )
        index = ApiIntentIndex.build(records)
        result = index.query("bandpass eeg filter")
        n = len(records)
        assert result.stats.corpus_size == n
        assert result.stats.used_fallback is False
        assert result.stats.candidates_examined < 0.20 * n
        assert result.stats.candidates_examined < n
        assert result.stats.alignments_scored == result.stats.candidates_examined
        assert result.stats.alignments_scored < n
        assert result.hits
        top = result.hits[0].record
        assert top.chunk_kind == "block"
        assert "sosfilt" in top.preview

    def test_empty_postings_fall_back_to_legacy_scan(self):
        records = [
            _chunk(1, name="count_words", docstring="Count words in prose", calls=[]),
            _chunk(
                2,
                name="load_table",
                docstring="Load a CSV table",
                calls=["pd.read_csv"],
            ),
        ]
        result = rank_query("zzzznotaterm_in_corpus", records)
        assert result.stats.used_fallback is True
        assert result.stats.candidates_examined == len(records)


class TestCanonicalizationAndKinds:
    def test_numpy_mean_canonicalizes_to_np_mean(self):
        from analysis_gui.repository.matching import _FileAliases

        assert canonicalize_call("numpy.mean", _FileAliases()) == "np.mean"
        assert canonicalize_call("np.mean", _FileAliases()) == "np.mean"
        assert (
            canonicalize_call("scipy.signal.butter", _FileAliases()) == "signal.butter"
        )
        assert canonicalize_call("scipy.signal", _FileAliases()) == "signal"

        from analysis_gui.repository.matching import _aliases_from_source

        aliases = _aliases_from_source("from scipy import signal\n")
        assert canonicalize_call("signal.butter", aliases) == "signal.butter"
        aliases = _aliases_from_source("from scipy.signal import butter\n")
        assert canonicalize_call("butter", aliases) == "signal.butter"

    def test_infer_data_kinds_from_tokens(self):
        assert "eeg" in infer_data_kinds("bandpass eeg filter")
        assert "spike" in infer_data_kinds("spike psth")
        assert "table" in infer_data_kinds("load a csv table")

    def test_run_similar_json_shape(self, isolated_manager, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        index = discover_libraries(roots=[str(FIXTURE_ROOT)], manager=isolated_manager)
        payload = run_similar(
            "filter", index["records"], match_index=index["match_index"]
        )
        assert payload["ranker"] == RANKER_NAME
        assert payload["hits"]
        hit = payload["hits"][0]
        assert "score_breakdown" in hit
        assert hit["chunk_kind"] in {"function", "method", "block"}
        assert "span" in hit
        assert "preview" in hit
        assert "align" in hit["score_breakdown"]


class TestSequenceAlignment:
    def test_butter_sosfilt_plot_outranks_butter_only(self):
        records = [
            _chunk(
                1,
                name="seq_filter_plot",
                preview=(
                    "sos = signal.butter(4, [1, 40], btype='bandpass', "
                    "fs=250, output='sos')\n"
                    "filtered = signal.sosfilt(sos, raw)\n"
                    "plt.plot(filtered)\n"
                ),
                calls=["signal.butter", "signal.sosfilt", "plt.plot"],
            ),
            _chunk(
                2,
                name="butter_only",
                preview="sos = signal.butter(4, 10, output='sos')\n",
                calls=["signal.butter"],
            ),
            _chunk(
                3,
                name="identity",
                docstring="return x unchanged",
                preview="return x\n",
                calls=[],
            ),
        ]
        index = ApiIntentIndex.build(records)
        result = index.query("bandpass then plot")
        names = [hit.record.name for hit in result.hits]
        assert "seq_filter_plot" in names
        assert "butter_only" in names
        seq = next(hit for hit in result.hits if hit.record.name == "seq_filter_plot")
        decoy = next(hit for hit in result.hits if hit.record.name == "butter_only")
        assert names.index("seq_filter_plot") < names.index("butter_only")
        assert seq.breakdown.align > decoy.breakdown.align


class TestWrapperExpansion:
    def test_wrapper_without_butter_ranks_for_bandpass(self, tmp_path):
        src = tmp_path / "wrappers.py"
        src.write_text(
            "from scipy import signal\n"
            "\n"
            "def _apply_sos(data):\n"
            "    sos = signal.butter(4, [1, 40], btype='bandpass', "
            "fs=250, output='sos')\n"
            "    return signal.sosfilt(sos, data)\n"
            "\n"
            "def process_traces(data):\n"
            '    """Apply the lab wrapper."""\n'
            "    return _apply_sos(data)\n"
            "\n"
            "def return_x(data):\n"
            '    """Return the array unchanged."""\n'
            "    return data\n"
        )
        from analysis_gui.repository.scan import scan_python_tree

        records = scan_python_tree(str(tmp_path))
        wrapper = next(r for r in records if r.name == "process_traces")
        assert "butter" not in wrapper.preview.lower()
        index = ApiIntentIndex.build(records)
        feat = index._feature_for_record(wrapper)
        assert feat is not None
        assert any("butter" in name for name in feat.api_seq)
        result = index.query("bandpass")
        names = [hit.record.name for hit in result.hits]
        assert "process_traces" in names
        if "return_x" in names:
            assert names.index("process_traces") < names.index("return_x")


class TestCrossFileWrapperExpansion:
    def test_relative_import_inherits_callee_api(self, tmp_path):
        pkg = tmp_path / "labpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "filters.py").write_text(
            "from scipy import signal\n"
            "\n"
            "def bandpass(data):\n"
            '    """Butterworth band-pass."""\n'
            "    sos = signal.butter(4, [1, 40], btype='bandpass', "
            "fs=250, output='sos')\n"
            "    return signal.sosfilt(sos, data)\n"
        )
        (pkg / "wrapper.py").write_text(
            "from .filters import bandpass\n"
            "\n"
            "def process_traces(data):\n"
            '    """Apply the imported lab filter."""\n'
            "    return bandpass(data)\n"
            "\n"
            "def return_x(data):\n"
            '    """Return the array unchanged."""\n'
            "    return data\n"
        )
        from analysis_gui.repository.scan import scan_python_tree

        records = scan_python_tree(str(pkg))
        wrapper = next(r for r in records if r.name == "process_traces")
        assert "butter" not in wrapper.preview.lower()
        index = ApiIntentIndex.build(records)
        feat = index._feature_for_record(wrapper)
        assert feat is not None
        assert any("butter" in name for name in feat.api_seq)
        assert any("sosfilt" in name for name in feat.api_seq)


class TestQueryByChunk:
    def test_from_span_ranks_filter_above_identity(self, tmp_path):
        src = tmp_path / "span_query.py"
        src.write_text(
            "from scipy import signal\n"
            "import matplotlib.pyplot as plt\n"
            "\n"
            "def bandpass_then_plot(data):\n"
            '    """Butter, sosfilt, then plot."""\n'
            "    sos = signal.butter(4, [1, 40], btype='bandpass', "
            "fs=250, output='sos')\n"
            "    filtered = signal.sosfilt(sos, data)\n"
            "    plt.plot(filtered)\n"
            "    return filtered\n"
            "\n"
            "def other_bandpass(data):\n"
            '    """Another filter implementation."""\n'
            "    sos = signal.butter(2, [2, 30], btype='band', "
            "fs=250, output='sos')\n"
            "    return signal.sosfilt(sos, data)\n"
            "\n"
            "def return_x(data):\n"
            '    """Identity decoy for plot-like queries."""\n'
            "    return data\n"
        )
        from analysis_gui.repository.scan import scan_python_tree

        records = scan_python_tree(str(tmp_path))
        seed = next(r for r in records if r.name == "bandpass_then_plot")
        spec = f"{seed.source_path}:{seed.lineno}-{seed.end_lineno}"
        index = ApiIntentIndex.build(records)
        result = index.query("", from_span=spec, fallback_records=records)
        names = [hit.record.name for hit in result.hits]
        assert "bandpass_then_plot" not in names
        assert "other_bandpass" in names
        assert "return_x" in names
        assert names.index("other_bandpass") < names.index("return_x")

    def test_from_kind_uses_chunk_features(self, tmp_path):
        src = tmp_path / "kind_query.py"
        src.write_text(
            "from scipy import signal\n"
            "\n"
            "def seed_filter(data):\n"
            '    """Seed bandpass."""\n'
            "    sos = signal.butter(4, [1, 40], output='sos')\n"
            "    return signal.sosfilt(sos, data)\n"
            "\n"
            "def cousin_filter(data):\n"
            '    """Related filter."""\n'
            "    return signal.sosfilt(signal.butter(2, 10, output='sos'), data)\n"
            "\n"
            "def return_x(data):\n"
            '    """Identity decoy that does not filter."""\n'
            "    return data\n"
        )
        from analysis_gui.repository.scan import scan_python_tree

        records = scan_python_tree(str(tmp_path))
        seed = next(r for r in records if r.name == "seed_filter")
        index = ApiIntentIndex.build(records)
        result = index.query("", from_kind=seed.kind, fallback_records=records)
        names = [hit.record.name for hit in result.hits]
        assert "seed_filter" not in names
        assert names.index("cousin_filter") < names.index("return_x")


class TestScientificLexicon:
    def test_spikeinterface_names_are_in_the_table(self):
        from analysis_gui.repository.lexicon import OPERATION_APIS, OPERATION_PHRASES

        for key in (
            "bandpass_filter",
            "common_reference",
            "whiten",
            "kilosort",
            "quality_metrics",
            "waveforms",
            "templates",
            "export_to_phy",
            "sorter",
            "psth",
            "raster",
        ):
            assert key in OPERATION_APIS
        phrases = {phrase for phrase, _ in OPERATION_PHRASES}
        assert "isi violation" in phrases
        assert "spikeinterface.preprocessing" in OPERATION_APIS["spikeinterface"]

    def test_query_expands_bandpass_filter_identifier(self):
        records = [
            _chunk(
                1,
                name="si_pre",
                preview="out = bandpass_filter(rec)\n",
                calls=["bandpass_filter"],
            ),
            _chunk(2, name="decoy", preview="return x\n", calls=[]),
        ]
        index = ApiIntentIndex.build(records)
        result = index.query("bandpass_filter")
        assert result.hits
        assert result.hits[0].record.name == "si_pre"
