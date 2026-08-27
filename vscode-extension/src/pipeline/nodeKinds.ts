/**
 * The node-kind registry: `analysis_gui.cli describe --json`, read into
 * something the palette, the port handles and the inspector can use.
 *
 * `describe` is the single source of truth for what a node kind is. Nothing in
 * this file hard-codes a kind, a parameter type or a data-kind tag — the
 * vocabulary comes off the wire, so adding a node kind in Python needs no
 * change here. The one thing that is hard-coded is the *shape* of the envelope.
 *
 * Pure: no `vscode`, no `node:*`, no DOM. Both sides of the webview boundary
 * use it.
 */

import type { JsonObject, JsonValue, PipelineNode, PipelineParameter, Position } from './document';
import { isJsonObject } from './document';

/** The tag every port carries when `describe` did not say otherwise. */
export const DATA_KIND_ANY = 'any';

export interface NodePort {
	readonly name: string;
	readonly label: string;
	readonly data_kind: string;
	readonly required: boolean;
	readonly description: string;
}

export interface NodeKindParameter {
	readonly name: string;
	readonly param_type: string;
	readonly default_value: JsonValue;
	readonly description: string;
	readonly options: readonly JsonValue[];
}

export interface NodeKind {
	readonly kind: string;
	readonly palette_label: string;
	/**
	 * False for a kind that is constructible but should not be offered in the
	 * palette. A document that already contains one still renders.
	 */
	readonly in_palette: boolean;
	readonly node_type: string;
	readonly label: string;
	readonly description: string;
	readonly metadata: JsonObject;
	readonly parameters: readonly NodeKindParameter[];
	readonly inputs: readonly NodePort[];
	readonly outputs: readonly NodePort[];
}

export interface NodeKindRegistry {
	readonly schemaVersion: number | undefined;
	readonly analysisGuiVersion: string | undefined;
	readonly nodeTypes: readonly string[];
	readonly portDataKinds: readonly string[];
	readonly kinds: readonly NodeKind[];
}

export const EMPTY_REGISTRY: NodeKindRegistry = {
	schemaVersion: undefined,
	analysisGuiVersion: undefined,
	nodeTypes: [],
	portDataKinds: [],
	kinds: [],
};

/**
 * Reads a `describe --json` envelope. Returns `undefined` rather than throwing
 * so a CLI that changed shape degrades the canvas to "no palette, generic
 * ports" instead of a blank webview.
 */
export function parseDescribeEnvelope(payload: unknown): NodeKindRegistry | undefined {
	if (!isJsonObject(payload) || !Array.isArray(payload['node_kinds'])) {
		return undefined;
	}

	const builtIn = payload['node_kinds'].filter(isJsonObject).map(parseKind);
	const discovered = asArray(payload['discovered_kinds']).filter(isJsonObject).map(parseKind);
	const seen = new Set(builtIn.map((kind) => kind.kind));
	const extra = discovered.filter((kind) => kind.kind !== '' && !seen.has(kind.kind));

	return {
		schemaVersion: asNumber(payload['schema_version']),
		analysisGuiVersion: asString(payload['analysis_gui_version']),
		nodeTypes: stringArray(payload['node_types']),
		portDataKinds: stringArray(payload['port_data_kinds']),
		kinds: extra.length > 0 ? [...builtIn, ...extra] : builtIn,
	};
}

/** Reads one node-kind object, as emitted by `discover` / `similar`. */
export function parseNodeKind(payload: unknown): NodeKind | undefined {
	if (!isJsonObject(payload)) {
		return undefined;
	}
	const kind = parseKind(payload);
	return kind.kind ? kind : undefined;
}

/**
 * Identifies the kind a document node belongs to.
 *
 * Ports are not stored on nodes, so this is the only way back to them: a kind
 * matches when `node_type` agrees and every metadata key the kind declares is
 * present with the same value. That is exactly how Python's `ports_for` picks a
 * variant — `preprocessor` alone is ambiguous, `preprocessor` plus
 * `processor_type: "split"` is not.
 *
 * The most specific match wins, so a kind declaring no metadata (`visualizer`)
 * never shadows one that does.
 */
