"""Node definitions for pipeline components.

Node kinds are declared once, at module level, in the ``*_CONFIGS`` dictionaries
and in :data:`NODE_KINDS`.  The UI palette, the ``Node.create_*`` factories and
the ``analysis-gui-cli describe`` command all read from those declarations so
they cannot drift apart.

Ports follow the same rule.  :data:`NODE_TYPE_PORTS` and :data:`VARIANT_PORTS`
are the only place a port is declared; everything else (a node instance, a
:class:`NodeKind`, :func:`describe_node_kinds`, the validator, the code
generator) reaches them through :func:`ports_for`.  Ports are therefore derived
from ``node_type`` plus ``metadata`` rather than stored on the node, which is
why they are absent from :meth:`Node.to_dict`: a saved port list could go stale
against the registry, and there would be two answers to "what ports does this
node have".
"""

import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class NodeType(Enum):
    """Types of nodes in the pipeline."""

    DATA_LOADER = "data_loader"
    PREPROCESSOR = "preprocessor"
    ANALYZER = "analyzer"
    VISUALIZER = "visualizer"
    MODEL_CALL = "model_call"
    CUSTOM_CODE = "custom_code"
    OUTPUT = "output"


@dataclass
class NodeParameter:
    """A parameter for a node.

    ``default_value`` belongs to the node *kind* and is effectively read-only:
    it is whatever the factory in this module declared.  ``value`` holds a user
    supplied override and stays ``None`` until someone edits the parameter.
    Consumers (code generation, inspectors, the CLI) should read
    :attr:`resolved_value` rather than either field directly.

    Tradeoff: ``None`` doubles as "not overridden", so a user cannot explicitly
    override a parameter *to* ``None``.  No parameter type currently treats
    ``None`` as a meaningful value, and the alternative (a separate
    ``has_value`` flag) adds a field that every client would have to serialize
    correctly to avoid silently dropping edits.
    """

    name: str
    param_type: str  # "string", "number", "boolean", "file", "dropdown"
    default_value: Any = None
    description: str = ""
    options: List[str] = field(default_factory=list)
    value: Any = None  # User override; None means "use default_value"

    @property
    def resolved_value(self) -> Any:
        """The value to actually use: the override if set, else the default."""
        return self.value if self.value is not None else self.default_value

    def to_dict(self) -> Dict[str, Any]:
        """Convert the parameter to a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "param_type": self.param_type,
            "default_value": self.default_value,
            "value": self.value,
            "description": self.description,
            "options": list(self.options),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeParameter":
        """Create a parameter from a dictionary.

        Unknown keys are ignored and missing keys fall back to defaults, so
        both pre-``value`` files and files written by a newer client load.
        """
        return cls(
            name=data["name"],
            param_type=data.get("param_type", "string"),
            default_value=data.get("default_value"),
            description=data.get("description", ""),
            options=list(data.get("options") or []),
            value=data.get("value"),
        )


# --------------------------------------------------------------------------
# Port model.
#
# A port is a named connection point on a node.  Edges reference ports by
# name; a graph editor draws one handle per port.  Ports are declared per node
# type, with per-variant overrides for the kinds whose shape differs (today
# only Train/Test Split and the analyzers, which differ in what they emit).
# --------------------------------------------------------------------------

#: Data-kind tags.  Deliberately a flat vocabulary of strings rather than a
#: type system: a canvas only needs enough to reject nonsense while dragging.
#: The compatibility rule is "connectable if either side is ``any``, or the
#: two tags are equal", and it is the whole rule.
#:
#: Neural loaders emit a typed tag (``eeg``, ``spike``, ``lfp``, ``calcium``)
#: so the canvas can tell them apart.  Neural analyzers accept ``any`` so a
#: plain CSV table still connects (channels × time, or spike timestamps)
#: without a converter node.  Existing CSV → normalize pipelines keep
#: emitting and consuming ``table`` and are unchanged.  Typed neural output
#: will not attach to a ``table``-only preprocessor (Normalize, Split, ...);
#: that is intentional — use a CSV loader if you want those steps.
DATA_KIND_ANY = "any"
DATA_KIND_TABLE = "table"  # 2-D tabular data (a DataFrame)
DATA_KIND_SERIES = "series"  # 1-D labels/targets (a Series or ndarray)
DATA_KIND_TEXT = "text"  # free text, e.g. a model response
DATA_KIND_EEG = "eeg"
DATA_KIND_SPIKE = "spike"
DATA_KIND_LFP = "lfp"
DATA_KIND_CALCIUM = "calcium"

#: Every tag that may appear as a port's ``data_kind``, reported by the CLI so
#: a client does not have to hard-code the vocabulary.
PORT_DATA_KINDS: Tuple[str, ...] = (
    DATA_KIND_ANY,
    DATA_KIND_SERIES,
    DATA_KIND_TABLE,
    DATA_KIND_TEXT,
    DATA_KIND_EEG,
    DATA_KIND_SPIKE,
    DATA_KIND_LFP,
    DATA_KIND_CALCIUM,
)

#: ``metadata.signal_type`` values a neural loader may declare.  Same strings
#: as the typed port tags, so a client that already understands ``data_kind``
#: can reuse the vocabulary.
NEURAL_SIGNAL_TYPES: Tuple[str, ...] = (
    DATA_KIND_EEG,
    DATA_KIND_SPIKE,
    DATA_KIND_LFP,
    DATA_KIND_CALCIUM,
)

#: Which recording types each neural analysis variant accepts.  Keyed by the
#: variant string (``processor_type`` or ``analyzer_type``).  Used by
#: validation; an upstream ``signal_type`` outside the set is
#: ``incompatible_signal_type``.
NEURAL_ANALYSIS_SIGNAL_TYPES = {
    "neural_filter": frozenset({DATA_KIND_EEG, DATA_KIND_LFP}),
    "neural_montage": frozenset({DATA_KIND_EEG, DATA_KIND_LFP}),
    "neural_ica": frozenset({DATA_KIND_EEG}),
    "neural_spectrum": frozenset({DATA_KIND_EEG, DATA_KIND_LFP}),
    "neural_epochs": frozenset({DATA_KIND_EEG}),
    "neural_spike": frozenset({DATA_KIND_SPIKE}),
    "neural_calcium": frozenset({DATA_KIND_CALCIUM}),
    "si_preprocess": frozenset({DATA_KIND_SPIKE}),
    "si_sort": frozenset({DATA_KIND_SPIKE}),
    "si_analyze": frozenset({DATA_KIND_SPIKE}),
    "si_metrics": frozenset({DATA_KIND_SPIKE}),
    "si_curate": frozenset({DATA_KIND_SPIKE}),
    "si_export": frozenset({DATA_KIND_SPIKE}),
    "si_compare": frozenset({DATA_KIND_SPIKE}),
}

#: Metadata stamped on every SpikeInterface canvas node.
SI_NODE_METADATA = {
    "signal_type": DATA_KIND_SPIKE,
    "backend": "spikeinterface",
}


@dataclass(frozen=True)
class NodePort:
    """One named connection point on a node.

    ``name`` is the stable identifier written into an edge's ``source_port`` /
    ``target_port``.  ``label`` is for humans and may change freely.

    ``required`` says an input must be connected for the node to mean anything;
    it is meaningless on an output and is always ``False`` there.  No node kind
    currently accepts a variable number of connections on one port, so there is
    no fan-in flag: every port takes at most one edge.  If a kind ever needs
    fan-in, this is where the flag goes.
    """

    name: str
    label: str
    data_kind: str = DATA_KIND_ANY
    required: bool = False
    description: str = ""

    def accepts(self, other: "NodePort") -> bool:
        """Whether a value from ``other`` may flow into (or out of) this port."""
        return (
            self.data_kind == DATA_KIND_ANY
            or other.data_kind == DATA_KIND_ANY
            or self.data_kind == other.data_kind
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the port to a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "label": self.label,
            "data_kind": self.data_kind,
            "required": self.required,
            "description": self.description,
        }


#: Outcomes of resolving an edge's port reference against a declared port list.
PORT_RESOLVED = "resolved"  # named a declared port, or None with exactly one
PORT_UNKNOWN = "unknown"  # named a port the node does not declare
PORT_AMBIGUOUS = "ambiguous"  # None against several declared ports
PORT_NONE_DECLARED = "none_declared"  # the node has no ports in that direction


@dataclass(frozen=True)
class PortResolution:
    """The result of resolving a port reference from an edge."""

    status: str
    port: Optional[NodePort] = None

    @property
    def ok(self) -> bool:
        """Whether the reference names exactly one real port."""
        return self.status == PORT_RESOLVED


@dataclass(frozen=True)
class PortSet:
    """The inputs and outputs a node declares."""

    inputs: Tuple[NodePort, ...] = ()
    outputs: Tuple[NodePort, ...] = ()

    def input(self, name: str) -> Optional[NodePort]:
        """Return the named input port, or ``None`` if it is not declared."""
        return next((p for p in self.inputs if p.name == name), None)

    def output(self, name: str) -> Optional[NodePort]:
        """Return the named output port, or ``None`` if it is not declared."""
        return next((p for p in self.outputs if p.name == name), None)

    def resolve_input(self, name: Optional[str]) -> PortResolution:
        """Resolve an edge's ``target_port`` against the declared inputs."""
        return _resolve_port(self.inputs, name)

    def resolve_output(self, name: Optional[str]) -> PortResolution:
        """Resolve an edge's ``source_port`` against the declared outputs."""
        return _resolve_port(self.outputs, name)

    def required_inputs(self) -> Tuple[NodePort, ...]:
        """The input ports that must be connected."""
        return tuple(p for p in self.inputs if p.required)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the port set to a JSON-serializable dictionary."""
        return {
            "inputs": [port.to_dict() for port in self.inputs],
            "outputs": [port.to_dict() for port in self.outputs],
        }


def _resolve_port(ports: Tuple[NodePort, ...], name: Optional[str]) -> PortResolution:
    """Resolve a port reference, which may be ``None``, against ``ports``.

    ``None`` means "the node's implicit single port" and predates port
    declarations, so every pre-existing ``.pipeline`` file is full of it.  It
    resolves to the sole port when the node declares exactly one, which is the
    common case and keeps those files meaningful.  Against several ports it is
    genuinely ambiguous and against none it cannot resolve at all; both are
    reported rather than guessed, and callers decide what to do (the validator
    warns, the code generator falls back to its pre-port behaviour).
    """
    if name is None:
        if len(ports) == 1:
            return PortResolution(PORT_RESOLVED, ports[0])
        if not ports:
            return PortResolution(PORT_NONE_DECLARED)
        return PortResolution(PORT_AMBIGUOUS)

    if not ports:
        return PortResolution(PORT_NONE_DECLARED)

    match = next((p for p in ports if p.name == name), None)
    if match is None:
        return PortResolution(PORT_UNKNOWN)
    return PortResolution(PORT_RESOLVED, match)


# The single input port every consuming node declares.  Named "data" because
# that is the name port-aware edges written before this change already use.
_DATA_IN_TABLE = NodePort(
    name="data",
    label="Data",
    data_kind=DATA_KIND_TABLE,
    required=True,
    description="Tabular data to process",
)
_DATA_IN_ANY = NodePort(
    name="data",
    label="Data",
    data_kind=DATA_KIND_ANY,
    required=True,
    description="Value to consume",
)

# Neural analyzers accept ``any`` so both a typed neural loader and a plain
# CSV table (channels × time, or spike timestamps) can feed them.
_NEURAL_IN_ANY = NodePort(
    name="data",
    label="Data",
    data_kind=DATA_KIND_ANY,
    required=True,
    description="Neural recording or a generic table",
)
_NEURAL_OUT_TABLE = NodePort(
    name="result",
    label="Result",
    data_kind=DATA_KIND_TABLE,
    description="Analysis result as a table",
)
_NEURAL_FILTER_OUT = NodePort(
    name="output",
    label="Output",
    data_kind=DATA_KIND_TABLE,
    description="Filtered samples × channels",
)

# SpikeInterface nodes speak ``spike`` so they do not attach to EEG/table
# ports.  Named recording/sorting inputs keep a Recording from feeding a
# Sorting slot even though both tags are ``spike``.
_SI_RECORDING_IN = NodePort(
    name="data",
    label="Recording",
    data_kind=DATA_KIND_SPIKE,
    required=True,
    description="SpikeInterface Recording",
)
_SI_RECORDING_OUT = NodePort(
    name="output",
    label="Recording",
    data_kind=DATA_KIND_SPIKE,
    description="SpikeInterface Recording",
)
_SI_SORTING_OUT = NodePort(
    name="output",
    label="Sorting",
    data_kind=DATA_KIND_SPIKE,
    description="SpikeInterface Sorting",
)
_SI_ANALYZER_IN = NodePort(
    name="data",
    label="Analyzer",
    data_kind=DATA_KIND_SPIKE,
    required=True,
    description="SpikeInterface SortingAnalyzer",
)
_SI_ANALYZER_OUT = NodePort(
    name="output",
    label="Analyzer",
    data_kind=DATA_KIND_SPIKE,
    description="SpikeInterface SortingAnalyzer",
)

#: Ports declared by every node of a type, before per-variant overrides.
NODE_TYPE_PORTS: Dict[NodeType, PortSet] = {
    NodeType.DATA_LOADER: PortSet(
        inputs=(),
        outputs=(
            NodePort(
                name="output",
                label="Data",
                data_kind=DATA_KIND_TABLE,
                description="The loaded table",
            ),
        ),
    ),
    NodeType.PREPROCESSOR: PortSet(
        inputs=(_DATA_IN_TABLE,),
        outputs=(
            NodePort(
                name="output",
                label="Output",
                data_kind=DATA_KIND_TABLE,
                description="The transformed table",
            ),
        ),
    ),
    NodeType.ANALYZER: PortSet(
        inputs=(_DATA_IN_TABLE,),
        outputs=(
            NodePort(
                name="result",
                label="Result",
                data_kind=DATA_KIND_ANY,
                description="The analysis result",
            ),
        ),
    ),
    NodeType.VISUALIZER: PortSet(
        inputs=(_DATA_IN_ANY,),
        outputs=(),
    ),
    NodeType.MODEL_CALL: PortSet(
        # Optional: when nothing is connected the call still runs on the
        # prompt alone; an incoming edge is previewed into the prompt.
        inputs=(
            NodePort(
                name="data",
                label="Context",
                data_kind=DATA_KIND_ANY,
                required=False,
                description="Optional upstream value, previewed into the prompt",
            ),
        ),
        outputs=(
            NodePort(
                name="response",
                label="Response",
                data_kind=DATA_KIND_TEXT,
                description="The model's reply",
            ),
        ),
    ),
    NodeType.CUSTOM_CODE: PortSet(
        inputs=(_DATA_IN_ANY,),
        outputs=(
            NodePort(
                name="output",
                label="Output",
                data_kind=DATA_KIND_ANY,
                description="Whatever the custom function returns",
            ),
        ),
    ),
    NodeType.OUTPUT: PortSet(inputs=(_DATA_IN_ANY,), outputs=()),
}

#: Ports for the variants whose shape differs from their type's default,
#: keyed by ``(node_type, variant)`` where the variant is the discriminating
#: metadata value (``processor_type``, ``analyzer_type``, ``provider``,
#: ``signal_type``).
VARIANT_PORTS: Dict[Tuple[NodeType, str], PortSet] = {
    (NodeType.DATA_LOADER, "eeg"): PortSet(
        inputs=(),
        outputs=(
            NodePort(
                name="output",
                label="EEG",
                data_kind=DATA_KIND_EEG,
                description="EEG samples × channels",
            ),
        ),
    ),
    (NodeType.DATA_LOADER, "spike"): PortSet(
        inputs=(),
        outputs=(
            NodePort(
                name="output",
                label="Spikes",
                data_kind=DATA_KIND_SPIKE,
                description="Spike timestamps (and optional unit ids)",
            ),
        ),
    ),
    (NodeType.DATA_LOADER, "lfp"): PortSet(
        inputs=(),
        outputs=(
            NodePort(
                name="output",
                label="LFP",
                data_kind=DATA_KIND_LFP,
                description="LFP samples × channels",
            ),
        ),
    ),
    (NodeType.DATA_LOADER, "calcium"): PortSet(
        inputs=(),
        outputs=(
            NodePort(
                name="output",
                label="Calcium",
                data_kind=DATA_KIND_CALCIUM,
                description="Calcium fluorescence samples × channels",
            ),
        ),
    ),
    (NodeType.PREPROCESSOR, "split"): PortSet(
        inputs=(_DATA_IN_TABLE,),
        outputs=(
            NodePort(
                name="X_train",
                label="X train",
                data_kind=DATA_KIND_TABLE,
                description="Training features",
            ),
            NodePort(
                name="X_test",
                label="X test",
                data_kind=DATA_KIND_TABLE,
                description="Test features",
            ),
            NodePort(
                name="y_train",
                label="y train",
                data_kind=DATA_KIND_SERIES,
                description="Training target",
            ),
            NodePort(
                name="y_test",
                label="y test",
                data_kind=DATA_KIND_SERIES,
                description="Test target",
            ),
        ),
    ),
    (NodeType.PREPROCESSOR, "neural_filter"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_FILTER_OUT,),
    ),
    (NodeType.PREPROCESSOR, "neural_montage"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_FILTER_OUT,),
    ),
    (NodeType.PREPROCESSOR, "neural_ica"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_FILTER_OUT,),
    ),
    (NodeType.ANALYZER, "correlation"): PortSet(
        inputs=(_DATA_IN_TABLE,),
        outputs=(
            NodePort(
                name="result",
                label="Correlation matrix",
                data_kind=DATA_KIND_TABLE,
                description="Pairwise correlations between features",
            ),
        ),
    ),
    (NodeType.ANALYZER, "clustering"): PortSet(
        inputs=(_DATA_IN_TABLE,),
        outputs=(
            NodePort(
                name="result",
                label="Cluster labels",
                data_kind=DATA_KIND_SERIES,
                description="One cluster label per row",
            ),
        ),
    ),
    (NodeType.ANALYZER, "neural_spectrum"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_OUT_TABLE,),
    ),
    (NodeType.ANALYZER, "neural_epochs"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_OUT_TABLE,),
    ),
    (NodeType.ANALYZER, "neural_spike"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_OUT_TABLE,),
    ),
    (NodeType.ANALYZER, "neural_calcium"): PortSet(
        inputs=(_NEURAL_IN_ANY,),
        outputs=(_NEURAL_OUT_TABLE,),
    ),
    (NodeType.DATA_LOADER, "recording"): PortSet(
        inputs=(),
        outputs=(_SI_RECORDING_OUT,),
    ),
    (NodeType.PREPROCESSOR, "preprocess"): PortSet(
        inputs=(_SI_RECORDING_IN,),
        outputs=(_SI_RECORDING_OUT,),
    ),
    (NodeType.PREPROCESSOR, "si_preprocess"): PortSet(
        inputs=(_SI_RECORDING_IN,),
        outputs=(_SI_RECORDING_OUT,),
    ),
    (NodeType.ANALYZER, "sort"): PortSet(
        inputs=(_SI_RECORDING_IN,),
        outputs=(_SI_SORTING_OUT,),
    ),
    (NodeType.ANALYZER, "si_sort"): PortSet(
        inputs=(_SI_RECORDING_IN,),
        outputs=(_SI_SORTING_OUT,),
    ),
    (NodeType.ANALYZER, "analyze"): PortSet(
        inputs=(
            NodePort(
                name="recording",
                label="Recording",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="SpikeInterface Recording (usually after preprocess)",
            ),
            NodePort(
                name="sorting",
                label="Sorting",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="SpikeInterface Sorting",
            ),
        ),
        outputs=(_SI_ANALYZER_OUT,),
    ),
    (NodeType.ANALYZER, "si_analyze"): PortSet(
        inputs=(
            NodePort(
                name="recording",
                label="Recording",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="SpikeInterface Recording (usually after preprocess)",
            ),
            NodePort(
                name="sorting",
                label="Sorting",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="SpikeInterface Sorting",
            ),
        ),
        outputs=(_SI_ANALYZER_OUT,),
    ),
    (NodeType.ANALYZER, "metrics"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(
            NodePort(
                name="analyzer",
                label="Analyzer",
                data_kind=DATA_KIND_SPIKE,
                description="SortingAnalyzer with quality_metrics computed",
            ),
            NodePort(
                name="metrics",
                label="Metrics",
                data_kind=DATA_KIND_TABLE,
                description="Per-unit quality metrics table",
            ),
        ),
    ),
    (NodeType.ANALYZER, "si_metrics"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(
            NodePort(
                name="analyzer",
                label="Analyzer",
                data_kind=DATA_KIND_SPIKE,
                description="SortingAnalyzer with quality_metrics computed",
            ),
            NodePort(
                name="metrics",
                label="Metrics",
                data_kind=DATA_KIND_TABLE,
                description="Per-unit quality metrics table",
            ),
        ),
    ),
    (NodeType.ANALYZER, "curate"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(_SI_ANALYZER_OUT,),
    ),
    (NodeType.ANALYZER, "si_curate"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(_SI_ANALYZER_OUT,),
    ),
    (NodeType.ANALYZER, "export"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(
            NodePort(
                name="output",
                label="Path",
                data_kind=DATA_KIND_TEXT,
                description="Export folder or NWB path",
            ),
        ),
    ),
    (NodeType.ANALYZER, "si_export"): PortSet(
        inputs=(_SI_ANALYZER_IN,),
        outputs=(
            NodePort(
                name="output",
                label="Path",
                data_kind=DATA_KIND_TEXT,
                description="Export folder or NWB path",
            ),
        ),
    ),
    (NodeType.ANALYZER, "compare"): PortSet(
        inputs=(
            NodePort(
                name="sorting1",
                label="Sorting A",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="First SpikeInterface Sorting",
            ),
            NodePort(
                name="sorting2",
                label="Sorting B",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="Second SpikeInterface Sorting",
            ),
        ),
        outputs=(
            NodePort(
                name="output",
                label="Comparison",
                data_kind=DATA_KIND_ANY,
                description="Sorting comparison result",
            ),
        ),
    ),
    (NodeType.ANALYZER, "si_compare"): PortSet(
        inputs=(
            NodePort(
                name="sorting1",
                label="Sorting A",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="First SpikeInterface Sorting",
            ),
            NodePort(
                name="sorting2",
                label="Sorting B",
                data_kind=DATA_KIND_SPIKE,
                required=True,
                description="Second SpikeInterface Sorting",
            ),
        ),
        outputs=(
            NodePort(
                name="output",
                label="Comparison",
                data_kind=DATA_KIND_ANY,
                description="Sorting comparison result",
            ),
        ),
    ),
}

#: Which metadata key names the variant of a node type, where one exists.
VARIANT_METADATA_KEYS: Dict[NodeType, str] = {
    NodeType.DATA_LOADER: "signal_type",
    NodeType.PREPROCESSOR: "processor_type",
    NodeType.ANALYZER: "analyzer_type",
    NodeType.MODEL_CALL: "provider",
}


def ports_for(
    node_type: Union["NodeType", str], metadata: Optional[Dict[str, Any]] = None
) -> PortSet:
    """Return the ports declared by a node of this type and metadata.

    This is the one place ports are derived.  It takes the raw ingredients a
    saved document already carries -- ``node_type`` and ``metadata`` -- so the
    validator can ask about a node it has not loaded, and a node loaded from a
    file written before ports existed still reports the right ones.

    Args:
        node_type: A :class:`NodeType` or its string value.
        metadata: The node's metadata, whose variant key (if any) selects an
            override.  An unrecognized variant falls back to the type default,
            matching how code generation treats an unrecognized variant.

    Raises:
        ValueError: If ``node_type`` is not a known node type.
    """
    if not isinstance(node_type, NodeType):
        try:
            node_type = NodeType(node_type)
        except ValueError:
            raise ValueError(f"Unknown node type: {node_type!r}") from None

    if metadata:
        # SpikeInterface stages use ``si_stage`` so a recording loader with
        # ``signal_type=spike`` does not inherit the CSV spike-timestamp ports.
        si_stage = metadata.get("si_stage")
        if isinstance(si_stage, str):
            override = VARIANT_PORTS.get((node_type, si_stage))
            if override is not None:
                return override

    variant_key = VARIANT_METADATA_KEYS.get(node_type)
    if variant_key and metadata:
        variant = metadata.get(variant_key)
        if isinstance(variant, str):
            override = VARIANT_PORTS.get((node_type, variant))
            if override is not None:
                return override

    if node_type == NodeType.CUSTOM_CODE and metadata:
        # Discovered library functions with no data-like first argument
        # (loaders, constructors) declare an output only.
        if metadata.get("has_data_input") is False:
            base = NODE_TYPE_PORTS.get(node_type, PortSet())
            return PortSet(inputs=(), outputs=base.outputs)

    return NODE_TYPE_PORTS.get(node_type, PortSet())


# --------------------------------------------------------------------------
# Node kind declarations.
#
# These live at module level so that the palette, the factories below and the
# CLI's ``describe`` command share a single source of truth.  The factories
# deep-copy the NodeParameter objects, so every node instance owns its
# parameters and editing one node cannot mutate these templates.
# --------------------------------------------------------------------------

DATA_LOADER_PARAMS: Dict[str, NodeParameter] = {
    "file_path": NodeParameter(
        name="file_path",
        param_type="file",
        description=("Local path or URI (file://, http(s), s3://, gs://) of the CSV"),
    ),
    "delimiter": NodeParameter(
        name="delimiter",
        param_type="string",
        default_value=",",
        description="Delimiter for CSV files",
    ),
}

NEURAL_LOADER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "eeg": {
        "label": "Load EEG",
        "description": "Load EEG (channels × time). CSV, NumPy, EDF or FIF.",
        "file_formats": ["csv", "npy", "edf", "fif", "nwb"],
        "sampling_rate": 250.0,
    },
    "lfp": {
        "label": "Load LFP",
        "description": "Load local field potential (channels × time).",
        "file_formats": ["csv", "npy"],
        "sampling_rate": 1000.0,
    },
    "spike": {
        "label": "Load Spike",
        "description": "Load spike timestamps (CSV/NumPy), optionally with unit ids.",
        "file_formats": ["csv", "npy"],
        "sampling_rate": 1.0,
        "spike_columns": True,
    },
    "calcium": {
        "label": "Load Calcium",
        "description": "Load calcium fluorescence traces (samples × ROIs).",
        "file_formats": ["csv", "npy"],
        "sampling_rate": 30.0,
    },
}


def _neural_loader_params(signal_type: str) -> Dict[str, NodeParameter]:
    """Parameters for a neural loader of the given signal type."""
    config = NEURAL_LOADER_CONFIGS[signal_type]
    params: Dict[str, NodeParameter] = {
        "file_path": NodeParameter(
            name="file_path",
            param_type="file",
            description=(
                "Local path or URI (file://, http(s), s3://, gs://). "
                ".nwb files and BIDS roots are routed to SI/MNE readers"
            ),
        ),
        "file_format": NodeParameter(
            name="file_format",
            param_type="dropdown",
            default_value="csv",
            options=list(config["file_formats"]),
            description=(
                "File format. EDF/FIF/NWB need MNE or SpikeInterface "
                "(pip install 'analysis-gui[eeg]' / '[spike]'). "
                "file_path may be a local path, file://, http(s), s3://, gs://"
            ),
        ),
        "sampling_rate": NodeParameter(
            name="sampling_rate",
            param_type="number",
            default_value=config["sampling_rate"],
            description="Sampling rate in Hz (unused for spike times in seconds)",
        ),
        "delimiter": NodeParameter(
            name="delimiter",
            param_type="string",
            default_value=",",
            description="Delimiter for CSV files",
        ),
    }
    if config.get("spike_columns"):
        params["time_column"] = NodeParameter(
            name="time_column",
            param_type="string",
            default_value="time",
            description="Column of spike timestamps",
        )
        params["unit_column"] = NodeParameter(
            name="unit_column",
            param_type="string",
            default_value="unit",
            description="Optional column of unit / cluster ids",
        )
    return params


PREPROCESSOR_CONFIGS: Dict[str, Dict[str, Any]] = {
    "normalize": {
        "label": "Normalize",
        "description": "Normalize numerical features",
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="minmax",
                options=["minmax", "zscore", "robust"],
                description="Normalization method",
            )
        },
    },
    "handle_missing": {
        "label": "Handle Missing Values",
        "description": "Handle missing values in data",
        "params": {
            "strategy": NodeParameter(
                name="strategy",
                param_type="dropdown",
                default_value="mean",
                options=["mean", "median", "drop", "forward_fill"],
                description="Strategy for handling missing values",
            )
        },
    },
    "feature_select": {
        "label": "Feature Selection",
        "description": "Select relevant features",
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="kbest",
                options=["kbest", "variance"],
                description="Feature selection method",
            ),
            "n_features": NodeParameter(
                name="n_features",
                param_type="number",
                default_value=10,
                description="Number of features to select (k-best)",
            ),
        },
    },
    "split": {
        "label": "Train/Test Split",
        "description": "Split data into train and test sets",
        "params": {
            "test_size": NodeParameter(
                name="test_size",
                param_type="number",
                default_value=0.2,
                description="Proportion of test data",
            ),
            "random_state": NodeParameter(
                name="random_state",
                param_type="number",
                default_value=42,
                description="Random seed for reproducibility",
            ),
        },
    },
    "neural_filter": {
        "label": "Neural Filter",
        "description": "Band-pass and optional notch filter for EEG or LFP",
        "params": {
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=250.0,
                description="Sampling rate in Hz",
            ),
            "low_hz": NodeParameter(
                name="low_hz",
                param_type="number",
                default_value=1.0,
                description="High-pass cutoff (Hz)",
            ),
            "high_hz": NodeParameter(
                name="high_hz",
                param_type="number",
                default_value=40.0,
                description="Low-pass cutoff (Hz)",
            ),
            "notch_hz": NodeParameter(
                name="notch_hz",
                param_type="number",
                default_value=0.0,
                description="Notch frequency in Hz (0 disables)",
            ),
        },
    },
    "neural_montage": {
        "label": "EEG Montage",
        "description": (
            "Set an MNE channel montage (standard_1020, …). "
            "Requires: pip install 'analysis-gui[eeg]'"
        ),
        "params": {
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=250.0,
                description="Sampling rate in Hz",
            ),
            "montage": NodeParameter(
                name="montage",
                param_type="string",
                default_value="standard_1020",
                description="MNE montage name",
            ),
        },
    },
    "neural_ica": {
        "label": "EEG ICA",
        "description": (
            "Fit and apply MNE ICA to EEG. Numpy filter/PSD stay available. "
            "Requires: pip install 'analysis-gui[eeg]'"
        ),
        "params": {
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=250.0,
                description="Sampling rate in Hz",
            ),
            "n_components": NodeParameter(
                name="n_components",
                param_type="number",
                default_value=0,
                description="ICA components (0 = MNE default)",
            ),
            "montage": NodeParameter(
                name="montage",
                param_type="string",
                default_value="",
                description="Optional MNE montage name applied before ICA",
            ),
        },
    },
    "si_preprocess": {
        "label": "SI Preprocess",
        "description": (
            "SpikeInterface preprocessing (bandpass_filter, highpass_filter, "
            "notch_filter, common_reference, whiten, phase_shift, "
            "blank_saturation, correct_motion). "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "preprocess"},
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="bandpass_filter",
                options=[
                    "bandpass_filter",
                    "highpass_filter",
                    "notch_filter",
                    "common_reference",
                    "whiten",
                    "phase_shift",
                    "blank_saturation",
                    "correct_motion",
                ],
                description="spikeinterface.preprocessing method",
            ),
            "freq_min": NodeParameter(
                name="freq_min",
                param_type="number",
                default_value=300.0,
                description="High-pass cutoff in Hz (bandpass/highpass)",
            ),
            "freq_max": NodeParameter(
                name="freq_max",
                param_type="number",
                default_value=6000.0,
                description="Low-pass cutoff in Hz (bandpass)",
            ),
            "notch_freq": NodeParameter(
                name="notch_freq",
                param_type="number",
                default_value=3000.0,
                description="Notch frequency in Hz",
            ),
            "notch_q": NodeParameter(
                name="notch_q",
                param_type="number",
                default_value=30.0,
                description="Notch filter Q",
            ),
            "reference": NodeParameter(
                name="reference",
                param_type="dropdown",
                default_value="global",
                options=["global", "single", "local"],
                description="common_reference mode",
            ),
        },
    },
}

ANALYZER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "correlation": {
        "label": "Correlation Analysis",
        "description": "Calculate correlation between features",
    },
    "clustering": {
        "label": "Clustering",
        "description": "Perform clustering analysis",
        "params": {
            "algorithm": NodeParameter(
                name="algorithm",
                param_type="dropdown",
                default_value="kmeans",
                options=["kmeans", "dbscan", "hierarchical"],
                description="Clustering algorithm",
            ),
            "n_clusters": NodeParameter(
                name="n_clusters",
                param_type="number",
                default_value=3,
                description="Number of clusters",
            ),
            "eps": NodeParameter(
                name="eps",
                param_type="number",
                default_value=0.5,
                description="DBSCAN neighborhood radius",
            ),
            "min_samples": NodeParameter(
                name="min_samples",
                param_type="number",
                default_value=5,
                description="DBSCAN minimum samples per cluster",
            ),
        },
    },
    "regression": {
        "label": "Regression Analysis",
        "description": "Perform regression analysis",
        "params": {
            "model_type": NodeParameter(
                name="model_type",
                param_type="dropdown",
                default_value="linear",
                options=["linear", "polynomial", "ridge"],
                description="Type of regression model",
            )
        },
    },
    "neural_spectrum": {
        "label": "Neural PSD",
        "description": "Welch power spectral density for EEG or LFP",
        "params": {
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=250.0,
                description="Sampling rate in Hz",
            ),
            "nperseg": NodeParameter(
                name="nperseg",
                param_type="number",
                default_value=256,
                description="Welch window length (samples)",
            ),
        },
    },
    "neural_spike": {
        "label": "Spike Statistics",
        "description": "Firing-rate / PSTH or inter-spike interval histogram",
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="psth",
                options=["psth", "isi"],
                description="Spike analysis",
            ),
            "bin_size": NodeParameter(
                name="bin_size",
                param_type="number",
                default_value=0.05,
                description="PSTH bin width in seconds",
            ),
            "t_start": NodeParameter(
                name="t_start",
                param_type="number",
                default_value=0.0,
                description="PSTH start time in seconds",
            ),
            "t_end": NodeParameter(
                name="t_end",
                param_type="number",
                default_value=0.0,
                description="PSTH end time in seconds (0 = last spike)",
            ),
            "n_bins": NodeParameter(
                name="n_bins",
                param_type="number",
                default_value=50,
                description="ISI histogram bins",
            ),
            "max_isi": NodeParameter(
                name="max_isi",
                param_type="number",
                default_value=0.0,
                description="ISI histogram upper bound in seconds (0 = auto)",
            ),
        },
    },
    "neural_calcium": {
        "label": "Calcium Analysis",
        "description": "ΔF/F traces or simple threshold event detection",
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="dff",
                options=["dff", "events"],
                description="Calcium analysis",
            ),
            "baseline_percentile": NodeParameter(
                name="baseline_percentile",
                param_type="number",
                default_value=10.0,
                description="Percentile used as F0 for ΔF/F",
            ),
            "threshold": NodeParameter(
                name="threshold",
                param_type="number",
                default_value=3.0,
                description="Event threshold in standard deviations",
            ),
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=30.0,
                description="Sampling rate in Hz (event times)",
            ),
        },
    },
    "neural_epochs": {
        "label": "EEG Epochs / ERP",
        "description": (
            "Epoch around events and average an ERP with MNE. "
            "Requires: pip install 'analysis-gui[eeg]'"
        ),
        "params": {
            "sampling_rate": NodeParameter(
                name="sampling_rate",
                param_type="number",
                default_value=250.0,
                description="Sampling rate in Hz",
            ),
            "tmin": NodeParameter(
                name="tmin",
                param_type="number",
                default_value=-0.2,
                description="Epoch start relative to event (seconds)",
            ),
            "tmax": NodeParameter(
                name="tmax",
                param_type="number",
                default_value=0.5,
                description="Epoch end relative to event (seconds)",
            ),
            "event_id": NodeParameter(
                name="event_id",
                param_type="number",
                default_value=1,
                description="MNE event id",
            ),
            "event_column": NodeParameter(
                name="event_column",
                param_type="string",
                default_value="",
                description="Optional 0/1 stim column or sample-index column",
            ),
            "montage": NodeParameter(
                name="montage",
                param_type="string",
                default_value="",
                description="Optional MNE montage name",
            ),
        },
    },
    "si_sort": {
        "label": "SI Sort",
        "description": (
            "Run spikeinterface.sorters.run_sorter. Sorter binaries "
            "(GPU Kilosort, MATLAB, …) are not vendored. "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "sort"},
        "params": {
            "sorter_name": NodeParameter(
                name="sorter_name",
                param_type="dropdown",
                default_value="simple",
                options=[
                    "kilosort4",
                    "mountainsort5",
                    "spykingcircus2",
                    "tridesclous",
                    "herdingspikes",
                    "simple",
                ],
                description="Name passed to run_sorter (simple is a threshold sorter)",
            ),
            "folder": NodeParameter(
                name="folder",
                param_type="string",
                default_value="si_sorter_output",
                description="Output folder for the sorter",
            ),
        },
    },
    "si_analyze": {
        "label": "SI Analyzer",
        "description": (
            "Create a SortingAnalyzer and compute waveforms/templates "
            "(spikeinterface.core.create_sorting_analyzer). "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "analyze"},
        "params": {
            "extensions": NodeParameter(
                name="extensions",
                param_type="string",
                default_value="random_spikes,waveforms,templates,noise_levels",
                description="Comma-separated SortingAnalyzer extensions",
            ),
        },
    },
    "si_metrics": {
        "label": "SI Quality Metrics",
        "description": (
            "Compute SNR, ISI violation, presence ratio, firing rate, "
            "and isolation distance via SortingAnalyzer quality_metrics. "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "metrics"},
        "params": {
            "metric_names": NodeParameter(
                name="metric_names",
                param_type="string",
                default_value=(
                    "snr,isi_violation,presence_ratio,firing_rate,isolation_distance"
                ),
                description="Comma-separated spikeinterface quality metric names",
            ),
        },
    },
    "si_curate": {
        "label": "SI Curate",
        "description": (
            "Remove units that fail quality-metric thresholds "
            "(SortingAnalyzer.select_units). "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "curate"},
        "params": {
            "snr_min": NodeParameter(
                name="snr_min",
                param_type="number",
                default_value=5.0,
                description="Minimum SNR",
            ),
            "isi_violations_max": NodeParameter(
                name="isi_violations_max",
                param_type="number",
                default_value=0.2,
                description="Maximum ISI violations ratio",
            ),
            "presence_ratio_min": NodeParameter(
                name="presence_ratio_min",
                param_type="number",
                default_value=0.9,
                description="Minimum presence ratio",
            ),
            "firing_rate_min": NodeParameter(
                name="firing_rate_min",
                param_type="number",
                default_value=0.1,
                description="Minimum firing rate (Hz)",
            ),
            "isolation_distance_min": NodeParameter(
                name="isolation_distance_min",
                param_type="number",
                default_value=0.0,
                description="Minimum isolation distance (0 disables)",
            ),
        },
    },
    "si_export": {
        "label": "SI Export",
        "description": (
            "Export to Phy, NWB (neuroconv), SortingView (emits a "
            "plot_sorting_summary call; no embedded webview), or "
            "spikeinterface.exporters.export_report. "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "export"},
        "params": {
            "method": NodeParameter(
                name="method",
                param_type="dropdown",
                default_value="phy",
                options=["phy", "nwb", "sortingview", "report"],
                description="Export target (sortingview / report emit SI calls)",
            ),
            "output_path": NodeParameter(
                name="output_path",
                param_type="string",
                default_value="si_export",
                description="Phy folder or NWB file path",
            ),
        },
    },
    "si_compare": {
        "label": "SI Compare",
        "description": (
            "Compare two sortings (spikeinterface.comparison.compare_two_sorters). "
            "Requires: pip install 'analysis-gui[spike]'"
        ),
        "metadata": {**SI_NODE_METADATA, "si_stage": "compare"},
        "params": {
            "delta_time": NodeParameter(
                name="delta_time",
                param_type="number",
                default_value=0.4,
                description="Matching delta time in ms",
            ),
            "match_score": NodeParameter(
                name="match_score",
                param_type="number",
                default_value=0.5,
                description="Minimum agreement score to match units",
            ),
        },
    },
}

MODEL_PROVIDER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "description": "Call Claude API for analysis",
        "params": {
            "prompt": NodeParameter(
                name="prompt", param_type="string", description="Prompt for Claude"
            ),
            "model": NodeParameter(
                name="model",
                param_type="dropdown",
                default_value="claude-sonnet-5",
                options=[
                    "claude-opus-5",
                    "claude-sonnet-5",
                    "claude-haiku-4-5",
                ],
                description="Claude model version",
            ),
        },
    },
    "gpt": {
        "label": "GPT (OpenAI)",
        "description": "Call GPT API for analysis",
        "params": {
            "prompt": NodeParameter(
                name="prompt", param_type="string", description="Prompt for GPT"
            ),
            "model": NodeParameter(
                name="model",
                param_type="dropdown",
                default_value="gpt-4.1",
                options=["gpt-5.6-sol", "gpt-4.1", "gpt-4o"],
                description="GPT model version",
            ),
        },
    },
}

SI_RECORDING_PARAMS: Dict[str, NodeParameter] = {
    "file_path": NodeParameter(
        name="file_path",
        param_type="file",
        description="Recording file or folder (SpikeGLX/Open Ephys directory, NWB, binary, BIDS root, or URI)",
    ),
    "format": NodeParameter(
        name="format",
        param_type="dropdown",
        default_value="binary",
        options=[
            "binary",
            "nwb",
            "spikeglx",
            "openephys",
            "intan",
            "blackrock",
            "neuralynx",
            "mearec",
            "bids",
        ],
        description=(
            "Extractor to call. Other SI formats can be set via custom_format "
            "(suffix of read_<format>, e.g. blackrock)"
        ),
    ),
    "custom_format": NodeParameter(
        name="custom_format",
        param_type="string",
        default_value="",
        description="Optional extractor suffix overriding format (read_<custom_format>)",
    ),
    "sampling_rate": NodeParameter(
        name="sampling_rate",
        param_type="number",
        default_value=30000.0,
        description="Sampling rate in Hz (binary extractor)",
    ),
    "num_channels": NodeParameter(
        name="num_channels",
        param_type="number",
        default_value=0,
        description="Channel count for binary (0 = omit, SI infers when possible)",
    ),
    "dtype": NodeParameter(
        name="dtype",
        param_type="string",
        default_value="int16",
        description="Sample dtype for binary recordings",
    ),
}


def _config_metadata(base: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Merge optional config metadata onto a factory's base metadata dict."""
    extra = config.get("metadata")
    if not extra:
        return base
    merged = dict(base)
    merged.update(extra)
    return merged


