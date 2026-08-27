/**
 * The `.pipeline` document format, as an in-memory model plus a serializer that
 * writes bytes Python would have written.
 *
 * Nothing here imports `vscode`, `node:*` or the DOM. The extension host and the
 * webview both run this module, and `npm test` exercises it under plain Node.
 *
 * ## Why this is not just `JSON.parse`
 *
 * A canvas rewrites the file on every drag. If serialization is not byte-stable,
 * a one-pixel move produces a diff touching every line and the format stops
 * being reviewable. Two properties buy that stability:
 *
 * 1. **Key order matches Python.** `PipelineGraph.to_dict` builds dicts in a
 *    fixed order and `json.dump(..., indent=2, sort_keys=False)` writes them in
 *    insertion order, so the order is part of the format whether or not anyone
 *    wrote it down. `KEY_ORDER` below mirrors `graph.py` and `node.py`.
 * 2. **Unrecognised content survives.** Anything this module does not have a
 *    field for — an extra key on a node, an edge shaped like `42` — is kept and
 *    written back in its original position. An editor that silently drops what
 *    it does not understand is worse than one that refuses to open the file.
 *
 * Ordered containers are `Map`s rather than plain objects so key order is a
 * guarantee rather than a property of V8's object layout. The wire format
 * between host and webview is plain JSON, so the `Map`s never have to survive a
 * `postMessage`; both sides parse the same JSON value independently.
 */

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

/** `[x, y]`, matching Python's `list(self.position)`. */
export type Position = readonly [number, number];

export interface PipelineParameter {
	readonly name: string;
	readonly param_type: string;
	readonly default_value: JsonValue;
	/** The user override. `null` means "not overridden"; never write a default here. */
	readonly value: JsonValue;
	readonly description: string;
	readonly options: readonly JsonValue[];
	/** Keys this version does not know about, preserved verbatim. */
	readonly extra: JsonObject;
}

export interface PipelineNode {
	readonly id: string;
	readonly node_type: string;
	readonly label: string;
	readonly description: string;
	readonly parameters: ReadonlyMap<string, PipelineParameter>;
	readonly position: Position;
	readonly metadata: JsonObject;
	readonly extra: JsonObject;
}

/**
 * An edge in one of the three shapes a document can contain.
 *
 * `legacyPair` and `unknown` exist so a v0 document, or one written by a newer
 * client, round-trips unchanged. The canvas only ever creates `object` edges.
 */
export type PipelineEdge =
	| {
			readonly form: 'object';
			readonly source: string;
			readonly source_port: string | null;
			readonly target: string;
			readonly target_port: string | null;
			readonly extra: JsonObject;
	  }
	| { readonly form: 'legacyPair'; readonly source: string; readonly target: string }
	| { readonly form: 'unknown'; readonly raw: JsonValue };

export interface PipelineDocument {
	/**
	 * Absent for a v0 document. Preserved rather than upgraded: this is an
	 * editor, not a migrator, and silently adding `"version": 1` to a file
	 * because someone dragged a node is churn nobody asked for. `analysis-gui`
	 * upgrades on load/save if that is what you want.
	 */
	readonly version: number | undefined;
	readonly nodes: ReadonlyMap<string, PipelineNode>;
	readonly edges: readonly PipelineEdge[];
	readonly extra: JsonObject;
}

export interface SerializeOptions {
	/**
	 * Python's `json.dump` writes no trailing newline, but every fixture and
	 * every file a human has touched has one. Follow the input rather than
	 * picking a side, so re-serializing never moves the last byte.
	 */
	readonly trailingNewline?: boolean;
}

export class PipelineParseError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'PipelineParseError';
	}
}

/** Key order per container, copied from the Python `to_dict` implementations. */
const DOCUMENT_KEYS = ['version', 'nodes', 'edges'] as const;
const NODE_KEYS = ['id', 'node_type', 'label', 'description', 'parameters', 'position', 'metadata'] as const;
const PARAMETER_KEYS = ['name', 'param_type', 'default_value', 'value', 'description', 'options'] as const;
const EDGE_KEYS = ['source', 'source_port', 'target', 'target_port'] as const;

const EMPTY_POSITION: Position = [0, 0];