export function matchKind(registry: NodeKindRegistry, node: PipelineNode): NodeKind | undefined {
	let best: NodeKind | undefined;
	let bestSpecificity = -1;

	for (const kind of registry.kinds) {
		if (kind.node_type !== node.node_type) {
			continue;
		}
		const declared = Object.entries(kind.metadata);
		const matches = declared.every(([key, value]) => sameJson(node.metadata[key], value));
		if (matches && declared.length > bestSpecificity) {
			best = kind;
			bestSpecificity = declared.length;
		}
	}

	return best;
}

/**
 * The kinds the palette offers, in `describe` order.
 *
 * Starred discovered kinds are offered even when `in_palette` is false: that
 * flag exists so a library scan does not dump every function onto the rail, but
 * a starred hit is a deliberate offer.
 */
export function paletteKinds(registry: NodeKindRegistry): readonly NodeKind[] {
	return registry.kinds.filter(
		(kind) => kind.in_palette || (kind.metadata['starred'] === true && typeof kind.metadata['source_path'] === 'string'),
	);
}

export function findKind(registry: NodeKindRegistry, kind: string): NodeKind | undefined {
	return registry.kinds.find((candidate) => candidate.kind === kind);
}

/**
 * The connection rule, and the whole of it: connectable when either side is
 * `any` or the two tags are equal. Mirrors `NodePort.accepts` in `node.py`.
 *
 * `any` is the tag used for an unknown port precisely so that an unrecognised
 * node kind stays connectable rather than becoming an island.
 */
export function accepts(source: NodePort, target: NodePort): boolean {
	return (
		source.data_kind === DATA_KIND_ANY ||
		target.data_kind === DATA_KIND_ANY ||
		source.data_kind === target.data_kind
	);
}

/**
 * Ports for a node, from its kind.
 *
 * A node whose kind is unknown still gets one input and one output, both
 * tagged `any`. Without them an unrecognised node would render with no handles
 * and its existing edges would have nothing to attach to — the file would look
 * more broken than it is.
 */
export function portsFor(kind: NodeKind | undefined): { inputs: readonly NodePort[]; outputs: readonly NodePort[] } {
	if (!kind) {
		return { inputs: [FALLBACK_INPUT], outputs: [FALLBACK_OUTPUT] };
	}
	return { inputs: kind.inputs, outputs: kind.outputs };
}

const FALLBACK_INPUT: NodePort = {
	name: 'input',
	label: 'Input',
	data_kind: DATA_KIND_ANY,
	required: false,
	description: 'This node kind is not in the registry, so its ports are unknown.',
};

const FALLBACK_OUTPUT: NodePort = {
	name: 'output',
	label: 'Output',
	data_kind: DATA_KIND_ANY,
	required: false,
	description: 'This node kind is not in the registry, so its ports are unknown.',
};

/**
 * Resolves an edge endpoint's port name to a port.
 *
 * `null` means "the single implicit port", which is how every edge in a v0
 * document and every edge the desktop app writes identifies itself. It resolves
 * to the sole port when there is exactly one; with several, there is no
 * defensible guess, so it stays unresolved and the edge is drawn against the
 * first handle without a compatibility claim.
 */
export function resolvePort(ports: readonly NodePort[], name: string | null): NodePort | undefined {
	if (name === null) {
		return ports.length === 1 ? ports[0] : undefined;
	}
	return ports.find((port) => port.name === name);
}

/**
 * Builds the document JSON for a new node of `kind`.
 *
 * Parameters come from the kind's template with `value: null` — the node starts
 * un-overridden and codegen resolves to `default_value` until someone edits it.
 * Writing the default into `value` here would make every new node look
 * hand-tuned and would pin it to today's default forever.
 *
 * No Python process is involved: `describe` already said everything needed.
 */