CUSTOM_CODE_PARAMS: Dict[str, NodeParameter] = {
    "function_name": NodeParameter(
        name="function_name",
        param_type="string",
        description="Name of the function to call",
    ),
    "module": NodeParameter(
        name="module",
        param_type="string",
        description="Importable module that contains the function",
    ),
    "library_root": NodeParameter(
        name="library_root",
        param_type="string",
        description="Directory added to sys.path so the module imports",
    ),
}


def _instantiate_params(params: Dict[str, NodeParameter]) -> Dict[str, NodeParameter]:
    """Copy parameter templates so each node owns its own parameter objects."""
    return copy.deepcopy(params)


@dataclass
class Node:
    """A single node in the pipeline."""

    id: str
    node_type: NodeType
    label: str
    description: str = ""
    parameters: Dict[str, NodeParameter] = field(default_factory=dict)
    position: tuple = field(default=(0, 0))  # (x, y) coordinates for UI
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Generate unique ID if not provided."""
        if not self.id:
            self.id = str(uuid.uuid4())

    @classmethod
    def create_data_loader(cls, file_format: str = "csv"):
        """Create a data loader node."""
        return cls(
            id="",
            node_type=NodeType.DATA_LOADER,
            label=f"Load {file_format.upper()}",
            description=f"Load data from {file_format.upper()} file",
            parameters=_instantiate_params(DATA_LOADER_PARAMS),
            metadata={"file_format": file_format},
        )

    @classmethod
    def create_neural_loader(cls, signal_type: str = "eeg"):
        """Create a neural recording loader for ``signal_type``.

        ``signal_type`` is stored as ``metadata.signal_type`` (parallel to
        ``metadata.provider`` on model-call nodes) and selects the palette
        kind, default sampling rate and output ``data_kind``.
        """
        if signal_type not in NEURAL_LOADER_CONFIGS:
            raise ValueError(
                f"Unknown neural signal type: {signal_type!r}. "
                f"Expected one of: {', '.join(NEURAL_LOADER_CONFIGS)}"
            )
        config = NEURAL_LOADER_CONFIGS[signal_type]
        return cls(
            id="",
            node_type=NodeType.DATA_LOADER,
            label=config["label"],
            description=config["description"],
            parameters=_instantiate_params(_neural_loader_params(signal_type)),
            metadata={"file_format": "csv", "signal_type": signal_type},
        )

    @classmethod
    def create_si_recording(cls):
        """Create a SpikeInterface recording loader.

        Emits ``signal_type=spike`` plus SI-specific metadata so codegen
        calls extractors instead of :func:`load_neural`.
        """
        return cls(
            id="",
            node_type=NodeType.DATA_LOADER,
            label="SI Recording",
            description=(
                "Load a SpikeInterface recording (binary, NWB, SpikeGLX, "
                "Open Ephys, Intan; other extractors via custom_format). "
                "Requires: pip install 'analysis-gui[spike]'"
            ),
            parameters=_instantiate_params(SI_RECORDING_PARAMS),
            metadata={**SI_NODE_METADATA, "si_stage": "recording"},
        )

    @classmethod
    def create_preprocessor(cls, processor_type: str):
        """Create a preprocessing node."""
        config = PREPROCESSOR_CONFIGS.get(processor_type, {})
        return cls(
            id="",
            node_type=NodeType.PREPROCESSOR,
            label=config.get("label", "Preprocess"),
            description=config.get("description", ""),
            parameters=_instantiate_params(config.get("params", {})),
            metadata=_config_metadata({"processor_type": processor_type}, config),
        )

    @classmethod
    def create_analyzer(cls, analyzer_type: str):
        """Create an analysis node."""
        config = ANALYZER_CONFIGS.get(analyzer_type, {})
        return cls(
            id="",
            node_type=NodeType.ANALYZER,
            label=config.get("label", "Analyze"),
            description=config.get("description", ""),
            parameters=_instantiate_params(config.get("params", {})),
            metadata=_config_metadata({"analyzer_type": analyzer_type}, config),
        )

    @classmethod
    def create_visualizer(cls):
        """Create a visualization node."""
        return cls(
            id="",
            node_type=NodeType.VISUALIZER,
            label="Visualization",
            description="Visualize data",
        )

    @classmethod
    def create_model_call(cls, model_provider: str):
        """Create a model API call node."""
        config = MODEL_PROVIDER_CONFIGS.get(model_provider, {})
        return cls(
            id="",
            node_type=NodeType.MODEL_CALL,
            label=config.get("label", "Model Call"),
            description=config.get("description", ""),
            parameters=_instantiate_params(config.get("params", {})),
            metadata={"provider": model_provider},
        )

    @classmethod
    def create_custom_code(cls, repository_id: Optional[str] = None):
        """Create a custom code node."""
        params = _instantiate_params(CUSTOM_CODE_PARAMS)

        if repository_id:
            params["repository_id"] = NodeParameter(
                name="repository_id",
                param_type="string",
                default_value=repository_id,
                description="ID of the user repository",
            )

        return cls(
            id="",
            node_type=NodeType.CUSTOM_CODE,
            label="Custom Code",
            description="Execute custom code from repository",
            parameters=params,
        )

    @classmethod
    def create_from_kind(cls, kind: str) -> "Node":
        """Create a node from a node-kind key declared in :data:`NODE_KINDS`.

        Args:
            kind: A key of :data:`NODE_KINDS`, e.g. ``"preprocessor_split"``

        Raises:
            ValueError: If the kind is not registered.
        """
        spec = NODE_KINDS.get(kind)
        if spec is None:
            raise ValueError(f"Unknown node kind: {kind}")
        return spec.factory()

    @property
    def ports(self) -> PortSet:
        """The ports this node declares, derived from its type and metadata.

        Derived rather than stored so a node loaded from a document written
        before ports existed still reports the ports its kind declares today.
        """
        return ports_for(self.node_type, self.metadata)

    @property
    def input_ports(self) -> Tuple[NodePort, ...]:
        """The input ports this node declares."""
        return self.ports.inputs

    @property
    def output_ports(self) -> Tuple[NodePort, ...]:
        """The output ports this node declares."""
        return self.ports.outputs

    def get_parameter_value(self, name: str, default: Any = None) -> Any:
        """Return a parameter's resolved value, or ``default`` if absent."""
        param = self.parameters.get(name)
        if param is None:
            return default
        resolved = param.resolved_value
        return default if resolved is None else resolved

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary for serialization."""
        return {
            "id": self.id,
            "node_type": self.node_type.value,
            "label": self.label,
            "description": self.description,
            "parameters": {
                name: param.to_dict() for name, param in self.parameters.items()
            },
            "position": list(self.position),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        """Create node from dictionary."""
        params = {
            name: NodeParameter.from_dict(param_data)
            for name, param_data in data.get("parameters", {}).items()
        }

        return cls(
            id=data["id"],
            node_type=NodeType(data["node_type"]),
            label=data.get("label", ""),
            description=data.get("description", ""),
            parameters=params,
            position=tuple(data.get("position", (0, 0))),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class NodeKind:
    """A constructible kind of node, as offered to the user.

    ``kind`` is the stable string identifier used by the palette, by saved
    pipelines' UI state and by the CLI.  ``in_palette`` is False for kinds that
    are constructible but deliberately not offered in the PyQt palette yet.
    """

    kind: str
    palette_label: str
    node_type: NodeType
    factory: Callable[[], Node]
    in_palette: bool = True

    @property
    def ports(self) -> PortSet:
        """The ports a node of this kind declares.

        A property rather than a field: a field would let a kind's declaration
        disagree with the node its factory actually builds, which is the drift
        :func:`describe_node_kinds` exists to prevent.
        """
        return self.factory().ports


NODE_KINDS: Dict[str, NodeKind] = {
    "data_loader": NodeKind(
        kind="data_loader",
        palette_label="Load CSV",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_data_loader("csv"),
    ),
    "neural_loader_eeg": NodeKind(
        kind="neural_loader_eeg",
        palette_label="Load EEG",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_neural_loader("eeg"),
    ),
    "neural_loader_lfp": NodeKind(
        kind="neural_loader_lfp",
        palette_label="Load LFP",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_neural_loader("lfp"),
    ),
    "neural_loader_spike": NodeKind(
        kind="neural_loader_spike",
        palette_label="Load Spike",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_neural_loader("spike"),
    ),
    "neural_loader_calcium": NodeKind(
        kind="neural_loader_calcium",
        palette_label="Load Calcium",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_neural_loader("calcium"),
    ),
    "preprocessor_normalize": NodeKind(
        kind="preprocessor_normalize",
        palette_label="Normalize",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("normalize"),
    ),
    "preprocessor_missing": NodeKind(
        kind="preprocessor_missing",
        palette_label="Handle Missing",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("handle_missing"),
    ),
    "preprocessor_split": NodeKind(
        kind="preprocessor_split",
        palette_label="Train/Test Split",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("split"),
    ),
    "preprocessor_feature_select": NodeKind(
        kind="preprocessor_feature_select",
        palette_label="Feature Selection",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("feature_select"),
    ),
    "preprocessor_neural_filter": NodeKind(
        kind="preprocessor_neural_filter",
        palette_label="Neural Filter",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("neural_filter"),
    ),
    "preprocessor_neural_montage": NodeKind(
        kind="preprocessor_neural_montage",
        palette_label="EEG Montage",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("neural_montage"),
    ),
    "preprocessor_neural_ica": NodeKind(
        kind="preprocessor_neural_ica",
        palette_label="EEG ICA",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("neural_ica"),
    ),
    "neural_si_recording": NodeKind(
        kind="neural_si_recording",
        palette_label="SI Recording",
        node_type=NodeType.DATA_LOADER,
        factory=lambda: Node.create_si_recording(),
    ),
    "preprocessor_neural_si": NodeKind(
        kind="preprocessor_neural_si",
        palette_label="SI Preprocess",
        node_type=NodeType.PREPROCESSOR,
        factory=lambda: Node.create_preprocessor("si_preprocess"),
    ),
    "analyzer_neural_si_sort": NodeKind(
        kind="analyzer_neural_si_sort",
        palette_label="SI Sort",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_sort"),
    ),
    "analyzer_neural_si_analyze": NodeKind(
        kind="analyzer_neural_si_analyze",
        palette_label="SI Analyzer",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_analyze"),
    ),
    "analyzer_neural_si_metrics": NodeKind(
        kind="analyzer_neural_si_metrics",
        palette_label="SI Quality Metrics",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_metrics"),
    ),
    "analyzer_neural_si_curate": NodeKind(
        kind="analyzer_neural_si_curate",
        palette_label="SI Curate",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_curate"),
    ),
    "analyzer_neural_si_export": NodeKind(
        kind="analyzer_neural_si_export",
        palette_label="SI Export",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_export"),
    ),
    "analyzer_neural_si_compare": NodeKind(
        kind="analyzer_neural_si_compare",
        palette_label="SI Compare",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("si_compare"),
    ),
    "analyzer_correlation": NodeKind(
        kind="analyzer_correlation",
        palette_label="Correlation Analysis",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("correlation"),
    ),
    "analyzer_clustering": NodeKind(
        kind="analyzer_clustering",
        palette_label="Clustering",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("clustering"),
    ),
    "analyzer_regression": NodeKind(
        kind="analyzer_regression",
        palette_label="Regression",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("regression"),
    ),
    "analyzer_neural_spectrum": NodeKind(
        kind="analyzer_neural_spectrum",
        palette_label="Neural PSD",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("neural_spectrum"),
    ),
    "analyzer_neural_epochs": NodeKind(
        kind="analyzer_neural_epochs",
        palette_label="EEG Epochs / ERP",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("neural_epochs"),
    ),
    "analyzer_neural_spike": NodeKind(
        kind="analyzer_neural_spike",
        palette_label="Spike Statistics",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("neural_spike"),
    ),
    "analyzer_neural_calcium": NodeKind(
        kind="analyzer_neural_calcium",
        palette_label="Calcium Analysis",
        node_type=NodeType.ANALYZER,
        factory=lambda: Node.create_analyzer("neural_calcium"),
    ),
    "visualizer": NodeKind(
        kind="visualizer",
        palette_label="Visualization",
        node_type=NodeType.VISUALIZER,
        factory=lambda: Node.create_visualizer(),
    ),
    "model_claude": NodeKind(
        kind="model_claude",
        palette_label="Call Claude",
        node_type=NodeType.MODEL_CALL,
        factory=lambda: Node.create_model_call("claude"),
    ),
    "model_gpt": NodeKind(
        kind="model_gpt",
        palette_label="Call GPT",
        node_type=NodeType.MODEL_CALL,
        factory=lambda: Node.create_model_call("gpt"),
    ),
    "custom_code": NodeKind(
        kind="custom_code",
        palette_label="Custom Code",
        node_type=NodeType.CUSTOM_CODE,
        factory=lambda: Node.create_custom_code(),
    ),
}


def describe_node_kinds() -> List[Dict[str, Any]]:
    """Describe every constructible node kind as JSON-serializable data.

    Each kind is described by actually building it, so the description can
    never disagree with what the factories produce.  That includes ``ports``,
    which is read off the constructed node: a client can render a node's
    handles from this output alone.
    """
    described = []
    for kind, spec in NODE_KINDS.items():
        node = spec.factory()
        described.append(
            {
                "kind": kind,
                "palette_label": spec.palette_label,
                "in_palette": spec.in_palette,
                "node_type": spec.node_type.value,
                "label": node.label,
                "description": node.description,
                "metadata": node.metadata,
                "parameters": [param.to_dict() for param in node.parameters.values()],
                "ports": node.ports.to_dict(),
            }
        )
    return described
