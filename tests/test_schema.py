"""Tests for the .pipeline schema: version, parameter values and ports (change 2)."""

import json

import pytest

from analysis_gui.pipeline import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    CodeGenerator,
    Edge,
    Node,
    NodeParameter,
    PipelineGraph,
)


def build_graph():
    """A small three node pipeline: loader -> split -> clustering."""
    graph = PipelineGraph()
    loader = graph.add_node(Node.create_data_loader("csv"))
    split = graph.add_node(Node.create_preprocessor("split"))
    cluster = graph.add_node(Node.create_analyzer("clustering"))
    graph.add_edge(loader, split)
    graph.add_edge(split, cluster, source_port="X_train", target_port="data")
    return graph, loader, split, cluster


def legacy_document(graph):
    """Render a graph the way schema version 0 wrote it."""
    data = graph.to_dict()
    nodes = {}
    for node_id, node_data in data["nodes"].items():
        node_data = dict(node_data)
        node_data["parameters"] = {
            name: {k: v for k, v in param.items() if k != "value"}
            for name, param in node_data["parameters"].items()
        }
        node_data["position"] = list(node_data["position"])
        nodes[node_id] = node_data
    return {
        "nodes": nodes,
        "edges": [[edge["source"], edge["target"]] for edge in data["edges"]],
    }


class TestSchemaVersion:
    def test_to_dict_declares_current_version(self):
        graph, *_ = build_graph()
        assert graph.to_dict()["version"] == SCHEMA_VERSION

    def test_fresh_graph_reports_current_version(self):
        assert PipelineGraph().source_schema_version == SCHEMA_VERSION

    def test_missing_version_reads_as_legacy(self):
        graph, *_ = build_graph()
        loaded = PipelineGraph.from_dict(legacy_document(graph))
        assert loaded.source_schema_version == LEGACY_SCHEMA_VERSION

    def test_schema_version_of_ignores_junk(self):
        assert PipelineGraph.schema_version_of({"version": "one"}) == (
            LEGACY_SCHEMA_VERSION
        )
        assert PipelineGraph.schema_version_of({"version": 1}) == 1


class TestFromFile:
    """The single path from a filename to a graph, shared by the GUI."""

    def test_round_trips_a_saved_pipeline(self, tmp_path):
        graph, loader, split, cluster = build_graph()
        path = tmp_path / "p.pipeline"
        path.write_text(json.dumps(graph.to_dict()))

        loaded = PipelineGraph.from_file(str(path))

        assert set(loaded.nodes) == {loader, split, cluster}
        assert loaded.edges == graph.edges
        assert loaded.source_schema_version == SCHEMA_VERSION

    def test_reads_a_legacy_document(self, tmp_path):
        graph, *_ = build_graph()
        path = tmp_path / "legacy.pipeline"
        path.write_text(json.dumps(legacy_document(graph)))

        loaded = PipelineGraph.from_file(str(path))

        assert loaded.source_schema_version == LEGACY_SCHEMA_VERSION
        assert len(loaded.nodes) == len(graph.nodes)

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            PipelineGraph.from_file(str(tmp_path / "nope.pipeline"))

    def test_bad_json_raises_valueerror(self, tmp_path):
        path = tmp_path / "broken.pipeline"
        path.write_text("{not json")

        with pytest.raises(ValueError):
            PipelineGraph.from_file(str(path))

    def test_non_object_document_raises_valueerror(self, tmp_path):
        path = tmp_path / "list.pipeline"
        path.write_text("[]")

        with pytest.raises(ValueError, match="must be a JSON object"):
            PipelineGraph.from_file(str(path))

    def test_corrupt_node_raises_valueerror(self, tmp_path):
        """from_dict's KeyError on a node without an id arrives as ValueError."""
        path = tmp_path / "corrupt.pipeline"
        path.write_text(json.dumps({"version": SCHEMA_VERSION, "nodes": {"a": {}}}))

        with pytest.raises(ValueError, match="could not load pipeline"):
            PipelineGraph.from_file(str(path))


class TestParameterValue:
    def test_resolved_value_prefers_override(self):
        param = NodeParameter(name="n", param_type="number", default_value=3)
        assert param.resolved_value == 3
        param.value = 7
        assert param.resolved_value == 7
        assert param.default_value == 3

    def test_value_round_trips(self):
        node = Node.create_analyzer("clustering")
        node.parameters["n_clusters"].value = 9

        restored = Node.from_dict(json.loads(json.dumps(node.to_dict())))

        assert restored.parameters["n_clusters"].value == 9
        assert restored.parameters["n_clusters"].default_value == 3
        assert restored.parameters["n_clusters"].resolved_value == 9

    def test_legacy_parameters_without_value_load(self):
        graph, *_ = build_graph()
        loaded = PipelineGraph.from_dict(legacy_document(graph))

        for node in loaded.nodes.values():
            for param in node.parameters.values():
                assert param.value is None
                assert param.resolved_value == param.default_value

    def test_unknown_parameter_keys_are_ignored(self):
        param = NodeParameter.from_dict(
            {"name": "x", "param_type": "number", "unit": "seconds"}
        )
        assert param.name == "x"

    def test_factories_do_not_share_parameter_objects(self):
        first = Node.create_analyzer("clustering")
        second = Node.create_analyzer("clustering")

        first.parameters["n_clusters"].value = 42

        assert second.parameters["n_clusters"].value is None
        assert Node.create_analyzer("clustering").parameters["n_clusters"].value is None

    def test_code_generator_uses_resolved_value(self):
        graph = PipelineGraph()
        node = Node.create_analyzer("clustering")
        node.parameters["n_clusters"].value = 7
        graph.add_node(node)

        code = CodeGenerator(graph).generate()

        assert "n_clusters=7" in code

    def test_code_generator_falls_back_to_default(self):
        graph = PipelineGraph()
        graph.add_node(Node.create_analyzer("clustering"))

        assert "n_clusters=3" in CodeGenerator(graph).generate()


