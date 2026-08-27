/**
 * Visual family and palette grouping, derived from `node_type` plus the
 * metadata `describe` already stamps (`signal_type`, `provider`, `backend`,
 * `si_stage`, discovered `source_path`).
 *
 * Nothing here names a factory or a `.pipeline` field that is not already on
 * the document. The canvas uses this for colour, badges and palette rails; the
 * intent protocol never sees it.
 */

import { nodeToJson, type JsonObject, type JsonValue, type PipelineNode, type PipelineParameter } from './document';
import type { PipelineIntent } from './intents';
import { findKind, nodeFromKind, uuid4, type NodeKind, type NodeKindRegistry, type NodePort } from './nodeKinds';

export const NEURAL_SIGNAL_TYPES = ['eeg', 'lfp', 'spike', 'calcium'] as const;
export type NeuralSignalType = (typeof NEURAL_SIGNAL_TYPES)[number];

export type NodeFamily =
	| 'loader'
	| 'preprocessor'
	| 'analyzer'
	| 'visualizer'
	| 'output'
	| 'llm-claude'
	| 'llm-gpt'
	| 'llm'
	| 'neural-eeg'
	| 'neural-lfp'
	| 'neural-spike'
	| 'neural-calcium'
	| 'neural'
	| 'spikeinterface'
	| 'custom'
	| 'unknown';

export type PaletteGroupId = 'neural' | 'spikeinterface' | 'analysis' | 'models' | 'code';

export interface NodeAppearance {
	readonly family: NodeFamily;
	readonly paletteGroup: PaletteGroupId;
	readonly badge: string;
	readonly signalType: NeuralSignalType | undefined;
	readonly provider: string | undefined;
	readonly siStage: string | undefined;
}

/** MiniMap / CSS fallbacks. Muted instrument pigments, not neon. */
export const FAMILY_SWATCH: Record<NodeFamily, string> = {
	loader: '#3d7a7a',
	preprocessor: '#8a7440',
	analyzer: '#4a6d94',
	visualizer: '#8a5a72',
	output: '#6a6a78',
	'llm-claude': '#b56a3c',
	'llm-gpt': '#4e8a62',
	llm: '#6a7a8a',
	'neural-eeg': '#4a78a8',
	'neural-lfp': '#3a8a7a',
	'neural-spike': '#b08a32',
	'neural-calcium': '#6a8a48',
	neural: '#4a7a8a',
	spikeinterface: '#6a5a88',
	custom: '#6e6e6e',
	unknown: '#5a5a5a',
};

export interface PaletteGroup {
	readonly id: PaletteGroupId;
	readonly title: string;
	readonly kinds: readonly NodeKind[];
}

const PALETTE_GROUP_ORDER: readonly PaletteGroupId[] = [
	'neural',
	'spikeinterface',
	'analysis',
	'models',
	'code',
];

const PALETTE_GROUP_TITLE: Record<PaletteGroupId, string> = {
	neural: 'Neural',
	spikeinterface: 'SpikeInterface',
	analysis: 'Analysis',
	models: 'Models',
	code: 'Your code',
};

const NEURAL_SIGNALS = new Set<string>(NEURAL_SIGNAL_TYPES);

const PREVIEW_PARAM_NAMES = [
	'file_path',
	'file',
	'path',
	'recording',
	'folder',
	'model',
	'model_name',
	'function_name',
	'algorithm',
	'method',
] as const;

export function isNeuralSignal(value: string | undefined): value is NeuralSignalType {
	return value !== undefined && NEURAL_SIGNALS.has(value);
}

export function metaString(metadata: JsonObject, key: string): string | undefined {
	const value = metadata[key];
	return typeof value === 'string' && value !== '' ? value : undefined;
}

