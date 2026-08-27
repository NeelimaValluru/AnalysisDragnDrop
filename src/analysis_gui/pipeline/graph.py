"""Pipeline graph for managing node connections."""

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from .node import Node

#: Version of the ``.pipeline`` document format written by :meth:`PipelineGraph.to_dict`.
#:
#: Version history:
#:   0 -- implicit legacy format: no ``version`` key, parameters without
#:        ``value``, edges as bare ``[source, target]`` pairs.
#:   1 -- adds ``version``, ``NodeParameter.value`` and port-aware edge objects.
SCHEMA_VERSION = 1

#: Schema version assumed for documents that predate the ``version`` key.
LEGACY_SCHEMA_VERSION = 0


@dataclass(eq=False)
class Edge:
    """A directed connection from one node's output port to another's input.

    A port is named by the string a node declares for it (see
    :func:`~analysis_gui.pipeline.node.ports_for`), or ``None`` for the node's
    implicit single port.  ``None`` is used rather than a sentinel string such
    as ``"default"`` so that a port-unaware client round-trips a document
    without inventing port names it does not understand; it resolves to the
    sole declared port, and is ambiguous when a node declares several.

    The edge is where ports are serialized.  A node's ports are not: they are
    derived from its ``node_type`` and ``metadata``, so writing them would
    create a second copy that can disagree with the registry.  This is why
    declaring ports did not change the document format or its version.

    Serialized form::

        {"source": "<uuid>", "source_port": null,
         "target": "<uuid>", "target_port": null}

    For backwards compatibility an ``Edge`` iterates as the 2-tuple
    ``(source, target)`` and compares equal to a ``(source, target)`` tuple
    when both ports are unset, so code written against the legacy edge list
    keeps working.  Mutating callers should still go through
    :meth:`PipelineGraph.add_edge` / :meth:`PipelineGraph.remove_edge`, which
    keep the adjacency index in sync.
    """

    source: str
    target: str
    source_port: Optional[str] = None
    target_port: Optional[str] = None

    @property
    def key(self) -> Tuple[str, Optional[str], str, Optional[str]]:
        """Identity of the edge, including ports."""
        return (self.source, self.source_port, self.target, self.target_port)

    def __iter__(self) -> Iterator[str]:
        """Iterate as ``(source, target)`` for legacy unpacking."""
        yield self.source
        yield self.target

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Edge):
            return self.key == other.key
        if isinstance(other, (tuple, list)) and len(other) == 2:
            # A legacy 2-element edge denotes default (unset) ports.
            return (
                self.source_port is None
                and self.target_port is None
                and (self.source, self.target) == tuple(other)
            )
        return NotImplemented

    def __hash__(self) -> int:
        if self.source_port is None and self.target_port is None:
            return hash((self.source, self.target))
        return hash(self.key)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the edge to a JSON-serializable dictionary."""
        return {
            "source": self.source,
            "source_port": self.source_port,
            "target": self.target,
            "target_port": self.target_port,
        }

    @classmethod
    def from_any(
        cls, data: Union[Dict[str, Any], List[Any], Tuple[Any, ...]]
    ) -> "Edge":
        """Build an edge from either the v1 dict form or a legacy pair.

        Raises:
            ValueError: If the data is neither shape.
        """
        if isinstance(data, Edge):
            return cls(data.source, data.target, data.source_port, data.target_port)
        if isinstance(data, dict):
            if "source" not in data or "target" not in data:
                raise ValueError("Edge object requires 'source' and 'target'")
            return cls(
                source=data["source"],
                target=data["target"],
                source_port=data.get("source_port"),
                target_port=data.get("target_port"),
            )
        if isinstance(data, (list, tuple)) and len(data) == 2:
            return cls(source=data[0], target=data[1])
        raise ValueError(f"Unrecognized edge representation: {data!r}")


class PipelineGraph:
    """Represents a pipeline as a directed acyclic graph (DAG).

    Serialized form (schema version 1)::

        {
          "version": 1,
          "environment": {"python": "3.11"},   // optional; absent = no pin
          "nodes": {"<node-id>": {...see Node.to_dict...}},
          "edges": [{"source": "<id>", "source_port": null,
                     "target": "<id>", "target_port": null}]
        }

    :meth:`from_dict` also accepts version 0 documents: a missing ``version``
    is read as :data:`LEGACY_SCHEMA_VERSION`, parameters without ``value`` are
    treated as un-overridden, and ``[source, target]`` edges are read as edges
    with unset ports.  :meth:`to_dict` always writes the current version, so
    loading and saving upgrades a document in place.
    """

    def __init__(self):
        """Initialize an empty pipeline graph."""
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        # Schema version this graph was loaded from; a fresh graph is current.
        self.source_schema_version: int = SCHEMA_VERSION
        # Optional pin (``environment`` / ``requires``). Absent means no pin.
        self.environment: Dict[str, Any] = {}
        # Adjacency indexes so neighbour lookups are O(degree) instead of O(E).
        self._outgoing: Dict[str, List[Edge]] = {}
        self._incoming: Dict[str, List[Edge]] = {}

    def add_node(self, node: Node) -> str:
        """
        Add a node to the graph.

        Args:
            node: The node to add

        Returns:
            The node's ID
        """
        self.nodes[node.id] = node
        self._outgoing.setdefault(node.id, [])
        self._incoming.setdefault(node.id, [])
        return node.id

    def remove_node(self, node_id: str) -> bool:
        """
        Remove a node and all its connected edges.

        Args:
            node_id: ID of the node to remove

        Returns:
            True if removed, False if not found
        """
        if node_id not in self.nodes:
            return False

        del self.nodes[node_id]
        self.edges = [
            e for e in self.edges if e.source != node_id and e.target != node_id
        ]
        self._rebuild_index()
        return True

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
    ) -> bool:
        """
        Add an edge between two nodes.

        Args:
            source_id: ID of the source node
            target_id: ID of the target node
            source_port: Optional named output port on the source node
            target_port: Optional named input port on the target node

        Returns:
            True if edge added, False if invalid or already present
        """
        if source_id not in self.nodes or target_id not in self.nodes:
            return False

        edge = Edge(source_id, target_id, source_port, target_port)

        # Prevent duplicate edges (same endpoints *and* same ports).
        if any(existing.key == edge.key for existing in self.edges):
            return False

        self.edges.append(edge)
        self._outgoing.setdefault(source_id, []).append(edge)
        self._incoming.setdefault(target_id, []).append(edge)
        return True

    def remove_edge(
        self,
        source_id: str,
        target_id: str,
        source_port: Optional[str] = None,
        target_port: Optional[str] = None,
    ) -> bool:
        """
        Remove an edge between two nodes.

        The ports are part of the edge's identity: calling this without ports
        removes the default-port edge, not every edge between the two nodes.

        Args:
            source_id: ID of the source node
            target_id: ID of the target node
            source_port: Optional named output port on the source node
            target_port: Optional named input port on the target node

        Returns:
            True if removed, False if not found
        """
        key = Edge(source_id, target_id, source_port, target_port).key
        for i, edge in enumerate(self.edges):
            if edge.key == key:
                del self.edges[i]
                self._rebuild_index()
                return True
        return False

    def get_node(self, node_id: str) -> Optional[Node]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def get_incoming_edges(self, node_id: str) -> List[Edge]:
        """Get the edges that terminate at the given node, in insertion order."""
        return list(self._incoming.get(node_id, []))

    def get_outgoing_edges(self, node_id: str) -> List[Edge]:
        """Get the edges that originate at the given node, in insertion order."""
        return list(self._outgoing.get(node_id, []))

    def get_predecessors(self, node_id: str, port: Optional[str] = None) -> List[str]:
        """Get IDs of nodes that feed into the given node.

        Args:
            node_id: The node whose inputs to look up
            port: If given, only edges arriving at this input port are counted
        """
        return [
            e.source
            for e in self._incoming.get(node_id, [])
            if port is None or e.target_port == port
        ]

    def get_successors(self, node_id: str, port: Optional[str] = None) -> List[str]:
        """Get IDs of nodes that the given node feeds into.

        Args:
            node_id: The node whose outputs to look up
            port: If given, only edges leaving this output port are counted
        """
        return [
            e.target
            for e in self._outgoing.get(node_id, [])
            if port is None or e.source_port == port
        ]

    def get_topological_order(self) -> List[str]:
        """
        Get nodes in topological order for execution.

        Uses an iterative Kahn's algorithm, so deep pipelines cannot blow the
        recursion limit. Nodes are emitted in insertion order among those whose
        predecessors are already satisfied, which keeps generated code stable.
        Any nodes left over by a cycle are appended in insertion order so this
        always returns every node.

        Returns:
            List of node IDs in execution order
        """
        order = self._kahn_order()
        if len(order) < len(self.nodes):
            emitted = set(order)
            order.extend(nid for nid in self.nodes if nid not in emitted)
        return order

    def _kahn_order(self) -> List[str]:
        """Return the topologically sortable prefix of the graph."""
        in_degree = {
            node_id: sum(
                1 for e in self._incoming.get(node_id, []) if e.source in self.nodes
            )
            for node_id in self.nodes
        }
        ready = [node_id for node_id in self.nodes if in_degree[node_id] == 0]
        order: List[str] = []

        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            for successor in self.get_successors(node_id):
                if successor not in in_degree:
                    continue
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    ready.append(successor)

        return order

    def is_valid(self) -> Tuple[bool, str]:
        """
        Validate the pipeline graph.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self.nodes:
            return False, "Pipeline has no nodes"

        if len(self._kahn_order()) < len(self.nodes):
            return False, "Pipeline contains cycles"

        return True, "Valid pipeline"

    def _rebuild_index(self) -> None:
        """Rebuild the adjacency indexes from :attr:`edges`."""
        self._outgoing = {node_id: [] for node_id in self.nodes}
        self._incoming = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            self._outgoing.setdefault(edge.source, []).append(edge)
            self._incoming.setdefault(edge.target, []).append(edge)

    def to_dict(self) -> Dict[str, Any]:
        """Convert graph to dictionary for serialization."""
        data: Dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
        }
        if self.environment:
            data["environment"] = dict(self.environment)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineGraph":
        """Create graph from dictionary, accepting any schema version <= current."""
        graph = cls()

        # Absent version means a pre-versioning (legacy) document.
        graph.source_schema_version = cls.schema_version_of(data)

        env = data.get("environment")
        if not isinstance(env, dict):
            env = data.get("requires")
        if isinstance(env, dict):
            graph.environment = dict(env)

        for node_data in data.get("nodes", {}).values():
            graph.add_node(Node.from_dict(node_data))

        for edge_data in data.get("edges", []):
            edge = Edge.from_any(edge_data)
            graph.add_edge(edge.source, edge.target, edge.source_port, edge.target_port)

        return graph

    @classmethod
    def from_file(cls, path: str) -> "PipelineGraph":
        """Load a graph from a ``.pipeline`` file on disk.

        This is the one path from a filename to a graph, shared by the window's
        File > Open, the startup file argument and anything else that grows a
        need for it, so their notions of "loadable" cannot drift apart.

        Every way a document can be unusable arrives as one of two exceptions,
        so callers do not have to enumerate the ways :meth:`from_dict` can fail
        on corrupt input.

        Raises:
            OSError: The file could not be read.
            ValueError: The file is not a JSON object describing a pipeline.
        """
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)  # JSONDecodeError is a ValueError

        if not isinstance(data, dict):
            raise ValueError(
                f"pipeline document must be a JSON object, "
                f"found {type(data).__name__}"
            )

        try:
            return cls.from_dict(data)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"could not load pipeline: {exc}") from exc

    @staticmethod
    def schema_version_of(data: Dict[str, Any]) -> int:
        """Return the schema version a serialized pipeline declares."""
        version = data.get("version", LEGACY_SCHEMA_VERSION)
        return version if isinstance(version, int) else LEGACY_SCHEMA_VERSION