export function nodeFromKind(kind: NodeKind, id: string, position: Position): PipelineNode {
	const parameters = new Map<string, PipelineParameter>();
	for (const parameter of kind.parameters) {
		parameters.set(parameter.name, {
			name: parameter.name,
			param_type: parameter.param_type,
			default_value: parameter.default_value,
			value: null,
			description: parameter.description,
			options: parameter.options,
			extra: {},
		});
	}

	return {
		id,
		node_type: kind.node_type,
		label: kind.label,
		description: kind.description,
		parameters,
		position,
		metadata: { ...kind.metadata },
		extra: {},
	};
}

/**
 * A RFC 4122 version 4 UUID, to match the ids the Python side mints.
 *
 * The byte source is injected so tests can be deterministic. The default is
 * `crypto.getRandomValues`, which exists in a VS Code webview, in a browser and
 * in Node 19+ — the three places this canvas is meant to be able to run.
 */
export function uuid4(randomBytes: (length: number) => Uint8Array = cryptoRandomBytes): string {
	const bytes = randomBytes(16);
	// Version 4 in the high nibble of byte 6, RFC 4122 variant in byte 8.
	bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
	bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;

	const hex: string[] = [];
	for (const byte of bytes) {
		hex.push(byte.toString(16).padStart(2, '0'));
	}
	const text = hex.join('');
	return [
		text.slice(0, 8),
		text.slice(8, 12),
		text.slice(12, 16),
		text.slice(16, 20),
		text.slice(20, 32),
	].join('-');
}

function cryptoRandomBytes(length: number): Uint8Array {
	return globalThis.crypto.getRandomValues(new Uint8Array(length));
}

function parseKind(raw: JsonObject): NodeKind {
	const ports = isJsonObject(raw['ports']) ? raw['ports'] : {};
	return {
		kind: asString(raw['kind']) ?? '',
		palette_label: asString(raw['palette_label']) ?? asString(raw['label']) ?? '',
		// Absent means offer it: a registry from an older CLI that predates the
		// flag should not produce an empty palette.
		in_palette: raw['in_palette'] !== false,
		node_type: asString(raw['node_type']) ?? '',
		label: asString(raw['label']) ?? '',
		description: asString(raw['description']) ?? '',
		metadata: isJsonObject(raw['metadata']) ? raw['metadata'] : {},
		parameters: asArray(raw['parameters']).filter(isJsonObject).map(parseKindParameter),
		inputs: asArray(ports['inputs']).filter(isJsonObject).map(parsePort),
		outputs: asArray(ports['outputs']).filter(isJsonObject).map(parsePort),
	};
}

function parseKindParameter(raw: JsonObject): NodeKindParameter {
	return {
		name: asString(raw['name']) ?? '',
		param_type: asString(raw['param_type']) ?? 'string',
		default_value: raw['default_value'] ?? null,
		description: asString(raw['description']) ?? '',
		options: asArray(raw['options']),
	};
}

function parsePort(raw: JsonObject): NodePort {
	return {
		name: asString(raw['name']) ?? '',
		label: asString(raw['label']) ?? asString(raw['name']) ?? '',
		data_kind: asString(raw['data_kind']) ?? DATA_KIND_ANY,
		required: raw['required'] === true,
		description: asString(raw['description']) ?? '',
	};
}

function sameJson(a: JsonValue | undefined, b: JsonValue | undefined): boolean {
	return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function asArray(value: JsonValue | undefined): JsonValue[] {
	return Array.isArray(value) ? value : [];
}

function stringArray(value: JsonValue | undefined): string[] {
	return asArray(value).filter((entry): entry is string => typeof entry === 'string');
}

function asString(value: JsonValue | undefined): string | undefined {
	return typeof value === 'string' ? value : undefined;
}

function asNumber(value: JsonValue | undefined): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