export function classifyAppearance(input: {
	readonly nodeType: string;
	readonly metadata: JsonObject;
	readonly kind?: NodeKind | undefined;
}): NodeAppearance {
	const nodeType = input.nodeType || input.kind?.node_type || '';
	const metadata = { ...(input.kind?.metadata ?? {}), ...input.metadata };
	const signalType = neuralSignalOf(metadata, input.kind);
	const provider = metaString(metadata, 'provider');
	const siStage = metaString(metadata, 'si_stage');

	if (isSpikeInterface(metadata)) {
		return {
			family: 'spikeinterface',
			paletteGroup: 'spikeinterface',
			badge: siStage ? `SI · ${siStage}` : 'SpikeInterface',
			signalType: isNeuralSignal(signalType) ? signalType : 'spike',
			provider,
			siStage,
		};
	}

	if (nodeType === 'model_call') {
		const family: NodeFamily =
			provider === 'claude' ? 'llm-claude' : provider === 'gpt' ? 'llm-gpt' : 'llm';
		return {
			family,
			paletteGroup: 'models',
			badge: provider === 'claude' ? 'Claude' : provider === 'gpt' ? 'GPT' : 'model',
			signalType: undefined,
			provider,
			siStage: undefined,
		};
	}

	if (nodeType === 'custom_code' || isDiscoveredKind(metadata, input.kind)) {
		return {
			family: 'custom',
			paletteGroup: 'code',
			badge: metaString(metadata, 'chunk_kind') === 'block' ? 'repo block' : 'your code',
			signalType: undefined,
			provider,
			siStage: undefined,
		};
	}

	if (isNeuralKind(metadata, input.kind, signalType)) {
		const family: NodeFamily = signalType ? (`neural-${signalType}` as NodeFamily) : 'neural';
		return {
			family,
			paletteGroup: 'neural',
			badge: neuralBadge(nodeType, signalType),
			signalType,
			provider,
			siStage: undefined,
		};
	}

	return {
		family: familyFromNodeType(nodeType),
		paletteGroup: 'analysis',
		badge: analysisBadge(nodeType),
		signalType: undefined,
		provider,
		siStage: undefined,
	};
}

export function classifyKind(kind: NodeKind): NodeAppearance {
	return classifyAppearance({ nodeType: kind.node_type, metadata: kind.metadata, kind });
}

export function classifyNode(node: PipelineNode, kind: NodeKind | undefined): NodeAppearance {
	return classifyAppearance({ nodeType: node.node_type, metadata: node.metadata, kind });
}

export function isStarredLibraryKind(kind: NodeKind): boolean {
	return kind.metadata['starred'] === true && typeof kind.metadata['source_path'] === 'string';
}

/** Collapsible palette rails, empty groups omitted, `describe` order preserved inside each. */
export function groupPalette(kinds: readonly NodeKind[]): PaletteGroup[] {
	const buckets = new Map<PaletteGroupId, NodeKind[]>();
	for (const kind of kinds) {
		const group = classifyKind(kind).paletteGroup;
		const existing = buckets.get(group);
		if (existing) {
			existing.push(kind);
		} else {
			buckets.set(group, [kind]);
		}
	}

	const groups: PaletteGroup[] = [];
	for (const id of PALETTE_GROUP_ORDER) {
		const groupKinds = buckets.get(id);
		if (groupKinds && groupKinds.length > 0) {
			groups.push({ id, title: PALETTE_GROUP_TITLE[id], kinds: groupKinds });
		}
	}
	return groups;
}

export function kindMatchesQuery(kind: NodeKind, query: string): boolean {
	const needle = query.trim().toLowerCase();
	if (!needle) {
		return true;
	}
	const appearance = classifyKind(kind);
	const haystack = [
		kind.palette_label,
		kind.label,
		kind.kind,
		kind.node_type,
		kind.description,
		appearance.badge,
		appearance.siStage,
		appearance.signalType,
		appearance.provider,
		metaString(kind.metadata, 'source_path'),
	]
		.filter((part): part is string => typeof part === 'string' && part !== '')
		.join(' ')
		.toLowerCase();
	return haystack.includes(needle);
}

export interface ParamPreview {
	readonly name: string;
	readonly value: string;
}

