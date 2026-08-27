"""Generate executable Python code from pipeline graphs."""

import ast
import builtins
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Set
from .graph import Edge, PipelineGraph
from .node import Node, NodeType


class CodeGenerator:
    """Generates executable Python code from a pipeline.

    Each node is assigned one variable, ``output_<i>``.  A node that declares
    several output ports must assign a dict keyed by those port names, because
    that is how a downstream node addresses one of them (see
    :meth:`_output_expression`).  Train/Test Split and SI Quality Metrics
    are the multi-output nodes today.
    """

    def __init__(self, graph: PipelineGraph):
        """
        Initialize the code generator.

        Args:
            graph: The pipeline graph to generate code from
        """
        self.graph = graph

    def generate(self) -> str:
        """
        Generate executable Python code from the pipeline.

        Returns:
            Python code as a string
        """
        is_valid, error = self.graph.is_valid()
        if not is_valid:
            raise ValueError(f"Cannot generate code: {error}")

        lines = self._generate_imports()
        lines.append("")
        lines.append("# Generated pipeline code")
        lines.append("")

        # Generate variable assignments for each node
        order = self.graph.get_topological_order()
        node_var_map = {}  # Maps node_id to variable name

        for i, node_id in enumerate(order):
            node = self.graph.get_node(node_id)
            var_name = f"output_{i}"
            node_var_map[node_id] = var_name

            node_code = self._generate_node_code(node, node_id, node_var_map)
            lines.extend(node_code)
            lines.append("")

        # Add execution guard
        lines.append("")
        lines.append("if __name__ == '__main__':")
        lines.append("    print('Pipeline executed successfully')")

        return "\n".join(lines)

    def _generate_imports(self) -> List[str]:
        """Generate import statements based on node types."""
        imports = set()
        imports.add("import pandas as pd")
        imports.add("import numpy as np")

        needs_complete = False
        needs_preview = False
        neural_names = set()
        si_names = set()
        mne_names = set()
        needs_uri_resolve = False
        needs_sklearn_analyzer = False
        for node in self.graph.nodes.values():
            if node.metadata.get("backend") == "spikeinterface":
                si_names.update(_si_helper_names(node))
                continue
            if node.node_type == NodeType.DATA_LOADER:
                if not node.metadata.get("signal_type") and _node_uses_uri(node):
                    needs_uri_resolve = True
                if node.metadata.get("signal_type"):
                    neural_names.add("load_neural")
            elif node.node_type == NodeType.PREPROCESSOR:
                processor = node.metadata.get("processor_type", "")
                if processor == "neural_filter":
                    neural_names.add("bandpass_filter")
                elif processor == "neural_montage":
                    mne_names.add("set_montage")
                elif processor == "neural_ica":
                    mne_names.add("fit_ica")
            elif node.node_type == NodeType.ANALYZER:
                analyzer_type = node.metadata.get("analyzer_type", "")
                if analyzer_type == "neural_spectrum":
                    neural_names.add("welch_psd")
                elif analyzer_type == "neural_spike":
                    method = node.get_parameter_value("method", "psth")
                    neural_names.add("isi_histogram" if method == "isi" else "psth")
                elif analyzer_type == "neural_epochs":
                    mne_names.add("epoch_erp")
                elif analyzer_type == "neural_calcium":
                    method = node.get_parameter_value("method", "dff")
                    neural_names.add(
                        "detect_threshold_events"
                        if method == "events"
                        else "delta_f_over_f"
                    )
                else:
                    needs_sklearn_analyzer = True
            elif node.node_type == NodeType.VISUALIZER:
                imports.add("import matplotlib.pyplot as plt")
            elif node.node_type == NodeType.MODEL_CALL:
                provider = node.metadata.get("provider", "")
                if provider in ("claude", "gpt"):
                    needs_complete = True
                    if self.graph.get_incoming_edges(node.id):
                        needs_preview = True

        if needs_sklearn_analyzer:
            imports.add("from sklearn.preprocessing import StandardScaler")
            imports.add("from sklearn.cluster import KMeans")
            imports.add("from sklearn.linear_model import LinearRegression")

        if neural_names:
            imports.add(
                "from analysis_gui.neural import " + ", ".join(sorted(neural_names))
            )

        if si_names:
            imports.add(
                "from analysis_gui.neural.spikeinterface_nodes import "
                + ", ".join(sorted(si_names))
            )

        if mne_names:
            imports.add(
                "from analysis_gui.neural.mne_nodes import "
                + ", ".join(sorted(mne_names))
            )

        if needs_uri_resolve:
            imports.add("from analysis_gui.utils.uris import resolve_data_uri")

        if needs_preview:
            imports.add("from analysis_gui.models import complete, preview_for_prompt")
        elif needs_complete:
            imports.add("from analysis_gui.models import complete")

        return sorted(list(imports))

    def _generate_node_code(
        self, node, node_id: str, node_var_map: Dict[str, str]
    ) -> List[str]:
        """Generate code for a specific node.

        The value a node consumes comes from the edge arriving at its input
        port, and names the specific output port the edge leaves from, so two
        downstream nodes can read two different outputs of the same upstream
        node.
        """
        lines = []
        var_name = node_var_map[node_id]

        if node.node_type == NodeType.DATA_LOADER:
            lines.extend(self._generate_data_loader(node, var_name))
            return lines

        pred_var = self._input_expression(node, node_id, node_var_map)

        if node.node_type == NodeType.PREPROCESSOR:
            lines.extend(
                self._generate_preprocessor(
                    node, var_name, pred_var, node_id, node_var_map
                )
            )

        elif node.node_type == NodeType.ANALYZER:
            lines.extend(
                self._generate_analyzer(node, var_name, pred_var, node_id, node_var_map)
            )

        elif node.node_type == NodeType.VISUALIZER:
            lines.extend(self._generate_visualizer(node, var_name, pred_var))

        elif node.node_type == NodeType.MODEL_CALL:
            port_name = node.ports.inputs[0].name if node.ports.inputs else None
            incoming = self._incoming_edge_for(node, node_id, port_name)
            connected = incoming is not None and incoming.source in node_var_map
            lines.extend(self._generate_model_call(node, var_name, pred_var, connected))

        elif node.node_type == NodeType.CUSTOM_CODE:
            if node.metadata.get("chunk_kind") == "block":
                lines.extend(self._generate_block_chunk(node, var_name, pred_var))
            else:
                lines.extend(self._generate_custom_code(node, var_name, pred_var))

        return lines

    def _input_expression(
        self, node: Node, node_id: str, node_var_map: Dict[str, str]
    ) -> str:
        """Return the expression feeding this node's input port.

        Falls back to the bare name ``data`` when nothing is connected, which
        is what an unconnected node generated before ports existed.
        """
        ports = node.ports
        port_name = ports.inputs[0].name if ports.inputs else None
        edge = self._incoming_edge_for(node, node_id, port_name)

        if edge is None or edge.source not in node_var_map:
            return "data"
        return self._output_expression(edge.source, edge.source_port, node_var_map)

    def _incoming_edge_for(
        self, node: Node, node_id: str, port_name: Optional[str]
    ) -> Optional[Edge]:
        """Find the edge that feeds ``port_name`` on this node.

        Code generation is deliberately more tolerant than validation: an edge
        whose target port is unknown or ambiguous still feeds the node, exactly
        as the first predecessor did before ports existed, so an unvalidated or
        legacy document generates the same code it always has.
        """
        incoming = self.graph.get_incoming_edges(node_id)
        if not incoming:
            return None

        if port_name is not None:
            for edge in incoming:
                resolved = node.ports.resolve_input(edge.target_port).port
                if resolved is not None and resolved.name == port_name:
                    return edge
            if len(node.ports.inputs) > 1:
                return None

        return incoming[0]

    def _output_expression(
        self, source_id: str, source_port: Optional[str], node_var_map: Dict[str, str]
    ) -> str:
        """Return the expression reading ``source_port`` off the source node.

        A node with several output ports assigns a dict keyed by port name, so
        one output is a subscript of its variable.  A node with a single output
        (or an edge that does not name one of several) reads the whole
        variable.
        """
        var_name = node_var_map[source_id]
        source = self.graph.get_node(source_id)
        if source is None:
            return var_name

        outputs = source.ports.outputs
        if len(outputs) < 2:
            return var_name

        port = source.ports.resolve_output(source_port).port
        if port is None:
            return var_name
        return f"{var_name}[{port.name!r}]"

    def _generate_data_loader(self, node, var_name: str) -> List[str]:
        """Generate code for data loading node."""
        if node.metadata.get("backend") == "spikeinterface":
            return self._generate_si_recording(node, var_name)

        signal_type = node.metadata.get("signal_type")
        if signal_type:
            return self._generate_neural_loader(node, var_name, signal_type)

        file_format = node.metadata.get("file_format", "csv")
        file_path = node.get_parameter_value("file_path", "data.csv")
        delimiter = node.get_parameter_value("delimiter", ",")

        # Arguments are assembled before the call is rendered; appending to the
        # rendered line would put the delimiter outside the closing paren.
        path_expr = _path_expression(file_path)
        args = [path_expr]
        if file_format == "csv" and delimiter:
            args.append(f"delimiter='{delimiter}'")

        lines = [
            f"# Load {file_format.upper()} data",
            f"{var_name} = pd.read_csv({', '.join(args)})",
        ]

        lines.append(f"print(f'Loaded data shape: {{{var_name}.shape}}')")

        return lines

    def _generate_neural_loader(
        self, node, var_name: str, signal_type: str
    ) -> List[str]:
        """Generate code for a typed neural recording loader."""
        file_format = node.get_parameter_value(
            "file_format", node.metadata.get("file_format", "csv")
        )
        file_path = node.get_parameter_value("file_path", "data.csv")
        sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
        delimiter = node.get_parameter_value("delimiter", ",")
        args = [
            repr(file_path),
            f"signal_type={signal_type!r}",
            f"file_format={file_format!r}",
            f"sampling_rate={sampling_rate}",
        ]
        if file_format == "csv" and delimiter:
            args.append(f"delimiter={delimiter!r}")
        if signal_type == "spike":
            time_column = node.get_parameter_value("time_column", "time")
            unit_column = node.get_parameter_value("unit_column", "unit")
            args.append(f"time_column={time_column!r}")
            args.append(f"unit_column={unit_column!r}")
        return [
            f"# Load {signal_type.upper()} neural data",
            f"{var_name} = load_neural({', '.join(args)})",
            f"print(f'Loaded {signal_type} data shape: {{{var_name}.shape}}')",
        ]

    def _generate_si_recording(self, node, var_name: str) -> List[str]:
        """Generate code for a SpikeInterface recording loader."""
        file_path = node.get_parameter_value("file_path", "recording.bin")
        fmt = node.get_parameter_value("format", "binary")
        custom_format = node.get_parameter_value("custom_format", "")
        sampling_rate = node.get_parameter_value("sampling_rate", 30000.0)
        num_channels = node.get_parameter_value("num_channels", 0)
        dtype = node.get_parameter_value("dtype", "int16")
        args = [
            repr(file_path),
            f"format={fmt!r}",
            f"sampling_rate={sampling_rate}",
            f"num_channels={num_channels}",
            f"dtype={dtype!r}",
        ]
        if custom_format:
            args.append(f"custom_format={custom_format!r}")
        return [
            "# Load SpikeInterface recording "
            "(pip install 'analysis-gui[spike]' if spikeinterface is missing)",
            f"{var_name} = load_si_recording({', '.join(args)})",
        ]

    def _port_input(
        self,
        node: Node,
        node_id: str,
        node_var_map: Dict[str, str],
        port_name: str,
        fallback: str,
    ) -> str:
        """Expression feeding ``port_name``, or ``fallback`` if unconnected."""
        edge = self._incoming_edge_for(node, node_id, port_name)
        if edge is None or edge.source not in node_var_map:
            return fallback
        return self._output_expression(edge.source, edge.source_port, node_var_map)

    def _generate_preprocessor(
        self,
        node,
        var_name: str,
        pred_var: str,
        node_id: Optional[str] = None,
        node_var_map: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Generate code for preprocessing node."""
        processor_type = node.metadata.get("processor_type", "")
        lines = [f"# Preprocessing: {node.label}"]

        if processor_type == "normalize":
            method_val = node.get_parameter_value("method", "minmax")
            lines.append(f"{var_name} = {pred_var}.copy()")
            if method_val == "minmax":
                lines.append(
                    f"{var_name} = ({pred_var} - {pred_var}.min()) / ({pred_var}.max() - {pred_var}.min())"
                )
            elif method_val == "zscore":
                lines.append(
                    f"{var_name} = ({pred_var} - {pred_var}.mean()) / {pred_var}.std()"
                )
            elif method_val == "robust":
                lines.append(
                    f"{var_name} = ({pred_var} - {pred_var}.median()) / "
                    f"({pred_var}.quantile(0.75) - {pred_var}.quantile(0.25))"
                )

        elif processor_type == "handle_missing":
            strategy_val = node.get_parameter_value("strategy", "mean")
            lines.append(f"{var_name} = {pred_var}.copy()")
            if strategy_val == "mean":
                lines.append(f"{var_name} = {var_name}.fillna({pred_var}.mean())")
            elif strategy_val == "median":
                lines.append(f"{var_name} = {var_name}.fillna({pred_var}.median())")
            elif strategy_val == "drop":
                lines.append(f"{var_name} = {var_name}.dropna()")
            elif strategy_val == "forward_fill":
                lines.append(f"{var_name} = {var_name}.ffill()")

        elif processor_type == "feature_select":
            method_val = node.get_parameter_value("method", "kbest")
            n_val = node.get_parameter_value("n_features", 10)
            if method_val == "variance":
                lines.append("from sklearn.feature_selection import VarianceThreshold")
                lines.append("_selector = VarianceThreshold()")
                lines.append(
                    f"{var_name} = {pred_var}.loc[:, _selector.fit({pred_var}).get_support()]"
                )
            else:
                lines.append(
                    "from sklearn.feature_selection import SelectKBest, f_classif"
                )
                lines.append(f"_k = min(int({n_val}), max({pred_var}.shape[1] - 1, 1))")
                lines.append(
                    f"_X, _y = {pred_var}.iloc[:, :-1], {pred_var}.iloc[:, -1]"
                )
                lines.append("_selector = SelectKBest(f_classif, k=_k)")
                lines.append("_selector.fit(_X, _y)")
                lines.append(f"{var_name} = _X.loc[:, _selector.get_support()].copy()")
                lines.append(f"{var_name}[{pred_var}.columns[-1]] = _y")

        elif processor_type == "split":
            # This node declares four output ports, so its variable must be a
            # dict keyed by those port names: that is how a downstream node
            # addresses one of them.
            lines.append(f"from sklearn.model_selection import train_test_split")
            test_size_val = node.get_parameter_value("test_size", 0.2)
            random_state_val = node.get_parameter_value("random_state", 42)
            lines.append(f"X_train, X_test, y_train, y_test = train_test_split(")
            lines.append(f"    {pred_var}.iloc[:, :-1], {pred_var}.iloc[:, -1],")
            lines.append(
                f"    test_size={test_size_val}, random_state={random_state_val})"
            )
            lines.append(
                f"{var_name} = {{'X_train': X_train, 'X_test': X_test, 'y_train': y_train, 'y_test': y_test}}"
            )

        elif processor_type == "neural_filter":
            sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
            low_hz = node.get_parameter_value("low_hz", 1.0)
            high_hz = node.get_parameter_value("high_hz", 40.0)
            notch_hz = node.get_parameter_value("notch_hz", 0.0)
            lines.append(
                f"{var_name} = bandpass_filter({pred_var}, sampling_rate={sampling_rate}, "
                f"low_hz={low_hz}, high_hz={high_hz}, notch_hz={notch_hz})"
            )

        elif processor_type == "neural_montage":
            sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
            montage = node.get_parameter_value("montage", "standard_1020")
            lines.append(
                "# MNE montage (pip install 'analysis-gui[eeg]' if mne is missing)"
            )
            lines.append(
                f"{var_name} = set_montage({pred_var}, montage={montage!r}, "
                f"sampling_rate={sampling_rate})"
            )

        elif processor_type == "neural_ica":
            sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
            n_components = node.get_parameter_value("n_components", 0)
            montage = node.get_parameter_value("montage", "")
            lines.append(
                "# MNE ICA (pip install 'analysis-gui[eeg]' if mne is missing)"
            )
            lines.append(
                f"{var_name} = fit_ica({pred_var}, n_components={n_components}, "
                f"sampling_rate={sampling_rate}, montage={montage!r})"
            )

        elif processor_type == "si_preprocess":
            method = node.get_parameter_value("method", "bandpass_filter")
            freq_min = node.get_parameter_value("freq_min", 300.0)
            freq_max = node.get_parameter_value("freq_max", 6000.0)
            notch_freq = node.get_parameter_value("notch_freq", 3000.0)
            notch_q = node.get_parameter_value("notch_q", 30.0)
            reference = node.get_parameter_value("reference", "global")
            lines.append(
                f"{var_name} = preprocess_si({pred_var}, method={method!r}, "
                f"freq_min={freq_min}, freq_max={freq_max}, "
                f"notch_freq={notch_freq}, notch_q={notch_q}, "
                f"reference={reference!r})"
            )

        else:
            lines.append(f"{var_name} = {pred_var}")

        return lines

    def _generate_analyzer(
        self,
        node,
        var_name: str,
        pred_var: str,
        node_id: Optional[str] = None,
        node_var_map: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Generate code for analysis node."""
        analyzer_type = node.metadata.get("analyzer_type", "")
        lines = [f"# Analysis: {node.label}"]

        if analyzer_type in _SI_ANALYZER_TYPES:
            return self._generate_si_analyzer(
                node, var_name, pred_var, node_id, node_var_map
            )

        if analyzer_type == "correlation":
            lines.append(f"{var_name} = {pred_var}.corr()")

        elif analyzer_type == "clustering":
            alg_val = node.get_parameter_value("algorithm", "kmeans")
            n_val = node.get_parameter_value("n_clusters", 3)

            if alg_val == "kmeans":
                lines.append(f"from sklearn.cluster import KMeans")
                lines.append(f"kmeans = KMeans(n_clusters={n_val}, random_state=42)")
                lines.append(f"{var_name} = kmeans.fit_predict({pred_var})")
            elif alg_val == "dbscan":
                eps_val = node.get_parameter_value("eps", 0.5)
                min_samples_val = node.get_parameter_value("min_samples", 5)
                lines.append("from sklearn.cluster import DBSCAN")
                lines.append(
                    f"dbscan = DBSCAN(eps={eps_val}, min_samples={min_samples_val})"
                )
                lines.append(f"{var_name} = dbscan.fit_predict({pred_var})")
            elif alg_val == "hierarchical":
                lines.append("from sklearn.cluster import AgglomerativeClustering")
                lines.append(f"agg = AgglomerativeClustering(n_clusters={n_val})")
                lines.append(f"{var_name} = agg.fit_predict({pred_var})")
            else:
                lines.append(f"{var_name} = {pred_var}")

        elif analyzer_type == "regression":
            model_type = node.get_parameter_value("model_type", "linear")
            lines.append(f"_X, _y = {pred_var}.iloc[:, :-1], {pred_var}.iloc[:, -1]")
            if model_type == "ridge":
                lines.append("from sklearn.linear_model import Ridge")
                lines.append("reg = Ridge()")
                lines.append("reg.fit(_X, _y)")
                lines.append(f"{var_name} = reg.predict(_X)")
            elif model_type == "polynomial":
                lines.append("from sklearn.linear_model import LinearRegression")
                lines.append("from sklearn.preprocessing import PolynomialFeatures")
                lines.append("poly = PolynomialFeatures(degree=2)")
                lines.append("_X_poly = poly.fit_transform(_X)")
                lines.append("reg = LinearRegression()")
                lines.append("reg.fit(_X_poly, _y)")
                lines.append(f"{var_name} = reg.predict(_X_poly)")
            else:
                lines.append("from sklearn.linear_model import LinearRegression")
                lines.append("reg = LinearRegression()")
                lines.append("reg.fit(_X, _y)")
                lines.append(f"{var_name} = reg.predict(_X)")

        elif analyzer_type == "neural_spectrum":
            sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
            nperseg = node.get_parameter_value("nperseg", 256)
            lines.append(
                f"{var_name} = welch_psd({pred_var}, sampling_rate={sampling_rate}, "
                f"nperseg={nperseg})"
            )

        elif analyzer_type == "neural_epochs":
            sampling_rate = node.get_parameter_value("sampling_rate", 250.0)
            tmin = node.get_parameter_value("tmin", -0.2)
            tmax = node.get_parameter_value("tmax", 0.5)
            event_id = node.get_parameter_value("event_id", 1)
            event_column = node.get_parameter_value("event_column", "")
            montage = node.get_parameter_value("montage", "")
            lines.append(
                "# MNE epochs / ERP (pip install 'analysis-gui[eeg]' if mne is missing)"
            )
            lines.append(
                f"{var_name} = epoch_erp({pred_var}, sampling_rate={sampling_rate}, "
                f"tmin={tmin}, tmax={tmax}, event_id={event_id}, "
                f"event_column={event_column!r}, montage={montage!r})"
            )

        elif analyzer_type == "neural_spike":
            method = node.get_parameter_value("method", "psth")
            if method == "isi":
                n_bins = node.get_parameter_value("n_bins", 50)
                max_isi = node.get_parameter_value("max_isi", 0.0)
                lines.append(
                    f"{var_name} = isi_histogram({pred_var}, n_bins={n_bins}, "
                    f"max_isi={max_isi})"
                )
            else:
                bin_size = node.get_parameter_value("bin_size", 0.05)
                t_start = node.get_parameter_value("t_start", 0.0)
                t_end = node.get_parameter_value("t_end", 0.0)
                lines.append(
                    f"{var_name} = psth({pred_var}, bin_size={bin_size}, "
                    f"t_start={t_start}, t_end={t_end})"
                )

        elif analyzer_type == "neural_calcium":
            method = node.get_parameter_value("method", "dff")
            if method == "events":
                threshold = node.get_parameter_value("threshold", 3.0)
                sampling_rate = node.get_parameter_value("sampling_rate", 30.0)
                lines.append(
                    f"{var_name} = detect_threshold_events({pred_var}, "
                    f"threshold={threshold}, sampling_rate={sampling_rate})"
                )
            else:
                baseline = node.get_parameter_value("baseline_percentile", 10.0)
                lines.append(
                    f"{var_name} = delta_f_over_f({pred_var}, "
                    f"baseline_percentile={baseline})"
                )

        else:
            lines.append(f"{var_name} = {pred_var}")

        return lines

    def _generate_si_analyzer(
        self,
        node,
        var_name: str,
        pred_var: str,
        node_id: Optional[str],
        node_var_map: Optional[Dict[str, str]],
    ) -> List[str]:
        """Generate code for a SpikeInterface analyzer stage."""
        analyzer_type = node.metadata.get("analyzer_type", "")
        node_id = node_id or node.id
        node_var_map = node_var_map or {}
        lines = [
            f"# Analysis: {node.label} "
            "(pip install 'analysis-gui[spike]' if spikeinterface is missing)"
        ]

        if analyzer_type == "si_sort":
            sorter_name = node.get_parameter_value("sorter_name", "simple")
            folder = node.get_parameter_value("folder", "si_sorter_output")
            lines.append("# spikeinterface.sorters.run_sorter (binaries not vendored)")
            lines.append(
                f"{var_name} = run_si_sorter("
                f"{pred_var}, sorter_name={sorter_name!r}, folder={folder!r})"
            )
        elif analyzer_type == "si_analyze":
            recording = self._port_input(
                node, node_id, node_var_map, "recording", "recording"
            )
            sorting = self._port_input(
                node, node_id, node_var_map, "sorting", "sorting"
            )
            extensions = node.get_parameter_value(
                "extensions", "random_spikes,waveforms,templates,noise_levels"
            )
            lines.append(
                "# SortingAnalyzer (SI >= 0.101; WaveformExtractor was removed)"
            )
            lines.append(f"_sorting = {sorting}")
            lines.append(f"_recording = {recording}")
            lines.append(
                f"{var_name} = create_si_analyzer("
                f"_sorting, _recording, extensions={extensions!r})"
            )
        elif analyzer_type == "si_metrics":
            metric_names = node.get_parameter_value(
                "metric_names",
                "snr,isi_violation,presence_ratio,firing_rate,isolation_distance",
            )
            lines.append("# SortingAnalyzer.compute('quality_metrics')")
            lines.append(
                f"{var_name} = compute_si_metrics({pred_var}, "
                f"metric_names={metric_names!r})"
            )
        elif analyzer_type == "si_curate":
            snr_min = node.get_parameter_value("snr_min", 5.0)
            isi_max = node.get_parameter_value("isi_violations_max", 0.2)
            presence_min = node.get_parameter_value("presence_ratio_min", 0.9)
            rate_min = node.get_parameter_value("firing_rate_min", 0.1)
            isolation_min = node.get_parameter_value("isolation_distance_min", 0.0)
            lines.append(
                f"{var_name} = curate_si({pred_var}, snr_min={snr_min}, "
                f"isi_violations_max={isi_max}, "
                f"presence_ratio_min={presence_min}, "
                f"firing_rate_min={rate_min}, "
                f"isolation_distance_min={isolation_min})"
            )
        elif analyzer_type == "si_export":
            method = node.get_parameter_value("method", "phy")
            output_path = node.get_parameter_value("output_path", "si_export")
            lines.append(
                f"{var_name} = export_si({pred_var}, method={method!r}, "
                f"output_path={output_path!r})"
            )
        elif analyzer_type == "si_compare":
            sorting1 = self._port_input(
                node, node_id, node_var_map, "sorting1", "sorting1"
            )
            sorting2 = self._port_input(
                node, node_id, node_var_map, "sorting2", "sorting2"
            )
            delta_time = node.get_parameter_value("delta_time", 0.4)
            match_score = node.get_parameter_value("match_score", 0.5)
            lines.append(
                f"{var_name} = compare_si_sorters({sorting1}, {sorting2}, "
                f"delta_time={delta_time}, match_score={match_score})"
            )
        else:
            lines.append(f"{var_name} = {pred_var}")

        return lines

    def _generate_visualizer(self, node, var_name: str, pred_var: str) -> List[str]:
        """Generate code for visualization node."""
        lines = [
            f"# Visualization: {node.label}",
            f"import matplotlib.pyplot as plt",
            f"plt.figure(figsize=(10, 6))",
            f"plt.plot({pred_var})",
            f"plt.title('{node.label}')",
            f"plt.show()",
            f"{var_name} = '{node.label} plot generated'",
        ]
        return lines

    def _generate_model_call(
        self, node, var_name: str, pred_var: str, connected: bool = False
    ) -> List[str]:
        """Generate code for model API call node.

        Claude and GPT nodes call :func:`analysis_gui.models.complete` with
        the provider taken from ``metadata.provider`` and the node's
        ``prompt`` / ``model`` parameters.  When an incoming edge exists,
        the upstream value is passed as ``context`` after
        :func:`analysis_gui.models.preview_for_prompt`.  Open-weights remains
        a placeholder.
        """
        provider = node.metadata.get("provider", "")
        prompt = node.get_parameter_value("prompt", "Analyze this data")
        model = node.get_parameter_value("model")

        lines = [f"# Model Call: {node.label}"]

        if provider in ("claude", "gpt"):
            args = [repr(provider), repr(prompt)]
            if model is not None:
                args.append(f"model={model!r}")
            if connected:
                args.append(f"context=preview_for_prompt({pred_var})")
            lines.append(f"{var_name} = complete({', '.join(args)})")
        elif provider in ("open_weights", "ollama"):
            lines.append(
                f"{var_name} = 'Open weights model call is not implemented yet'"
            )
        else:
            lines.append(f"{var_name} = 'Model call not configured'")

        return lines

    def _generate_custom_code(self, node, var_name: str, pred_var: str) -> List[str]:
        """Generate code that imports and calls a real library function.

        The function body is never pasted into the script: the generated
        pipeline adds the library root to ``sys.path`` (when one is known)
        and imports the module.  That is how a discovered callable becomes
        a node without forking a copy of the user's source.
        """
        func_name = (
            node.get_parameter_value("function_name")
            or node.metadata.get("function")
            or "process"
        )
        module = node.get_parameter_value("module") or node.metadata.get("module") or ""
        library_root = (
            node.get_parameter_value("library_root")
            or node.metadata.get("library_root")
            or ""
        )
        source_path = node.metadata.get("source_path") or ""
        class_name = node.metadata.get("class_name")
        has_data = node.metadata.get("has_data_input", True)

        lines = [f"# Custom Code: {node.label}"]

        if library_root:
            lines.append("import sys")
            lines.append(f"_agui_root = {library_root!r}")
            lines.append("if _agui_root not in sys.path:")
            lines.append("    sys.path.insert(0, _agui_root)")

        callable_expr = func_name
        if module and _is_importable_module(module):
            if class_name:
                lines.append(f"from {module} import {class_name}")
                callable_expr = f"{class_name}().{func_name}"
            else:
                lines.append(f"from {module} import {func_name}")
        elif source_path:
            alias = f"_agui_{var_name}"
            lines.append("import importlib.util")
            lines.append(
                f"_spec = importlib.util.spec_from_file_location({alias!r}, {source_path!r})"
            )
            lines.append(f"{alias} = importlib.util.module_from_spec(_spec)")
            lines.append(f"_spec.loader.exec_module({alias})")
            if class_name:
                callable_expr = f"{alias}.{class_name}().{func_name}"
            else:
                callable_expr = f"{alias}.{func_name}"
        else:
            lines.append(f"# No module or source path; calling {func_name} by name")

        call_args: List[str] = []
        if has_data is not False:
            call_args.append(pred_var)
        for name, param in node.parameters.items():
            if name in _CUSTOM_CODE_CONTROL_PARAMS:
                continue
            value = param.resolved_value
            if value is None:
                continue
            call_args.append(f"{name}={value!r}")

        lines.append(f"{var_name} = {callable_expr}({', '.join(call_args)})")
        return lines

    def _generate_block_chunk(self, node, var_name: str, pred_var: str) -> List[str]:
        """Inline an AST-bounded snippet into a generated helper.

        The original repository file is not modified.  The helper is emitted
        only in pipeline output, with a header citing ``path:start-end``.
        """
        source_path = str(node.metadata.get("source_path") or "")
        start = int(node.metadata.get("lineno") or 0)
        end = int(node.metadata.get("end_lineno") or start)
        source_hash = str(node.metadata.get("source_hash") or "block")
        helper = _helper_name(source_hash)
        snippet = _read_source_span(source_path, start, end)
        if not snippet.strip():
            snippet = str(node.metadata.get("preview") or "pass")

        dedented = textwrap.dedent(snippet).strip("\n") or "pass"
        import_lines = _top_level_imports(source_path)
        alias = _data_alias_for(dedented)
        return_name = _return_name_for(dedented)

        lines = [
            f"# Custom Code: {node.label}",
            f"# from {source_path}:{start}-{end}",
            f"def {helper}(data, **params):",
        ]
        for import_line in import_lines:
            lines.append(f"    {import_line}")
        if import_lines:
            lines.append("")
        if alias and alias != "data":
            lines.append(f"    {alias} = data")
        for raw in dedented.splitlines() or ["pass"]:
            lines.append(f"    {raw}" if raw.strip() else "")
        if return_name:
            lines.append(f"    return {return_name}")

        call_args: List[str] = []
        if node.metadata.get("has_data_input", True) is not False:
            call_args.append(pred_var)
        for name, param in node.parameters.items():
            if name in _CUSTOM_CODE_CONTROL_PARAMS:
                continue
            value = param.resolved_value
            if value is None:
                continue
            call_args.append(f"{name}={value!r}")
        lines.append(f"{var_name} = {helper}({', '.join(call_args)})")
        return lines


_CUSTOM_CODE_CONTROL_PARAMS = frozenset(
    {"function_name", "module", "library_root", "repository_id"}
)

_URI_SCHEMES = ("file://", "http://", "https://", "s3://", "gs://")


def _node_uses_uri(node: Node) -> bool:
    path = node.get_parameter_value("file_path", "")
    return isinstance(path, str) and path.strip().lower().startswith(_URI_SCHEMES)


def _path_expression(file_path: str) -> str:
    """Render a file_path argument, wrapping remote / file:// URIs."""
    if isinstance(file_path, str) and file_path.strip().lower().startswith(
        _URI_SCHEMES
    ):
        return f"resolve_data_uri({file_path!r})"
    return repr(file_path)


_SI_ANALYZER_TYPES = frozenset(
    {
        "si_sort",
        "si_analyze",
        "si_metrics",
        "si_curate",
        "si_export",
        "si_compare",
    }
)

_SI_STAGE_HELPERS = {
    "recording": "load_si_recording",
    "preprocess": "preprocess_si",
    "sort": "run_si_sorter",
    "analyze": "create_si_analyzer",
    "metrics": "compute_si_metrics",
    "curate": "curate_si",
    "export": "export_si",
    "compare": "compare_si_sorters",
}


def _si_helper_names(node: Node) -> Set[str]:
    """Return SpikeInterface helper names a node needs imported."""
    stage = node.metadata.get("si_stage")
    helper = _SI_STAGE_HELPERS.get(stage)
    return {helper} if helper else set()


_SCI_AND_COMMON = frozenset(
    {
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "sklearn",
        "sk",
        "mne",
        "signal",
        "plt",
        "matplotlib",
        "pyplot",
        "sns",
        "seaborn",
        "params",
        "data",
    }
)
_BUILTIN_NAMES = set(dir(builtins))
_MAX_HELPER_IMPORTS = 20

_DATA_NAME_HINTS = (
    "data",
    "eeg",
    "signal",
    "trace",
    "arr",
    "df",
    "frame",
    "raw",
    "samples",
    "recording",
)


def _is_importable_module(name: str) -> bool:
    """True when ``name`` is a dotted sequence of Python identifiers."""
    return bool(name) and all(part.isidentifier() for part in name.split("."))


def _helper_name(source_hash: str) -> str:
    token = "".join(ch for ch in source_hash if ch.isalnum()) or "block"
    return f"chunk_{token[:12]}"


def _read_source_span(path: str, start: int, end: int) -> str:
    if not path or start < 1:
        return ""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return ""
    if end < start:
        end = start
    return "".join(lines[start - 1 : end])


def _top_level_imports(path: str) -> List[str]:
    if not path:
        return []
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                lines.append(ast.unparse(node))
            except Exception:
                continue
        if len(lines) >= _MAX_HELPER_IMPORTS:
            break
    return lines


def _return_name_for(snippet: str) -> Optional[str]:
    try:
        tree = ast.parse(snippet)
    except SyntaxError:
        return "data"
    if not tree.body:
        return "data"
    last = tree.body[-1]
    if isinstance(last, ast.Return):
        return None
    target = _assign_target_name(last)
    if target:
        return target
    return "data"


def _assign_target_name(node: ast.stmt) -> Optional[str]:
    if isinstance(node, ast.Assign) and node.targets:
        target = node.targets[-1]
        if isinstance(target, ast.Name):
            return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _data_alias_for(snippet: str) -> Optional[str]:
    try:
        tree = ast.parse(snippet)
    except SyntaxError:
        return None
    unbound = _unbound_names(tree)
    for name in unbound:
        lowered = name.lower()
        if name == "data":
            return None
        if any(hint in lowered for hint in _DATA_NAME_HINTS):
            return name
    return unbound[0] if len(unbound) == 1 else None


def _unbound_names(tree: ast.AST) -> List[str]:
    assigned: Set[str] = set()
    unbound: List[str] = []
    for stmt in getattr(tree, "body", []):
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            if (
                child.id in assigned
                or child.id in _BUILTIN_NAMES
                or child.id in _SCI_AND_COMMON
            ):
                continue
            if child.id not in unbound:
                unbound.append(child.id)
        _record_assigns(stmt, assigned)
    return unbound


def _record_assigns(stmt: ast.AST, assigned: Set[str]) -> None:
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                assigned.add(target.id)
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        assigned.add(stmt.target.id)
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        assigned.add(stmt.target.id)
    elif isinstance(stmt, ast.For) and isinstance(stmt.target, ast.Name):
        assigned.add(stmt.target.id)
