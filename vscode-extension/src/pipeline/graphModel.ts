/**
 * Document to React Flow, and the rules for what may be connected to what.
 *
 * Pure and React-free: this module names no component and imports nothing from
 * `@xyflow/react`. It produces the plain `{id, position, data}` records React
 * Flow consumes, which is what makes the interesting half of the canvas
 * testable under `node:test` with no DOM in sight.
 */

import type { PipelineDocument, PipelineEdge, PipelineNode } from './document';
import type { EdgeRef } from './intents';
import { classifyNode, compactPreview, type NodeAppearance, type ParamPreview } from './nodeFamily';
import type { NodeKind, NodeKindRegistry, NodePort } from './nodeKinds';
import { accepts, matchKind, portsFor, resolvePort } from './nodeKinds';

/** How a node is faring in the last validation run, if we were told. */
export type NodeSeverity = 'error' | 'warning';

// A type alias rather than an interface so it satisfies React Flow's
// `Record<string, unknown>` constraint on node data. Interfaces get no implicit
// index signature; type aliases do.
export type FlowNodeData = {
	readonly label: string;
	readonly nodeType: string;
	/** The palette name of the matched kind, or undefined when unrecognised. */
	readonly kindLabel: string | undefined;
	readonly family: NodeAppearance['family'];
	readonly badge: string;
	readonly signalType: string | undefined;
	readonly provider: string | undefined;
	readonly siStage: string | undefined;
	readonly preview: ParamPreview | undefined;
	readonly inputs: readonly NodePort[];
	readonly outputs: readonly NodePort[];
	/** Parameters carrying an override, for the one-line summary on the card. */
	readonly overrides: readonly { readonly name: string; readonly value: string }[];
	readonly severity: NodeSeverity | undefined;
	/** Last `runProgress` for this node, if a run has started. */
	readonly runState: 'pending' | 'running' | 'ok' | 'error' | undefined;
};

export interface FlowNode {
	readonly id: string;
	readonly type: 'pipelineNode';
	readonly position: { readonly x: number; readonly y: number };
	readonly data: FlowNodeData;
}

export type FlowEdgeData = {
	/** The document edge this was drawn from, for a `deleteEdge` intent. */
	readonly ref: EdgeRef;
	readonly dataKind: string | undefined;
};

export interface FlowEdge {
	readonly id: string;
	readonly source: string;
	readonly target: string;
	readonly sourceHandle: string | null;
	readonly targetHandle: string | null;
	readonly data: FlowEdgeData;
}

export interface FlowModel {
	readonly nodes: readonly FlowNode[];
	readonly edges: readonly FlowEdge[];
	/**
	 * Edges in the document that have no drawing: a dangling endpoint, or an
	 * entry that is not an edge at all. Counted rather than dropped silently so
	 * the canvas can say "3 edges are not shown" instead of quietly disagreeing
	 * with the file.
	 */
	readonly undrawableEdges: number;
}

/** Handle ids. Prefixed by direction because a port name can repeat across sides. */
export function outputHandleId(portName: string): string {
	return `out:${portName}`;
}

export function inputHandleId(portName: string): string {
	return `in:${portName}`;
}

export function portNameFromHandle(handle: string | null | undefined): string | null {
	if (!handle) {
		return null;
	}
	const separator = handle.indexOf(':');
	return separator < 0 ? null : handle.slice(separator + 1);
}

/**
 * Builds the React Flow model for a document.
 *
 * `marks` carries validation severity per node id. It is optional because the
 * canvas has to render before — and whether or not — Python has said anything.
 */
export function toFlowModel(
	document: PipelineDocument,
	registry: NodeKindRegistry,
	marks: ReadonlyMap<string, NodeSeverity> = new Map(),
): FlowModel {
	const nodes: FlowNode[] = [];
	const kinds = new Map<string, NodeKind | undefined>();

	for (const [id, node] of document.nodes) {
		const kind = matchKind(registry, node);
		kinds.set(id, kind);
		const ports = portsFor(kind);
		const appearance = classifyNode(node, kind);
		const overrides = overridesOf(node);

		nodes.push({
			id,
			type: 'pipelineNode',
			position: { x: node.position[0], y: node.position[1] },
			data: {
				label: node.label || kind?.label || id,
				nodeType: node.node_type,
				kindLabel: kind?.palette_label,
				family: appearance.family,
				badge: appearance.badge,
				signalType: appearance.signalType,
				provider: appearance.provider,
				siStage: appearance.siStage,
				preview: compactPreview(node.parameters, overrides),
				inputs: ports.inputs,
				outputs: ports.outputs,
				overrides,
				severity: marks.get(id),
				runState: undefined,
			},
		});
	}

	const edges: FlowEdge[] = [];
	let undrawableEdges = 0;

	document.edges.forEach((edge, index) => {
		const drawn = toFlowEdge(edge, index, document, kinds);
		if (drawn) {
			edges.push(drawn);
		} else {
			undrawableEdges += 1;
		}
	});

	return { nodes, edges, undrawableEdges };
}

export type ConnectionVerdict = { readonly ok: true } | { readonly ok: false; readonly reason: string };

export interface ConnectionCandidate {
	readonly source: string | null;
	readonly sourceHandle: string | null;
	readonly target: string | null;
	readonly targetHandle: string | null;
}