/**
 * One compact line for the node card: a file path or model name if present,
 * otherwise the first overridden parameter.
 */
export function compactPreview(
	parameters: ReadonlyMap<string, PipelineParameter>,
	overrides: readonly { readonly name: string; readonly value: string }[],
): ParamPreview | undefined {
	for (const name of PREVIEW_PARAM_NAMES) {
		const parameter = parameters.get(name);
		if (!parameter) {
			continue;
		}
		const effective = parameter.value ?? parameter.default_value;
		const formatted = formatPreviewValue(effective);
		if (formatted) {
			return { name, value: formatted };
		}
	}

	const first = overrides[0];
	return first ? { name: first.name, value: first.value } : undefined;
}

export interface StarterChip {
	readonly id: string;
	readonly label: string;
	readonly hint: string;
	readonly kindIds: readonly string[];
}

export const STARTER_CHIPS: readonly StarterChip[] = [
	{
		id: 'eeg-psd',
		label: 'EEG PSD',
		hint: 'Load EEG → spectrum → plot',
		kindIds: ['neural_loader_eeg', 'analyzer_neural_spectrum', 'visualizer'],
	},
	{
		id: 'si-sort',
		label: 'SI spike sort',
		hint: 'Recording → preprocess → sort',
		kindIds: ['neural_si_recording', 'preprocessor_neural_si', 'analyzer_neural_si_sort'],
	},
	{
		id: 'csv-cluster',
		label: 'CSV → cluster',
		hint: 'Load CSV → normalize → cluster',
		kindIds: ['data_loader', 'preprocessor_normalize', 'analyzer_clustering'],
	},
];

export function availableStarters(registry: NodeKindRegistry): StarterChip[] {
	return STARTER_CHIPS.filter((chip) => chip.kindIds.every((id) => findKind(registry, id)));
}

/**
 * Tiny linear template: one node per kind, left to right, each connected to
 * the next through the first compatible ports (or the first ports if kinds
 * are `any`).
 */
export function starterIntents(
	chip: StarterChip,
	registry: NodeKindRegistry,
	origin: { readonly x: number; readonly y: number },
	newId: () => string = uuid4,
): PipelineIntent[] | undefined {
	const kinds: NodeKind[] = [];
	for (const id of chip.kindIds) {
		const kind = findKind(registry, id);
		if (!kind) {
			return undefined;
		}
		kinds.push(kind);
	}

	const intents: PipelineIntent[] = [];
	const ids: string[] = [];
	kinds.forEach((kind, index) => {
		const id = newId();
		ids.push(id);
		const node = nodeFromKind(kind, id, [Math.round(origin.x + index * 240), Math.round(origin.y)]);
		intents.push({ kind: 'addNode', nodeId: id, node: nodeToJson(node) });
	});

	for (let index = 0; index < kinds.length - 1; index += 1) {
		const source = kinds[index];
		const target = kinds[index + 1];
		const sourceId = ids[index];
		const targetId = ids[index + 1];
		if (!source || !target || !sourceId || !targetId) {
			continue;
		}
		const out = source.outputs[0];
		const inn = target.inputs[0];
		if (!out || !inn) {
			continue;
		}
		intents.push({
			kind: 'addEdge',
			edge: {
				source: sourceId,
				source_port: out.name,
				target: targetId,
				target_port: inn.name,
			},
		});
	}

	return intents;
}

export function inspectorParamGroups(
	parameters: readonly PipelineParameter[],
): { readonly id: string; readonly title: string; readonly parameters: PipelineParameter[] }[] {
	const files: PipelineParameter[] = [];
	const options: PipelineParameter[] = [];
	const numbers: PipelineParameter[] = [];
	const rest: PipelineParameter[] = [];

	for (const parameter of parameters) {
		if (parameter.param_type === 'file' || /(?:file|path|folder|recording)$/i.test(parameter.name)) {
			files.push(parameter);
		} else if (parameter.param_type === 'boolean' || parameter.param_type === 'dropdown') {
			options.push(parameter);
		} else if (parameter.param_type === 'number') {
			numbers.push(parameter);
		} else {
			rest.push(parameter);
		}
	}

	const groups = [
		{ id: 'files', title: 'Files', parameters: files },
		{ id: 'options', title: 'Options', parameters: options },
		{ id: 'values', title: 'Values', parameters: numbers },
		{ id: 'text', title: 'Text', parameters: rest },
	];
	return groups.filter((group) => group.parameters.length > 0);
}

