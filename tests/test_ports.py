"""Tests for declared node ports (change 4).

Covers the port declarations themselves, how a ``None`` port resolves against
them, the validation findings they make possible, port-aware code generation,
and that documents written before ports existed are unaffected.
"""

import io
import json

import pytest

from analysis_gui import cli
from analysis_gui.pipeline import (
    NODE_KINDS,
    NODE_TYPE_PORTS,
    PORT_AMBIGUOUS,
    PORT_DATA_KINDS,
    PORT_NONE_DECLARED,
    PORT_RESOLVED,
    PORT_UNKNOWN,
    CodeGenerator,
    Node,
    NodePort,
    NodeType,
    PipelineGraph,
    PortSet,
    ports_for,
)

PORT_KEYS = {"name", "label", "data_kind", "required", "description"}


def run_cli(*argv):
    """Run the CLI in-process, returning (exit_code, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def write_pipeline(tmp_path, data, name="p.pipeline"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def split_pipeline():
    """loader -> split, with X_train and y_train feeding two different nodes."""
    graph = PipelineGraph()
    loader = graph.add_node(Node.create_data_loader("csv"))
    split = graph.add_node(Node.create_preprocessor("split"))
    correlation = graph.add_node(Node.create_analyzer("correlation"))
    viz = graph.add_node(Node.create_visualizer())

    graph.add_edge(loader, split, source_port="output", target_port="data")
    graph.add_edge(split, correlation, source_port="X_train", target_port="data")
    graph.add_edge(split, viz, source_port="y_train", target_port="data")
    return graph, loader, split, correlation, viz


def legacy_document(graph):
    """Render a graph the way schema version 0 wrote it: no ports at all."""
    data = graph.to_dict()
    del data["version"]
    data["edges"] = [[edge["source"], edge["target"]] for edge in data["edges"]]
    return data


class TestPortDeclarations:
    def test_every_kind_declares_well_formed_ports(self):
        for kind, spec in NODE_KINDS.items():
            ports = spec.ports
            assert isinstance(ports, PortSet), kind

            for declared in (ports.inputs, ports.outputs):
                names = [port.name for port in declared]
                assert len(names) == len(set(names)), f"{kind} repeats a port name"
                for port in declared:
                    assert port.name, f"{kind} has an unnamed port"
                    assert port.label, f"{kind} port {port.name} has no label"
                    assert port.data_kind in PORT_DATA_KINDS

            assert not any(
                port.required for port in ports.outputs
            ), f"{kind} marks an output required, which means nothing"

    def test_kind_ports_match_the_node_the_factory_builds(self):
        for kind, spec in NODE_KINDS.items():
            assert spec.ports == Node.create_from_kind(kind).ports

    def test_data_loaders_have_no_inputs(self):
        assert Node.create_data_loader("csv").input_ports == ()
        assert len(Node.create_data_loader("csv").output_ports) == 1

    def test_split_declares_its_four_outputs(self):
        ports = Node.create_preprocessor("split").ports

        assert [port.name for port in ports.outputs] == [
            "X_train",
            "X_test",
            "y_train",
            "y_test",
        ]
        assert [port.data_kind for port in ports.outputs] == [
            "table",
            "table",
            "series",
            "series",
        ]
        assert [port.name for port in ports.inputs] == ["data"]

    def test_visualizers_are_sinks(self):
        assert Node.create_visualizer().output_ports == ()
        assert len(Node.create_visualizer().input_ports) == 1

    def test_model_calls_take_an_optional_input(self):
        """The generated call sends only the prompt, so nothing need feed it."""
        inputs = Node.create_model_call("claude").input_ports

        assert len(inputs) == 1
        assert inputs[0].required is False

    def test_multi_input_kinds_are_explicit(self):
        """Most kinds take one input; SI analyzer/compare take two named ports.

        The code generator's single-input shortcut is skipped for these kinds
        and each port is resolved by name.
        """
        multi = {
            "analyzer_neural_si_analyze": ("recording", "sorting"),
            "analyzer_neural_si_compare": ("sorting1", "sorting2"),
        }
        for kind, spec in NODE_KINDS.items():
            names = tuple(port.name for port in spec.ports.inputs)
            if kind in multi:
                assert names == multi[kind], kind
            else:
                assert len(names) <= 1, kind

    def test_ports_are_derived_from_type_and_metadata(self):
        loaded = Node.from_dict(Node.create_preprocessor("split").to_dict())

        assert [port.name for port in loaded.output_ports] == [
            "X_train",
            "X_test",
            "y_train",
            "y_test",
        ]

    def test_ports_are_not_serialized(self):
        """They are derivable from node_type plus metadata, so they are not
        stored: a saved copy could disagree with the registry."""
        data = Node.create_preprocessor("split").to_dict()

        assert "ports" not in data

    def test_unknown_variant_falls_back_to_the_type_default(self):
        assert (
            ports_for(NodeType.PREPROCESSOR, {"processor_type": "teleport"})
            == NODE_TYPE_PORTS[NodeType.PREPROCESSOR]
        )
        assert ports_for("preprocessor", None) == NODE_TYPE_PORTS[NodeType.PREPROCESSOR]

    def test_unknown_node_type_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown node type"):
            ports_for("quantum_loader")

    def test_every_node_type_declares_ports(self):
        for member in NodeType:
            assert isinstance(ports_for(member), PortSet)

    def test_data_kind_compatibility_is_equality_or_any(self):
        table = NodePort(name="a", label="A", data_kind="table")
        series = NodePort(name="b", label="B", data_kind="series")
        anything = NodePort(name="c", label="C", data_kind="any")

        assert table.accepts(table)
        assert not table.accepts(series)
        assert anything.accepts(series)
        assert series.accepts(anything)


class TestNonePortResolution:
    """``None`` means "the node's implicit single port"."""

    def test_none_resolves_to_the_sole_output(self):
        resolution = Node.create_data_loader("csv").ports.resolve_output(None)

        assert resolution.status == PORT_RESOLVED
        assert resolution.ok
        assert resolution.port.name == "output"

    def test_none_resolves_to_the_sole_input(self):
        resolution = Node.create_analyzer("correlation").ports.resolve_input(None)

        assert resolution.ok
        assert resolution.port.name == "data"

    def test_none_is_ambiguous_against_several_ports(self):
        resolution = Node.create_preprocessor("split").ports.resolve_output(None)

        assert resolution.status == PORT_AMBIGUOUS
        assert resolution.port is None
        assert not resolution.ok

    def test_none_cannot_resolve_when_nothing_is_declared(self):
        assert (
            Node.create_visualizer().ports.resolve_output(None).status
            == PORT_NONE_DECLARED
        )
        assert (
            Node.create_data_loader("csv").ports.resolve_input(None).status
            == PORT_NONE_DECLARED
        )

    def test_a_declared_name_resolves(self):
        resolution = Node.create_preprocessor("split").ports.resolve_output("y_test")

        assert resolution.ok
        assert resolution.port.data_kind == "series"

    def test_an_undeclared_name_does_not_resolve(self):
        resolution = Node.create_preprocessor("split").ports.resolve_output("z_train")

        assert resolution.status == PORT_UNKNOWN
        assert resolution.port is None

    def test_lookup_helpers_agree_with_resolution(self):
        ports = Node.create_preprocessor("split").ports

        assert ports.output("X_test").label == "X test"
        assert ports.output("nope") is None
        assert ports.input("data").required is True
        assert [port.name for port in ports.required_inputs()] == ["data"]


class TestDescribeContract:
    """``describe --json`` is what the canvas builds its palette from."""

    def test_describe_reports_ports_for_every_kind(self):
        code, out, _ = run_cli("describe", "--json")
        payload = json.loads(out)

        assert code == 0
        for described in payload["node_kinds"]:
            expected = Node.create_from_kind(described["kind"]).ports.to_dict()
            assert described["ports"] == expected

    def test_every_described_port_carries_the_full_contract(self):
        _, out, _ = run_cli("describe", "--json")

        for described in json.loads(out)["node_kinds"]:
            ports = described["ports"]
            assert set(ports) == {"inputs", "outputs"}
            for port in ports["inputs"] + ports["outputs"]:
                assert set(port) == PORT_KEYS
                assert port["data_kind"] in json.loads(out)["port_data_kinds"]

    def test_describe_reports_the_data_kind_vocabulary(self):
        _, out, _ = run_cli("describe", "--json")

        assert json.loads(out)["port_data_kinds"] == list(PORT_DATA_KINDS)

    def test_split_is_described_with_four_labelled_outputs(self):
        _, out, _ = run_cli("describe", "--json")
        kinds = {kind["kind"]: kind for kind in json.loads(out)["node_kinds"]}

        outputs = kinds["preprocessor_split"]["ports"]["outputs"]
        assert [port["name"] for port in outputs] == [
            "X_train",
            "X_test",
            "y_train",
            "y_test",
        ]
        assert outputs[0]["label"] == "X train"
        assert kinds["data_loader"]["ports"]["inputs"] == []


class TestPortValidation:
    def test_a_fully_ported_pipeline_has_no_findings(self, tmp_path):
        graph, *_ = split_pipeline()
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["findings"] == []

    def test_unknown_port_is_an_error(self, tmp_path):
        graph, _, split, correlation, _ = split_pipeline()
        data = graph.to_dict()
        data["edges"][1]["source_port"] = "X_trian"
        path = write_pipeline(tmp_path, data, name="typo.pipeline")

        code, out, err = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 1
        assert payload["valid"] is False
        finding = payload["findings"][0]
        assert finding["code"] == "unknown_port"
        assert finding["severity"] == "error"
        assert finding["node_id"] == split
        assert finding["edge_index"] == 1
        assert "X_trian" in finding["message"]
        assert "error:" in err

    def test_unknown_target_port_is_an_error(self, tmp_path):
        graph, _, _, correlation, _ = split_pipeline()
        data = graph.to_dict()
        data["edges"][1]["target_port"] = "rows"
        path = write_pipeline(tmp_path, data, name="badtarget.pipeline")

        code, out, _ = run_cli("validate", path)
        findings = json.loads(out)["findings"]

        assert code == 1
        assert findings[0]["code"] == "unknown_port"
        assert findings[0]["node_id"] == correlation
        assert "input port 'rows'" in findings[0]["message"]

    def test_connecting_to_a_node_with_no_such_direction_is_an_error(self, tmp_path):
        """A data loader has no inputs and a visualizer has no outputs."""
        graph, loader, split, _, viz = split_pipeline()
        data = graph.to_dict()
        data["edges"].append(
            {"source": viz, "source_port": None, "target": loader, "target_port": None}
        )
        path = write_pipeline(tmp_path, data, name="sink.pipeline")

        code, out, _ = run_cli("validate", path)
        findings = [
            f for f in json.loads(out)["findings"] if f["code"] == "unknown_port"
        ]

        assert code == 1
        assert len(findings) == 2
        assert {f["node_id"] for f in findings} == {viz, loader}
        assert "no output ports" in findings[0]["message"]
        assert "no input ports" in findings[1]["message"]

    def test_ambiguous_port_is_a_warning(self, tmp_path):
        """A null port on the four-output split node: legal, but unspecific."""
        graph, _, split, correlation, _ = split_pipeline()
        data = graph.to_dict()
        data["edges"][1]["source_port"] = None
        path = write_pipeline(tmp_path, data, name="ambiguous.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True
        finding = payload["findings"][0]
        assert finding["code"] == "ambiguous_port"
        assert finding["severity"] == "warning"
        assert finding["node_id"] == split
        assert finding["edge_index"] == 1
        assert "X_train, X_test, y_train, y_test" in finding["message"]

    def test_unconnected_required_input_is_a_warning(self, tmp_path):
        """Half-wired pipelines are normal while editing."""
        graph = PipelineGraph()
        graph.add_node(Node.create_data_loader("csv"))
        orphan = graph.add_node(Node.create_analyzer("clustering"))
        path = write_pipeline(tmp_path, graph.to_dict(), name="orphan.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True
        assert payload["summary"] == {"errors": 0, "warnings": 1}
        finding = payload["findings"][0]
        assert finding["code"] == "unconnected_required_input"
        assert finding["severity"] == "warning"
        assert finding["node_id"] == orphan
        assert "edge_index" not in finding

    def test_optional_input_left_unconnected_is_not_reported(self, tmp_path):
        graph = PipelineGraph()
        graph.add_node(Node.create_model_call("gpt"))
        path = write_pipeline(tmp_path, graph.to_dict(), name="model.pipeline")

        code, out, _ = run_cli("validate", path)

        assert code == 0
        assert json.loads(out)["findings"] == []

    def test_findings_follow_the_existing_shape(self, tmp_path):
        graph, *_ = split_pipeline()
        data = graph.to_dict()
        data["edges"][1]["source_port"] = "nope"
        path = write_pipeline(tmp_path, data, name="shape.pipeline")

        _, out, _ = run_cli("validate", path)

        for finding in json.loads(out)["findings"]:
            assert set(finding) <= {
                "severity",
                "code",
                "message",
                "node_id",
                "edge_index",
            }
            assert finding["severity"] in {"error", "warning"}

    def test_unknown_node_type_suppresses_port_checks(self, tmp_path):
        graph, *_ = split_pipeline()
        data = graph.to_dict()
        node_id = list(data["nodes"])[0]
        data["nodes"][node_id]["node_type"] = "quantum_loader"
        path = write_pipeline(tmp_path, data, name="unknowntype.pipeline")

        code, out, _ = run_cli("validate", path)
        codes = [f["code"] for f in json.loads(out)["findings"]]

        assert code == 1
        assert codes == ["unknown_node_type"]


class TestCodegenPortRouting:
    def test_two_split_outputs_feed_two_nodes(self):
        graph, _, _, _, _ = split_pipeline()

        code = CodeGenerator(graph).generate()

        assert "output_2 = output_1['X_train'].corr()" in code
        assert "plt.plot(output_1['y_train'])" in code
        compile(code, "<generated>", "exec")

    def test_each_split_output_is_individually_addressable(self):
        for port in ("X_train", "X_test", "y_train", "y_test"):
            graph = PipelineGraph()
            loader = graph.add_node(Node.create_data_loader("csv"))
            split = graph.add_node(Node.create_preprocessor("split"))
            sink = graph.add_node(Node.create_analyzer("correlation"))
            graph.add_edge(loader, split)
            graph.add_edge(split, sink, source_port=port, target_port="data")

            code = CodeGenerator(graph).generate()

            assert f"output_2 = output_1['{port}'].corr()" in code
            compile(code, "<generated>", "exec")

    def test_routing_survives_a_round_trip_through_json(self, tmp_path):
        graph, *_ = split_pipeline()
        path = write_pipeline(tmp_path, graph.to_dict())

        code, out, _ = run_cli("codegen", path)

        assert code == 0
        assert "output_1['X_train']" in out
        assert "output_1['y_train']" in out
        compile(out, "<generated>", "exec")

    def test_single_output_nodes_are_not_subscripted(self):
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(loader, normalize, source_port="output", target_port="data")

        code = CodeGenerator(graph).generate()

        assert "output_1 = output_0.copy()" in code
        assert "['output']" not in code

    def test_an_unresolvable_port_still_generates_code(self):
        """Code generation is tolerant where validation is strict."""
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(loader, normalize, source_port="output", target_port="typo")

        code = CodeGenerator(graph).generate()

        assert "output_1 = output_0.copy()" in code
        compile(code, "<generated>", "exec")

    def test_a_node_with_nothing_connected_reads_data(self):
        graph = PipelineGraph()
        graph.add_node(Node.create_analyzer("correlation"))

        assert "output_0 = data.corr()" in CodeGenerator(graph).generate()


class TestLegacyDocumentsAreUnaffected:
    """Documents written before ports existed must behave exactly as before."""

    def test_legacy_document_loads(self, tmp_path):
        graph, loader, split, correlation, viz = split_pipeline()
        path = write_pipeline(tmp_path, legacy_document(graph), name="legacy.pipeline")

        loaded = PipelineGraph.from_file(path)

        assert loaded.source_schema_version == 0
        assert set(loaded.nodes) == {loader, split, correlation, viz}
        assert all(
            edge.source_port is None and edge.target_port is None
            for edge in loaded.edges
        )

    def test_legacy_document_still_validates(self, tmp_path):
        graph, *_ = split_pipeline()
        path = write_pipeline(tmp_path, legacy_document(graph), name="legacy.pipeline")

        code, out, _ = run_cli("validate", path)
        payload = json.loads(out)

        assert code == 0
        assert payload["valid"] is True
        assert payload["summary"]["errors"] == 0
        # Two null-port edges leave the four-output split node.
        assert [f["code"] for f in payload["findings"]] == [
            "ambiguous_port",
            "ambiguous_port",
        ]

    def test_legacy_document_generates_the_same_code(self, tmp_path):
        """Null ports consume the upstream node whole, exactly as before."""
        graph, *_ = split_pipeline()
        path = write_pipeline(tmp_path, legacy_document(graph), name="legacy.pipeline")

        code, out, _ = run_cli("codegen", path)

        assert code == 0
        assert "output_2 = output_1.corr()" in out
        assert "plt.plot(output_1)" in out
        assert "output_1[" not in out
        compile(out, "<generated>", "exec")

    def test_legacy_chain_is_byte_identical(self, tmp_path):
        """The common single-output chain generates character for character
        what it did before ports existed."""
        graph = PipelineGraph()
        loader = graph.add_node(Node.create_data_loader("csv"))
        normalize = graph.add_node(Node.create_preprocessor("normalize"))
        cluster = graph.add_node(Node.create_analyzer("clustering"))
        viz = graph.add_node(Node.create_visualizer())
        graph.add_edge(loader, normalize)
        graph.add_edge(normalize, cluster)
        graph.add_edge(cluster, viz)

        from_memory = CodeGenerator(graph).generate()
        path = write_pipeline(tmp_path, legacy_document(graph), name="chain.pipeline")
        from_legacy = CodeGenerator(PipelineGraph.from_file(path)).generate()

        assert from_legacy == from_memory
        assert "output_1 = output_0.copy()" in from_legacy
        assert "kmeans.fit_predict(output_1)" in from_legacy
        assert "plt.plot(output_2)" in from_legacy
        compile(from_legacy, "<generated>", "exec")