/**
 * Whether a drag in progress may be dropped where it is.
 *
 * Four things can stop it, in the order a user is likely to hit them:
 *
 * 1. an endpoint that is not there — a half-finished drag;
 * 2. a self-connection, which is a cycle of length one;
 * 3. incompatible data kinds, per the rule in `nodeKinds.accepts`;
 * 4. a cycle, because the document is a DAG and `cycle_detected` is a
 *    validation error. Cheaper to refuse the drag than to let someone author
 *    the error and then read about it in the Problems panel.
 *
 * The reason string is shown to the user, so it says what is wrong in terms of
 * the ports rather than in terms of the model.
 */
export function canConnect(
	document: PipelineDocument,
	registry: NodeKindRegistry,
	candidate: ConnectionCandidate,
): ConnectionVerdict {
	const { source, target } = candidate;
	if (!source || !target) {
		return { ok: false, reason: 'A connection needs both ends.' };
	}

	const sourceNode = document.nodes.get(source);
	const targetNode = document.nodes.get(target);
	if (!sourceNode || !targetNode) {
		return { ok: false, reason: 'One end of this connection is not in the document.' };
	}
	if (source === target) {
		return { ok: false, reason: 'A node cannot be connected to itself.' };
	}

	const sourcePort = portOf(registry, sourceNode, 'outputs', portNameFromHandle(candidate.sourceHandle));
	const targetPort = portOf(registry, targetNode, 'inputs', portNameFromHandle(candidate.targetHandle));
	if (!sourcePort || !targetPort) {
		return { ok: false, reason: 'That handle does not correspond to a declared port.' };
	}

	if (!accepts(sourcePort, targetPort)) {
		return {
			ok: false,
			reason: `${sourcePort.label} carries ${sourcePort.data_kind}, but ${targetPort.label} takes ${targetPort.data_kind}.`,
		};
	}

	if (reaches(document, target, source)) {
		return { ok: false, reason: 'That would make a cycle, and a pipeline has to be acyclic.' };
	}

	return { ok: true };
}

/**
 * The document edge a completed connection becomes.
 *
 * Port names are written out rather than left `null`. `null` is only
 * unambiguous on a node with exactly one port on that side, and being explicit
 * costs nothing: Python resolves an edge by port name and only falls back to
 * "the single implicit port" when the name is absent.
 */
export function refFromConnection(candidate: ConnectionCandidate): EdgeRef | undefined {
	if (!candidate.source || !candidate.target) {
		return undefined;
	}
	return {
		source: candidate.source,
		source_port: portNameFromHandle(candidate.sourceHandle),
		target: candidate.target,
		target_port: portNameFromHandle(candidate.targetHandle),
	};
}

function toFlowEdge(
	edge: PipelineEdge,
	index: number,
	document: PipelineDocument,
	kinds: ReadonlyMap<string, NodeKind | undefined>,
): FlowEdge | undefined {
	if (edge.form === 'unknown') {
		return undefined;
	}

	const sourceNode = document.nodes.get(edge.source);
	const targetNode = document.nodes.get(edge.target);
	if (!sourceNode || !targetNode) {
		// A dangling endpoint. React Flow silently drops an edge naming a node
		// it does not have, so counting it here is the only way to know.
		return undefined;
	}

	const sourcePortName = edge.form === 'legacyPair' ? null : edge.source_port;
	const targetPortName = edge.form === 'legacyPair' ? null : edge.target_port;

	const outputs = portsFor(kinds.get(edge.source)).outputs;
	const inputs = portsFor(kinds.get(edge.target)).inputs;
	// An unresolvable name still gets drawn, against the first handle: the edge
	// is in the file and hiding it would misrepresent the document. The
	// validator is the thing that complains about the name.
	const sourcePort = resolvePort(outputs, sourcePortName) ?? outputs[0];
	const targetPort = resolvePort(inputs, targetPortName) ?? inputs[0];

	return {
		id: `edge:${String(index)}`,
		source: edge.source,
		target: edge.target,
		sourceHandle: sourcePort ? outputHandleId(sourcePort.name) : null,
		targetHandle: targetPort ? inputHandleId(targetPort.name) : null,
		data: {
			ref: {
				source: edge.source,
				source_port: sourcePortName,
				target: edge.target,
				target_port: targetPortName,
			},
			dataKind: sourcePort?.data_kind ?? undefined,
		},
	};
}

function portOf(
	registry: NodeKindRegistry,
	node: PipelineNode,
	side: 'inputs' | 'outputs',
	portName: string | null,
): NodePort | undefined {
	const ports = portsFor(matchKind(registry, node))[side];
	return resolvePort(ports, portName);
}

/** Whether `from` can reach `to` by following edges. Iterative: documents nest deeply. */
function reaches(document: PipelineDocument, from: string, to: string): boolean {
	const outgoing = new Map<string, string[]>();
	for (const edge of document.edges) {
		if (edge.form === 'unknown') {
			continue;
		}
		const existing = outgoing.get(edge.source);
		if (existing) {
			existing.push(edge.target);
		} else {
			outgoing.set(edge.source, [edge.target]);
		}
	}

	const seen = new Set<string>([from]);
	const stack = [from];
	while (stack.length > 0) {
		const current = stack.pop();
		if (current === undefined) {
			break;
		}
		if (current === to) {
			return true;
		}
		for (const next of outgoing.get(current) ?? []) {
			if (!seen.has(next)) {
				seen.add(next);
				stack.push(next);
			}
		}
	}

	return false;
}

function overridesOf(node: PipelineNode): { name: string; value: string }[] {
	const out: { name: string; value: string }[] = [];
	for (const [name, parameter] of node.parameters) {
		if (parameter.value !== null) {
			out.push({ name, value: typeof parameter.value === 'string' ? parameter.value : JSON.stringify(parameter.value) });
		}
	}
	return out;
}
