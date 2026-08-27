"""Tests for Claude/GPT clients, dispatch, and model-call codegen."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from analysis_gui.models import (
    ClaudeClient,
    GPTClient,
    MissingAPIKeyError,
    MissingSDKError,
    ModelError,
    ModelProvider,
    UnknownProviderError,
    complete,
    preview_for_prompt,
    run_model_call,
)
from analysis_gui.models.base import require_package
from analysis_gui.models.claude import resolve_model as resolve_claude_model
from analysis_gui.pipeline import CodeGenerator, Node, PipelineGraph
from analysis_gui.pipeline.node import describe_node_kinds

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


class _FakeFrame:
    """A DataFrame-shaped object: ``head`` / ``to_string`` / ``len``."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __len__(self):
        return len(self._rows)

    def head(self, n):
        return _FakeFrame(self._rows[:n])

    def to_string(self):
        return "\n".join(self._rows)


def _claude_sdk(text="claude-ok"):
    """A stand-in for the ``anthropic`` package."""
    message = SimpleNamespace(content=[SimpleNamespace(text=text, type="text")])
    client = MagicMock()
    client.messages.create.return_value = message
    module = MagicMock()
    module.Anthropic.return_value = client
    return module, client


def _gpt_sdk(text="gpt-ok"):
    """A stand-in for the ``openai`` package."""
    choice = SimpleNamespace(message=SimpleNamespace(content=text))
    response = SimpleNamespace(choices=[choice])
    client = MagicMock()
    client.chat.completions.create.return_value = response
    module = MagicMock()
    module.OpenAI.return_value = client
    return module, client


def _model_pipeline(kind, **param_values):
    graph = PipelineGraph()
    node = Node.create_from_kind(kind)
    for name, value in param_values.items():
        node.parameters[name].value = value
    graph.add_node(node)
    return graph, node


class TestDescribeKinds:
    def test_palette_kinds_carry_provider_metadata(self):
        kinds = {item["kind"]: item for item in describe_node_kinds()}

        assert kinds["model_claude"]["metadata"]["provider"] == "claude"
        assert kinds["model_gpt"]["metadata"]["provider"] == "gpt"
        assert kinds["model_claude"]["node_type"] == "model_call"
        assert kinds["model_gpt"]["node_type"] == "model_call"

        claude_params = {p["name"]: p for p in kinds["model_claude"]["parameters"]}
        gpt_params = {p["name"]: p for p in kinds["model_gpt"]["parameters"]}
        assert claude_params["model"]["default_value"] == "claude-sonnet-5"
        assert gpt_params["model"]["default_value"] == "gpt-4.1"
        assert "prompt" in claude_params
        assert "prompt" in gpt_params