function isSpikeInterface(metadata: JsonObject): boolean {
	return metaString(metadata, 'backend') === 'spikeinterface' || metaString(metadata, 'si_stage') !== undefined;
}

function isDiscoveredKind(metadata: JsonObject, kind: NodeKind | undefined): boolean {
	return typeof metadata['source_path'] === 'string' || (kind !== undefined && isStarredLibraryKind(kind));
}

function isNeuralKind(metadata: JsonObject, kind: NodeKind | undefined, signalType: NeuralSignalType | undefined): boolean {
	if (signalType) {
		return true;
	}
	const processor = metaString(metadata, 'processor_type') ?? '';
	const analyzer = metaString(metadata, 'analyzer_type') ?? '';
	if (processor.startsWith('neural_') || analyzer.startsWith('neural_')) {
		return true;
	}
	if (kind) {
		if (kind.kind.includes('neural_')) {
			return true;
		}
		const ports: readonly NodePort[] = [...kind.inputs, ...kind.outputs];
		if (ports.some((port) => NEURAL_SIGNALS.has(port.data_kind))) {
			return true;
		}
	}
	return false;
}

function neuralSignalOf(metadata: JsonObject, kind: NodeKind | undefined): NeuralSignalType | undefined {
	const declared = metaString(metadata, 'signal_type');
	if (isNeuralSignal(declared)) {
		return declared;
	}
	const analyzer = metaString(metadata, 'analyzer_type');
	if (analyzer === 'neural_spike') {
		return 'spike';
	}
	if (analyzer === 'neural_calcium') {
		return 'calcium';
	}
	if (kind) {
		const fromPort = [...kind.outputs, ...kind.inputs].find((port) => isNeuralSignal(port.data_kind));
		if (fromPort && isNeuralSignal(fromPort.data_kind)) {
			return fromPort.data_kind;
		}
	}
	return undefined;
}

function neuralBadge(nodeType: string, signalType: NeuralSignalType | undefined): string {
	const signal = signalType ? signalType.toUpperCase() : 'neural';
	if (nodeType === 'data_loader') {
		return signal;
	}
	if (nodeType === 'preprocessor') {
		return `${signal} filter`;
	}
	if (nodeType === 'analyzer') {
		return `${signal} analysis`;
	}
	return signal;
}

function familyFromNodeType(nodeType: string): NodeFamily {
	switch (nodeType) {
		case 'data_loader':
			return 'loader';
		case 'preprocessor':
			return 'preprocessor';
		case 'analyzer':
			return 'analyzer';
		case 'visualizer':
			return 'visualizer';
		case 'output':
			return 'output';
		case 'model_call':
			return 'llm';
		case 'custom_code':
			return 'custom';
		default:
			return 'unknown';
	}
}

function analysisBadge(nodeType: string): string {
	switch (nodeType) {
		case 'data_loader':
			return 'loader';
		case 'preprocessor':
			return 'preprocess';
		case 'analyzer':
			return 'analyze';
		case 'visualizer':
			return 'plot';
		case 'output':
			return 'output';
		default:
			return nodeType || 'node';
	}
}

function formatPreviewValue(value: JsonValue): string | undefined {
	if (value === null || value === '' || value === false) {
		return undefined;
	}
	if (typeof value === 'string') {
		return truncate(value, 42);
	}
	if (typeof value === 'number' || typeof value === 'boolean') {
		return String(value);
	}
	return truncate(JSON.stringify(value), 42);
}

function truncate(text: string, max: number): string {
	return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
