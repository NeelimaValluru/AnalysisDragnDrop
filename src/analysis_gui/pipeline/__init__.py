"""Pipeline system for data analysis workflows.

This subpackage is dependency-free: it imports only the standard library and
its own siblings, so it can run headless (no PyQt6, no display).
"""

from .node import (
    NODE_KINDS,
    NODE_TYPE_PORTS,
    PORT_AMBIGUOUS,
    PORT_DATA_KINDS,
    PORT_NONE_DECLARED,
    PORT_RESOLVED,
    PORT_UNKNOWN,
    VARIANT_PORTS,
    NEURAL_ANALYSIS_SIGNAL_TYPES,
    NEURAL_SIGNAL_TYPES,
    Node,
    NodeKind,
    NodeParameter,
    NodePort,
    NodeType,
    PortResolution,
    PortSet,
    describe_node_kinds,
    ports_for,
)
from .graph import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Edge, PipelineGraph
from .code_generator import CodeGenerator
from .receipt import RECEIPT_SCHEMA_VERSION

__all__ = [
    "Node",
    "NodeType",
    "NodeParameter",
    "NodeKind",
    "NODE_KINDS",
    "NodePort",
    "PortSet",
    "PortResolution",
    "ports_for",
    "NODE_TYPE_PORTS",
    "VARIANT_PORTS",
    "PORT_DATA_KINDS",
    "PORT_RESOLVED",
    "PORT_UNKNOWN",
    "PORT_AMBIGUOUS",
    "PORT_NONE_DECLARED",
    "describe_node_kinds",
    "NEURAL_SIGNAL_TYPES",
    "NEURAL_ANALYSIS_SIGNAL_TYPES",
    "PipelineGraph",
    "Edge",
    "SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "CodeGenerator",
]