/** `JSON.parse` plus {@link parsePipelineDocument}, for callers holding text. */
export function parsePipelineText(text: string): PipelineDocument {
	let payload: unknown;
	try {
		payload = JSON.parse(text);
	} catch (error) {
		throw new PipelineParseError(error instanceof Error ? error.message : String(error));
	}
	return parsePipelineDocument(payload as JsonValue);
}

/**
 * Reads an already-parsed JSON value into the model.
 *
 * Deliberately more permissive than the Python loader, which raises on an
 * unknown `node_type`. Refusing to render a document is the one thing a canvas
 * must not do when the Problems panel is right there explaining the problem, so
 * anything structurally sane loads and anything else is preserved untouched.
 */
export function parsePipelineDocument(payload: JsonValue): PipelineDocument {
	if (!isJsonObject(payload)) {
		throw new PipelineParseError('A pipeline document must be a JSON object.');
	}

	const rawNodes = payload['nodes'];
	if (rawNodes !== undefined && !isJsonObject(rawNodes)) {
		throw new PipelineParseError('"nodes" must be a JSON object keyed by node id.');
	}
	const rawEdges = payload['edges'];
	if (rawEdges !== undefined && !Array.isArray(rawEdges)) {
		throw new PipelineParseError('"edges" must be an array.');
	}

	const nodes = new Map<string, PipelineNode>();
	for (const [id, value] of Object.entries(rawNodes ?? {})) {
		nodes.set(id, parseNode(id, value));
	}

	return {
		version: asNumber(payload['version']),
		nodes,
		edges: (rawEdges ?? []).map(parseEdge),
		extra: extraKeys(payload, DOCUMENT_KEYS),
	};
}

/**
 * Writes the document as `json.dump(..., indent=2)` would have.
 *
 * Verified byte-for-byte against Python output in `pipelineDocument.test.ts`.
 * The one divergence is non-ASCII text: `json.dump` defaults to
 * `ensure_ascii=True` and escapes it, `JSON.stringify` emits UTF-8. Both parse
 * back to the same string, so this is a diff-noise question rather than a
 * correctness one, and UTF-8 is the better thing to have in a reviewed file.
 */
export function serializePipelineDocument(
	document: PipelineDocument,
	options: SerializeOptions = {},
): string {
	const text = JSON.stringify(toJson(document), null, 2);
	return options.trailingNewline === false ? text : `${text}\n`;
}

/** The plain JSON value for a document, with every key in Python's order. */
export function toJson(document: PipelineDocument): JsonObject {
	const out: JsonObject = {};
	if (document.version !== undefined) {
		out['version'] = document.version;
	}

	const nodes: JsonObject = {};
	for (const [id, node] of document.nodes) {
		nodes[id] = nodeToJson(node);
	}
	out['nodes'] = nodes;
	out['edges'] = document.edges.map(edgeToJson);

	return appendExtra(out, document.extra);
}

export function nodeToJson(node: PipelineNode): JsonObject {
	const parameters: JsonObject = {};
	for (const [name, parameter] of node.parameters) {
		parameters[name] = parameterToJson(parameter);
	}

	return appendExtra(
		{
			id: node.id,
			node_type: node.node_type,
			label: node.label,
			description: node.description,
			parameters,
			position: [node.position[0], node.position[1]],
			metadata: node.metadata,
		},
		node.extra,
	);
}

export function parameterToJson(parameter: PipelineParameter): JsonObject {
	return appendExtra(
		{
			name: parameter.name,
			param_type: parameter.param_type,
			default_value: parameter.default_value,
			value: parameter.value,
			description: parameter.description,
			options: [...parameter.options],
		},
		parameter.extra,
	);
}

export function edgeToJson(edge: PipelineEdge): JsonValue {
	switch (edge.form) {
		case 'object':
			return appendExtra(
				{
					source: edge.source,
					source_port: edge.source_port,
					target: edge.target,
					target_port: edge.target_port,
				},
				edge.extra,
			);
		case 'legacyPair':
			return [edge.source, edge.target];
		case 'unknown':
			return edge.raw;
	}
}

/**
 * Identity of an edge for matching, mirroring Python's `Edge.key`. A legacy
 * pair denotes unset ports, so it keys the same as the object form it upgrades
 * to — which is what makes "delete this edge" work on either shape.
 */