class TestPortAwareEdges:
    def test_edges_serialize_with_ports(self):
        graph, loader, split, cluster = build_graph()
        edges = graph.to_dict()["edges"]

        assert edges[0] == {
            "source": loader,
            "source_port": None,
            "target": split,
            "target_port": None,
        }
        assert edges[1]["source_port"] == "X_train"
        assert edges[1]["target_port"] == "data"

    def test_round_trip_preserves_ports(self):
        graph, _, split, cluster = build_graph()
        restored = PipelineGraph.from_dict(json.loads(json.dumps(graph.to_dict())))

        ported = [e for e in restored.edges if e.source_port == "X_train"]
        assert len(ported) == 1
        assert ported[0].source == split
        assert ported[0].target == cluster
        assert ported[0].target_port == "data"

    def test_legacy_pair_edges_load_with_null_ports(self):
        graph, loader, split, _ = build_graph()
        restored = PipelineGraph.from_dict(legacy_document(graph))

        assert len(restored.edges) == 2
        assert all(
            e.source_port is None and e.target_port is None for e in restored.edges
        )
        assert restored.get_predecessors(split) == [loader]

    def test_edge_is_backwards_compatible_with_pairs(self):
        edge = Edge("a", "b")

        assert tuple(edge) == ("a", "b")
        assert edge == ("a", "b")
        assert edge == Edge("a", "b")
        assert Edge("a", "b", source_port="p") != ("a", "b")

    def test_ports_distinguish_parallel_edges(self):
        graph = PipelineGraph()
        split = graph.add_node(Node.create_preprocessor("split"))
        sink = graph.add_node(Node.create_analyzer("correlation"))

        assert graph.add_edge(split, sink, source_port="X_train")
        assert graph.add_edge(split, sink, source_port="X_test")
        assert not graph.add_edge(split, sink, source_port="X_test")
        assert len(graph.edges) == 2

    def test_predecessors_and_successors_filter_by_port(self):
        graph = PipelineGraph()
        split = graph.add_node(Node.create_preprocessor("split"))
        train_sink = graph.add_node(Node.create_analyzer("correlation"))
        test_sink = graph.add_node(Node.create_analyzer("correlation"))
        graph.add_edge(split, train_sink, source_port="X_train", target_port="data")
        graph.add_edge(split, test_sink, source_port="X_test", target_port="data")

        assert graph.get_successors(split, port="X_train") == [train_sink]
        assert graph.get_successors(split, port="X_test") == [test_sink]
        assert sorted(graph.get_successors(split)) == sorted([train_sink, test_sink])
        assert graph.get_predecessors(train_sink, port="data") == [split]
        assert graph.get_predecessors(train_sink, port="other") == []

    def test_incoming_and_outgoing_edges_expose_ports(self):
        graph, _, split, cluster = build_graph()

        outgoing = graph.get_outgoing_edges(split)
        assert [e.source_port for e in outgoing] == ["X_train"]
        assert [e.target_port for e in graph.get_incoming_edges(cluster)] == ["data"]

    def test_remove_edge_is_port_specific(self):
        graph = PipelineGraph()
        split = graph.add_node(Node.create_preprocessor("split"))
        sink = graph.add_node(Node.create_analyzer("correlation"))
        graph.add_edge(split, sink, source_port="X_train")

        assert not graph.remove_edge(split, sink)
        assert graph.remove_edge(split, sink, source_port="X_train")
        assert graph.edges == []

    def test_remove_node_clears_adjacency(self):
        graph, loader, split, cluster = build_graph()

        assert graph.remove_node(split)

        assert graph.get_successors(loader) == []
        assert graph.get_predecessors(cluster) == []
        assert graph.edges == []

    def test_from_dict_rejects_unrecognized_edges(self):
        graph, loader, split, _ = build_graph()
        data = graph.to_dict()
        data["edges"] = [[loader, split, "extra"]]

        with pytest.raises(ValueError):
            PipelineGraph.from_dict(data)


class TestTraversal:
    def test_topological_order_respects_edges(self):
        graph, loader, split, cluster = build_graph()
        order = graph.get_topological_order()

        assert order.index(loader) < order.index(split) < order.index(cluster)

    def test_topological_order_returns_every_node_even_with_cycles(self):
        graph = PipelineGraph()
        a = graph.add_node(Node.create_data_loader())
        b = graph.add_node(Node.create_preprocessor("normalize"))
        graph.add_edge(a, b)
        graph.add_edge(b, a)

        assert sorted(graph.get_topological_order()) == sorted([a, b])
        assert graph.is_valid() == (False, "Pipeline contains cycles")

    def test_deep_chain_does_not_recurse(self):
        """A 2000 node chain used to exceed the recursion limit."""
        graph = PipelineGraph()
        previous = None
        for _ in range(2000):
            current = graph.add_node(Node.create_preprocessor("normalize"))
            if previous is not None:
                graph.add_edge(previous, current)
            previous = current

        assert graph.is_valid()[0]
        assert len(graph.get_topological_order()) == 2000
