/**
 * The intent protocol: what the canvas asks for, and what that does to a
 * document.
 *
 * The webview never sends a document. It sends one of the six intents below and
 * the host replays it against the `TextDocument` it already has. Two things
 * follow from that:
 *
 * - **Undo is meaningful.** One intent becomes one `WorkspaceEdit`, so Ctrl+Z
 *   undoes "moved a node", not "replaced the file". A whole-document message
 *   cannot produce that no matter how the host applies it.
 * - **The host stays authoritative.** The document the intent lands on is the
 *   one on screen in every other tab, so an edit made in a text editor while
 *   the canvas is open is not clobbered by a stale copy.
 *
 * Intents are plain JSON: they cross `postMessage`, which in a VS Code webview
 * is a JSON channel, not a structured clone. That is why `addNode` carries the
 * node in its serialized form rather than as a `Map`-bearing model object.
 *
 * `applyIntent` is a pure function of (document, intent). It returns
 * `undefined` for a no-op — dragging a node back to where it started, deleting
 * an edge that is already gone — so the host can skip the edit entirely rather
 * than writing identical bytes and burning an undo step.
 */

import type { JsonObject, JsonValue, PipelineDocument, PipelineEdge, PipelineNode, Position } from './document';
import { edgeKey, parsePipelineNode } from './document';

export interface EdgeRef {
	readonly source: string;
	readonly source_port: string | null;
	readonly target: string;
	readonly target_port: string | null;
}

export type PipelineIntent =
	| { readonly kind: 'moveNode'; readonly nodeId: string; readonly position: readonly [number, number] }
	| { readonly kind: 'addNode'; readonly nodeId: string; readonly node: JsonObject }
	| { readonly kind: 'deleteNode'; readonly nodeId: string }
	| { readonly kind: 'addEdge'; readonly edge: EdgeRef }
	| { readonly kind: 'deleteEdge'; readonly edge: EdgeRef }
	| { readonly kind: 'setParam'; readonly nodeId: string; readonly param: string; readonly value: JsonValue };

/** A short label for the undo stack and the output channel. */
export function describeIntent(intent: PipelineIntent): string {
	switch (intent.kind) {
		case 'moveNode':
			return `Move node ${short(intent.nodeId)}`;
		case 'addNode':
			return `Add node ${short(intent.nodeId)}`;
		case 'deleteNode':
			return `Delete node ${short(intent.nodeId)}`;
		case 'addEdge':
			return `Connect ${short(intent.edge.source)} to ${short(intent.edge.target)}`;
		case 'deleteEdge':
			return `Disconnect ${short(intent.edge.source)} from ${short(intent.edge.target)}`;
		case 'setParam':
			return `Set ${intent.param} on node ${short(intent.nodeId)}`;
	}
}

/**
 * Applies one intent, returning a new document or `undefined` when the intent
 * would not change anything.
 *
 * Permissive on purpose: an intent naming a node that is no longer there is a
 * no-op rather than an error. The webview's view of the document can lag the
 * text by one message, and a race between a delete in the text editor and a
 * drag on the canvas should lose the drag, not raise.
 */
export function applyIntent(document: PipelineDocument, intent: PipelineIntent): PipelineDocument | undefined {
	switch (intent.kind) {
		case 'moveNode':
			return moveNode(document, intent.nodeId, intent.position);
		case 'addNode':
			return addNode(document, intent.nodeId, intent.node);
		case 'deleteNode':
			return deleteNode(document, intent.nodeId);
		case 'addEdge':
			return addEdge(document, intent.edge);
		case 'deleteEdge':
			return deleteEdge(document, intent.edge);
		case 'setParam':
			return setParam(document, intent.nodeId, intent.param, intent.value);
	}
}

function moveNode(document: PipelineDocument, nodeId: string, position: readonly [number, number]): PipelineDocument | undefined {
	const node = document.nodes.get(nodeId);
	if (!node) {
		return undefined;
	}

	// Positions arrive from the pointer as floats. Round to whole pixels: the
	// document is a file people read, and `[112.00000000000001, 47]` in a diff
	// is noise with no meaning behind it.
	const next: Position = [Math.round(position[0]), Math.round(position[1])];
	if (next[0] === node.position[0] && next[1] === node.position[1]) {
		return undefined;
	}

	return withNodes(document, replaceNode(document.nodes, nodeId, { ...node, position: next }));
}