export function edgeKey(edge: PipelineEdge): string | undefined {
	switch (edge.form) {
		case 'object':
			return JSON.stringify([edge.source, edge.source_port, edge.target, edge.target_port]);
		case 'legacyPair':
			return JSON.stringify([edge.source, null, edge.target, null]);
		case 'unknown':
			return undefined;
	}
}

/** True when both endpoints of `edge` name a node the document actually has. */
export function edgeEndpoints(edge: PipelineEdge): { source: string; target: string } | undefined {
	return edge.form === 'unknown' ? undefined : { source: edge.source, target: edge.target };
}

/** Reads one node from its JSON form. Exported for `addNode` intents. */
export function parsePipelineNode(id: string, value: JsonValue): PipelineNode {
	return parseNode(id, value);
}

function parseNode(id: string, value: JsonValue): PipelineNode {
	if (!isJsonObject(value)) {
		// A non-object under `nodes` is what `malformed_nodes` reports on. Keep
		// enough of a node to draw and to not lose the id.
		return {
			id,
			node_type: '',
			label: id,
			description: '',
			parameters: new Map(),
			position: EMPTY_POSITION,
			metadata: {},
			extra: {},
		};
	}

	const rawParameters = isJsonObject(value['parameters']) ? value['parameters'] : {};
	const parameters = new Map<string, PipelineParameter>();
	for (const [name, parameter] of Object.entries(rawParameters)) {
		parameters.set(name, parseParameter(name, parameter));
	}

	return {
		id: asString(value['id']) ?? id,
		node_type: asString(value['node_type']) ?? '',
		label: asString(value['label']) ?? '',
		description: asString(value['description']) ?? '',
		parameters,
		position: parsePosition(value['position']),
		metadata: isJsonObject(value['metadata']) ? value['metadata'] : {},
		extra: extraKeys(value, NODE_KEYS),
	};
}

function parseParameter(name: string, value: JsonValue): PipelineParameter {
	if (!isJsonObject(value)) {
		return {
			name,
			param_type: 'string',
			default_value: null,
			value: null,
			description: '',
			options: [],
			extra: {},
		};
	}

	const options = value['options'];
	return {
		name: asString(value['name']) ?? name,
		// Matches `NodeParameter.from_dict`, which defaults a missing type to string.
		param_type: asString(value['param_type']) ?? 'string',
		default_value: value['default_value'] ?? null,
		value: value['value'] ?? null,
		description: asString(value['description']) ?? '',
		options: Array.isArray(options) ? options : [],
		extra: extraKeys(value, PARAMETER_KEYS),
	};
}

function parseEdge(value: JsonValue): PipelineEdge {
	if (isJsonObject(value)) {
		const source = asString(value['source']);
		const target = asString(value['target']);
		if (source === undefined || target === undefined) {
			return { form: 'unknown', raw: value };
		}
		return {
			form: 'object',
			source,
			source_port: asString(value['source_port']) ?? null,
			target,
			target_port: asString(value['target_port']) ?? null,
			extra: extraKeys(value, EDGE_KEYS),
		};
	}

	if (Array.isArray(value) && value.length === 2) {
		const source = asString(value[0]);
		const target = asString(value[1]);
		if (source !== undefined && target !== undefined) {
			return { form: 'legacyPair', source, target };
		}
	}

	return { form: 'unknown', raw: value };
}

function parsePosition(value: JsonValue | undefined): Position {
	if (Array.isArray(value)) {
		const x = asNumber(value[0]);
		const y = asNumber(value[1]);
		if (x !== undefined && y !== undefined) {
			return [x, y];
		}
	}
	return EMPTY_POSITION;
}

/** The keys of `value` that are not in `known`, in their original order. */
function extraKeys(value: JsonObject, known: readonly string[]): JsonObject {
	const out: JsonObject = {};
	for (const [key, entry] of Object.entries(value)) {
		if (!known.includes(key) && entry !== undefined) {
			out[key] = entry;
		}
	}
	return out;
}

function appendExtra(known: JsonObject, extra: JsonObject): JsonObject {
	for (const [key, value] of Object.entries(extra)) {
		known[key] = value;
	}
	return known;
}

export function isJsonObject(value: unknown): value is JsonObject {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: JsonValue | undefined): string | undefined {
	return typeof value === 'string' ? value : undefined;
}

function asNumber(value: JsonValue | undefined): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