class TestDispatch:
    def test_claude_kind_hits_claude_client(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["client"] = "claude"
            seen["prompt"] = prompt
            seen["model"] = model
            return "from-claude"

        monkeypatch.setattr(ClaudeClient, "complete", fake_complete)
        monkeypatch.setattr(
            GPTClient,
            "complete",
            lambda *a, **k: pytest.fail("GPT client must not be called"),
        )

        node = Node.create_from_kind("model_claude")
        node.parameters["prompt"].value = "summarize this"
        node.parameters["model"].value = "claude-opus-5"

        assert run_model_call(node) == "from-claude"
        assert seen == {
            "client": "claude",
            "prompt": "summarize this",
            "model": "claude-opus-5",
        }

    def test_gpt_kind_hits_gpt_client(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["client"] = "gpt"
            seen["prompt"] = prompt
            seen["model"] = model
            return "from-gpt"

        monkeypatch.setattr(GPTClient, "complete", fake_complete)
        monkeypatch.setattr(
            ClaudeClient,
            "complete",
            lambda *a, **k: pytest.fail("Claude client must not be called"),
        )

        node = Node.create_from_kind("model_gpt")
        node.parameters["prompt"].value = "classify this"
        node.parameters["model"].value = "gpt-4o"

        assert run_model_call(node) == "from-gpt"
        assert seen == {
            "client": "gpt",
            "prompt": "classify this",
            "model": "gpt-4o",
        }

    def test_create_model_call_dispatches_on_provider(self, monkeypatch):
        monkeypatch.setattr(ClaudeClient, "complete", lambda *a, **k: "c")
        monkeypatch.setattr(GPTClient, "complete", lambda *a, **k: "g")

        claude = Node.create_model_call("claude")
        gpt = Node.create_model_call("gpt")
        assert run_model_call(claude) == "c"
        assert run_model_call(gpt) == "g"

    def test_input_text_is_appended_to_the_prompt(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(ClaudeClient, "complete", fake_complete)
        node = Node.create_from_kind("model_claude")
        node.parameters["prompt"].value = "Analyze:"
        run_model_call(node, input_text="row,value")
        assert seen["prompt"] == "Analyze:\n\nrow,value"

    def test_upstream_dataframe_is_previewed_into_the_prompt(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(ClaudeClient, "complete", fake_complete)
        node = Node.create_from_kind("model_claude")
        node.parameters["prompt"].value = "Summarize"

        frame = _FakeFrame(["a  b", "1  2", "3  4"])
        run_model_call(node, input_text=frame)

        assert "Summarize" in seen["prompt"]
        assert "a  b" in seen["prompt"]
        assert "1  2" in seen["prompt"]

    def test_prompt_only_still_works_without_upstream_data(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(GPTClient, "complete", fake_complete)
        node = Node.create_from_kind("model_gpt")
        node.parameters["prompt"].value = "Just the prompt"
        run_model_call(node)

        assert seen["prompt"] == "Just the prompt"

    def test_complete_context_kwarg_reaches_the_client(self, monkeypatch):
        seen = {}

        def fake_complete(self, prompt, model=None, **kwargs):
            seen["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(ClaudeClient, "complete", fake_complete)
        complete("claude", "Look:", context="upstream-table")
        assert seen["prompt"] == "Look:\n\nupstream-table"

    def test_unknown_provider_raises(self):
        node = Node.create_model_call("claude")
        node.metadata["provider"] = "not-a-vendor"
        with pytest.raises(UnknownProviderError, match="not-a-vendor"):
            run_model_call(node)

    def test_open_weights_is_still_a_placeholder(self):
        with pytest.raises(ModelError, match="not implemented"):
            complete("open_weights", "hello")


class TestClaudeClient:
    def test_complete_calls_messages_api(self, monkeypatch):
        module, client = _claude_sdk("hello from claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(
            "analysis_gui.models.claude._import_anthropic", lambda: module
        )

        text = ClaudeClient().complete("ping", model="claude-opus-5")

        assert text == "hello from claude"
        module.Anthropic.assert_called_once_with(api_key="sk-ant-test")
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["max_tokens"] == 1024
        assert kwargs["messages"] == [{"role": "user", "content": "ping"}]

    def test_default_model_is_current_sonnet(self, monkeypatch):
        module, client = _claude_sdk()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(
            "analysis_gui.models.claude._import_anthropic", lambda: module
        )

        ClaudeClient().complete("ping")
        assert client.messages.create.call_args.kwargs["model"] == "claude-sonnet-5"

    def test_legacy_model_alias(self):
        assert resolve_claude_model("claude-3-sonnet") == "claude-sonnet-5"

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
            ClaudeClient().complete("hello")

    def test_missing_sdk(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        def fail(name, *args, **kwargs):
            raise ImportError(name)

        monkeypatch.setattr("analysis_gui.models.base.import_module", fail)
        with pytest.raises(MissingSDKError, match="pip install anthropic"):
            ClaudeClient().complete("hello")


class TestGPTClient:
    def test_complete_calls_chat_completions(self, monkeypatch):
        module, client = _gpt_sdk("hello from gpt")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        monkeypatch.setattr("analysis_gui.models.gpt._import_openai", lambda: module)

        text = GPTClient().complete("ping", model="gpt-4o")

        assert text == "hello from gpt"
        module.OpenAI.assert_called_once_with(api_key="sk-openai-test")
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["messages"] == [{"role": "user", "content": "ping"}]

    def test_default_model_is_gpt_4_1(self, monkeypatch):
        module, client = _gpt_sdk()
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        monkeypatch.setattr("analysis_gui.models.gpt._import_openai", lambda: module)

        GPTClient().complete("ping")
        assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4.1"

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
            GPTClient().complete("hello")

    def test_missing_sdk(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

        def fail(name, *args, **kwargs):
            raise ImportError(name)

        monkeypatch.setattr("analysis_gui.models.base.import_module", fail)
        with pytest.raises(MissingSDKError, match="pip install openai"):
            GPTClient().complete("hello")


class TestRequirePackage:
    def test_missing_module_names_pip_install(self, monkeypatch):
        def fail(name, *args, **kwargs):
            raise ImportError(name)

        monkeypatch.setattr("analysis_gui.models.base.import_module", fail)
        with pytest.raises(MissingSDKError, match="pip install anthropic"):
            require_package("anthropic")


class TestCodegen:
    def test_claude_node_emits_claude_complete(self):
        graph, _ = _model_pipeline("model_claude", prompt="Look at this")
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "from analysis_gui.models import complete" in code
        assert "complete('claude', 'Look at this', model='claude-sonnet-5')" in code
        assert "claude-3-sonnet-20240229" not in code
        assert "from anthropic import" not in code

    def test_gpt_node_emits_gpt_complete(self):
        graph, _ = _model_pipeline("model_gpt", prompt="Look at this")
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "from analysis_gui.models import complete" in code
        assert "complete('gpt', 'Look at this', model='gpt-4.1')" in code
        assert "openai.ChatCompletion" not in code
        assert "import openai" not in code

    def test_model_parameter_override_is_honored(self):
        graph, _ = _model_pipeline("model_claude", prompt="hi", model="claude-opus-5")
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "model='claude-opus-5'" in code
        assert "model='claude-sonnet-5'" not in code

    def test_gpt_model_parameter_override_is_honored(self):
        graph, _ = _model_pipeline("model_gpt", prompt="hi", model="gpt-4o")
        code = CodeGenerator(graph).generate()

        compile(code, "<generated>", "exec")
        assert "complete('gpt', 'hi', model='gpt-4o')" in code

    def test_prompt_with_quotes_still_compiles(self):
        graph, _ = _model_pipeline("model_gpt", prompt="Say \"hello\" and 'goodbye'")
        code = CodeGenerator(graph).generate()
        compile(code, "<generated>", "exec")

    def test_connected_upstream_is_passed_as_context(self):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        claude = Node.create_from_kind("model_claude")
        claude.parameters["prompt"].value = "Summarize"
        claude_id = graph.add_node(claude)
        graph.add_edge(loader, claude_id)

        code = CodeGenerator(graph).generate()
        compile(code, "<generated>", "exec")

        assert "preview_for_prompt" in code
        assert "context=preview_for_prompt(output_0)" in code
        assert (
            "complete('claude', 'Summarize', model='claude-sonnet-5', context=preview_for_prompt(output_0))"
            in code
        )

    def test_prompt_only_codegen_omits_context(self):
        graph, _ = _model_pipeline("model_claude", prompt="Look at this")
        code = CodeGenerator(graph).generate()
        compile(code, "<generated>", "exec")

        assert "context=" not in code
        assert "preview_for_prompt" not in code
        assert "complete('claude', 'Look at this', model='claude-sonnet-5')" in code

    def test_complete_helper_routes_like_codegen(self, monkeypatch):
        monkeypatch.setattr(ClaudeClient, "complete", lambda *a, **k: "c")
        monkeypatch.setattr(GPTClient, "complete", lambda *a, **k: "g")

        assert complete("claude", "p", model="claude-sonnet-5") == "c"
        assert complete(ModelProvider.GPT, "p", model="gpt-4.1") == "g"


class TestPreviewForPrompt:
    def test_plain_strings_pass_through(self):
        assert preview_for_prompt("row,value") == "row,value"

    def test_dataframe_uses_head_to_string(self):
        frame = _FakeFrame(["col_a col_b", "1 2", "3 4"])
        text = preview_for_prompt(frame)
        assert "col_a col_b" in text
        assert "1 2" in text

    def test_long_tables_are_truncated(self):
        rows = [f"row-{i}" for i in range(100)]
        frame = _FakeFrame(rows)
        text = preview_for_prompt(frame, head_rows=3, max_chars=80)
        assert "row-0" in text
        assert "100 rows total" in text
        assert "row-99" not in text
        assert "[truncated]" in text or len(text) <= 80 + len("\n... [truncated]")


MODELS_IMPORT_PROBE = """
import importlib.abc
import json
import sys


class BlockSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("anthropic", "openai", "PyQt6")
        if fullname in blocked or any(fullname.startswith(name + ".") for name in blocked):
            raise ImportError(f"{fullname} is unavailable")
        return None


sys.meta_path.insert(0, BlockSDKs())

import analysis_gui.models
import analysis_gui.cli
import analysis_gui.pipeline

print(json.dumps({
    "imported_models": "analysis_gui.models" in sys.modules,
    "leaked": sorted(
        name for name in sys.modules
        if name == "anthropic"
        or name.startswith("anthropic.")
        or name == "openai"
        or name.startswith("openai.")
        or name.startswith("PyQt6")
    ),
}))
"""


def test_importing_models_does_not_need_sdks_or_qt():
    result = subprocess.run(
        [sys.executable, "-c", MODELS_IMPORT_PROBE],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC_DIR), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["imported_models"] is True
    assert payload["leaked"] == []