function addNode(document: PipelineDocument, nodeId: string, raw: JsonObject): PipelineDocument | undefined {
	if (document.nodes.has(nodeId)) {
		return undefined;
	}

	const node = parsePipelineNode(nodeId, raw);
	const nodes = new Map(document.nodes);
	// The id is the key *and* a field, and `node_id_mismatch` exists because
	// they can disagree. Take the key as authoritative so a malformed intent
	// cannot introduce the finding.
	nodes.set(nodeId, { ...node, id: nodeId });
	return withNodes(document, nodes);
}

/**
 * Removes a node and every edge touching it.
 *
 * Leaving the edges behind would turn one deletion into a handful of
 * `dangling_edge` errors, which is never what someone pressing Delete on a
 * canvas meant. Both go in a single intent so they also undo together.
 */
function deleteNode(document: PipelineDocument, nodeId: string): PipelineDocument | undefined {
	if (!document.nodes.has(nodeId)) {
		return undefined;
	}

	const nodes = new Map(document.nodes);
	nodes.delete(nodeId);

	return {
		...document,
		nodes,
		edges: document.edges.filter((edge) => !touches(edge, nodeId)),
	};
}

/**
 * Connects two ports, displacing whatever was already on the target port.
 *
 * `node.py` is explicit that no kind takes fan-in: "every port takes at most
 * one edge". Enforcing that on connect means a re-wire is one gesture instead
 * of delete-then-drag, and the canvas cannot author a document the model says
 * is impossible.
 */
function addEdge(document: PipelineDocument, ref: EdgeRef): PipelineDocument | undefined {
	const edge: PipelineEdge = { form: 'object', ...ref, extra: {} };
	const key = edgeKey(edge);

	const kept = document.edges.filter((existing) => !occupiesSameTargetPort(existing, ref));
	if (kept.length === document.edges.length && document.edges.some((existing) => edgeKey(existing) === key)) {
		return undefined;
	}

	return { ...document, edges: [...kept, edge] };
}

/** Removes the first edge matching the reference, in either serialized shape. */
function deleteEdge(document: PipelineDocument, ref: EdgeRef): PipelineDocument | undefined {
	const key = edgeKey({ form: 'object', ...ref, extra: {} });
	const index = document.edges.findIndex((edge) => edgeKey(edge) === key);
	if (index < 0) {
		return undefined;
	}

	const edges = [...document.edges];
	edges.splice(index, 1);
	return { ...document, edges };
}

/**
 * Writes a parameter override.
 *
 * Always `value`, never `default_value`: the default belongs to the node kind
 * and is whatever `describe` declared. `null` clears the override, which is how
 * "reset to default" is expressed — `resolved_value` in Python reads `value`
 * when it is not `None` and `default_value` otherwise.
 *
 * A parameter the node does not declare is ignored rather than created. The
 * parameter set comes from the kind, so inventing one here would produce a node
 * that codegen has no idea what to do with.
 */
function setParam(document: PipelineDocument, nodeId: string, param: string, value: JsonValue): PipelineDocument | undefined {
	const node = document.nodes.get(nodeId);
	const parameter = node?.parameters.get(param);
	if (!node || !parameter) {
		return undefined;
	}

	const next = value ?? null;
	if (JSON.stringify(parameter.value) === JSON.stringify(next)) {
		return undefined;
	}

	const parameters = new Map(node.parameters);
	parameters.set(param, { ...parameter, value: next });
	return withNodes(document, replaceNode(document.nodes, nodeId, { ...node, parameters }));
}

/** Replaces a node in place, keeping the surrounding key order intact. */
function replaceNode(
	nodes: ReadonlyMap<string, PipelineNode>,
	nodeId: string,
	replacement: PipelineNode,
): Map<string, PipelineNode> {
	const next = new Map(nodes);
	next.set(nodeId, replacement);
	return next;
}

function withNodes(document: PipelineDocument, nodes: Map<string, PipelineNode>): PipelineDocument {
	return { ...document, nodes };
}

function touches(edge: PipelineEdge, nodeId: string): boolean {
	return edge.form !== 'unknown' && (edge.source === nodeId || edge.target === nodeId);
}

function occupiesSameTargetPort(edge: PipelineEdge, ref: EdgeRef): boolean {
	if (edge.form === 'unknown') {
		return false;
	}
	const port = edge.form === 'legacyPair' ? null : edge.target_port;
	return edge.target === ref.target && port === ref.target_port;
}

function short(id: string): string {
	return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}
