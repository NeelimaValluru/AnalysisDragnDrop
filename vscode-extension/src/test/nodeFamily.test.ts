/**
 * Visual family, palette grouping and starter templates — derived from
 * `node_type` + metadata, never from hard-coded factory names in the canvas.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { PipelineParameter } from '../pipeline/document';
import {
	classifyKind,
	compactPreview,
	groupPalette,
	kindMatchesQuery,
	starterIntents,
	STARTER_CHIPS,
} from '../pipeline/nodeFamily';
import { parseDescribeEnvelope, paletteKinds } from '../pipeline/nodeKinds';
import type { NodeKind, NodeKindRegistry } from '../pipeline/nodeKinds';
import { toFlowModel } from '../pipeline/graphModel';
import type { PipelineDocument, PipelineNode } from '../pipeline/document';

function kind(partial: Partial<NodeKind> & Pick<NodeKind, 'kind' | 'node_type'>): NodeKind {
	return {
		palette_label: partial.palette_label ?? partial.kind,
		in_palette: partial.in_palette ?? true,
		label: partial.label ?? partial.kind,
		description: partial.description ?? '',
		metadata: partial.metadata ?? {},
		parameters: partial.parameters ?? [],
		inputs: partial.inputs ?? [],
		outputs: partial.outputs ?? [],
		...partial,
	};
}

function registryOf(kinds: NodeKind[]): NodeKindRegistry {
	return {
		schemaVersion: 1,
		analysisGuiVersion: '0.1.0',
		nodeTypes: [...new Set(kinds.map((entry) => entry.node_type))],
		portDataKinds: ['any', 'table', 'eeg', 'spike'],
		kinds,
	};
}

describe('classifyKind', () => {
	it('colours a CSV loader as analysis/loader, not neural', () => {
		const appearance = classifyKind(kind({ kind: 'data_loader', node_type: 'data_loader', metadata: { file_format: 'csv' } }));
		assert.equal(appearance.family, 'loader');
		assert.equal(appearance.paletteGroup, 'analysis');
	});

	it('uses metadata.signal_type for neural loaders', () => {
		const appearance = classifyKind(
			kind({
				kind: 'neural_loader_eeg',
				node_type: 'data_loader',
				metadata: { file_format: 'csv', signal_type: 'eeg' },
			}),
		);
		assert.equal(appearance.family, 'neural-eeg');
		assert.equal(appearance.paletteGroup, 'neural');
		assert.equal(appearance.signalType, 'eeg');
	});

	it('puts SpikeInterface nodes on their own rail even when they are loaders', () => {
		const appearance = classifyKind(
			kind({
				kind: 'neural_si_recording',
				node_type: 'data_loader',
				metadata: { signal_type: 'spike', backend: 'spikeinterface', si_stage: 'recording' },
			}),
		);
		assert.equal(appearance.family, 'spikeinterface');
		assert.equal(appearance.paletteGroup, 'spikeinterface');
		assert.equal(appearance.siStage, 'recording');
	});

	it('distinguishes Claude from GPT via metadata.provider', () => {
		const claude = classifyKind(
			kind({ kind: 'model_claude', node_type: 'model_call', metadata: { provider: 'claude' } }),
		);
		const gpt = classifyKind(kind({ kind: 'model_gpt', node_type: 'model_call', metadata: { provider: 'gpt' } }));
		assert.equal(claude.family, 'llm-claude');
		assert.equal(gpt.family, 'llm-gpt');
		assert.equal(claude.paletteGroup, 'models');
	});

	it('groups custom / discovered code under Your code', () => {
		const custom = classifyKind(kind({ kind: 'custom_code', node_type: 'custom_code' }));
		const discovered = classifyKind(
			kind({
				kind: 'repo.bandpass',
				node_type: 'custom_code',
				in_palette: false,
				metadata: { source_path: '/lib/filters.py', starred: true },
			}),
		);
		assert.equal(custom.paletteGroup, 'code');
		assert.equal(discovered.paletteGroup, 'code');
		assert.equal(discovered.family, 'custom');
	});

	it('treats neural_filter as neural even without signal_type', () => {
		const appearance = classifyKind(
			kind({
				kind: 'preprocessor_neural_filter',
				node_type: 'preprocessor',
				metadata: { processor_type: 'neural_filter' },
			}),
		);
		assert.equal(appearance.paletteGroup, 'neural');
		assert.equal(appearance.family, 'neural');
	});
});

describe('groupPalette', () => {
	it('emits Neural / SpikeInterface / Analysis / Models / Your code in that order', () => {
		const groups = groupPalette([
			kind({ kind: 'data_loader', node_type: 'data_loader', palette_label: 'Load CSV' }),
			kind({
				kind: 'neural_loader_eeg',
				node_type: 'data_loader',
				metadata: { signal_type: 'eeg' },
				palette_label: 'Load EEG',
			}),
			kind({
				kind: 'neural_si_recording',
				node_type: 'data_loader',
				metadata: { backend: 'spikeinterface', si_stage: 'recording' },
				palette_label: 'SI Recording',
			}),
			kind({ kind: 'model_claude', node_type: 'model_call', metadata: { provider: 'claude' } }),
			kind({ kind: 'custom_code', node_type: 'custom_code' }),
		]);
		assert.deepEqual(
			groups.map((group) => group.id),
			['neural', 'spikeinterface', 'analysis', 'models', 'code'],
		);
		assert.equal(groups[0]?.title, 'Neural');
		assert.equal(groups[1]?.title, 'SpikeInterface');
	});

	it('omits empty groups', () => {
		const groups = groupPalette([kind({ kind: 'visualizer', node_type: 'visualizer' })]);
		assert.deepEqual(
			groups.map((group) => group.id),
			['analysis'],
		);
	});
});

describe('kindMatchesQuery', () => {
	it('matches label, kind id and signal type', () => {
		const eeg = kind({
			kind: 'neural_loader_eeg',
			node_type: 'data_loader',
			palette_label: 'Load EEG',
			metadata: { signal_type: 'eeg' },
		});
		assert.equal(kindMatchesQuery(eeg, 'eeg'), true);
		assert.equal(kindMatchesQuery(eeg, 'csv'), false);
	});
});

describe('paletteKinds / describe envelope', () => {
	it('merges discovered_kinds and offers starred ones', () => {
		const registry = parseDescribeEnvelope({
			node_kinds: [
				{
					kind: 'custom_code',
					palette_label: 'Custom Code',
					node_type: 'custom_code',
					label: 'Custom Code',
					description: '',
					metadata: {},
					parameters: [],
					ports: { inputs: [], outputs: [] },
				},
			],
			discovered_kinds: [
				{
					kind: 'lib.bandpass',
					palette_label: 'bandpass',
					in_palette: false,
					node_type: 'custom_code',
					label: 'bandpass',
					description: '',
					metadata: { source_path: '/lib/filters.py', starred: true },
					parameters: [],
					ports: { inputs: [], outputs: [] },
				},
				{
					kind: 'lib.other',
					palette_label: 'other',
					in_palette: false,
					node_type: 'custom_code',
					label: 'other',
					description: '',
					metadata: { source_path: '/lib/other.py', starred: false },
					parameters: [],
					ports: { inputs: [], outputs: [] },
				},
			],
		});
		assert.ok(registry);
		assert.equal(registry.kinds.length, 3);
		const offered = paletteKinds(registry).map((entry) => entry.kind);
		assert.deepEqual(offered, ['custom_code', 'lib.bandpass']);
	});
});

describe('compactPreview', () => {
	it('prefers an overridden file_path', () => {
		const parameters = new Map<string, PipelineParameter>([
			[
				'file_path',
				{
					name: 'file_path',
					param_type: 'file',
					default_value: null,
					value: '/data/eeg.csv',
					description: '',
					options: [],
					extra: {},
				},
			],
		]);
		const preview = compactPreview(parameters, [{ name: 'file_path', value: '/data/eeg.csv' }]);
		assert.deepEqual(preview, { name: 'file_path', value: '/data/eeg.csv' });
	});
});

describe('starterIntents', () => {
	it('emits addNode then addEdge intents for a known chip', () => {
		const chip = STARTER_CHIPS.find((entry) => entry.id === 'csv-cluster');
		assert.ok(chip);
		const registry = registryOf([
			kind({
				kind: 'data_loader',
				node_type: 'data_loader',
				outputs: [{ name: 'data', label: 'Data', data_kind: 'table', required: false, description: '' }],
			}),
			kind({
				kind: 'preprocessor_normalize',
				node_type: 'preprocessor',
				inputs: [{ name: 'data', label: 'Data', data_kind: 'table', required: true, description: '' }],
				outputs: [{ name: 'data', label: 'Data', data_kind: 'table', required: false, description: '' }],
			}),
			kind({
				kind: 'analyzer_clustering',
				node_type: 'analyzer',
				inputs: [{ name: 'data', label: 'Data', data_kind: 'table', required: true, description: '' }],
				outputs: [{ name: 'result', label: 'Result', data_kind: 'table', required: false, description: '' }],
			}),
		]);
		let n = 0;
		const intents = starterIntents(chip, registry, { x: 10, y: 20 }, () => `id-${String((n += 1))}`);
		assert.ok(intents);
		assert.equal(intents.filter((intent) => intent.kind === 'addNode').length, 3);
		assert.equal(intents.filter((intent) => intent.kind === 'addEdge').length, 2);
		const first = intents[0];
		assert.equal(first?.kind, 'addNode');
		if (first?.kind === 'addNode') {
			assert.deepEqual(first.node['position'], [10, 20]);
		}
	});
});

describe('toFlowModel appearance', () => {
	it('stamps family, badge and severity onto node data', () => {
		const node: PipelineNode = {
			id: 'n1',
			node_type: 'data_loader',
			label: 'Load EEG',
			description: '',
			parameters: new Map(),
			position: [0, 0],
			metadata: { signal_type: 'eeg' },
			extra: {},
		};
		const document: PipelineDocument = {
			version: 1,
			nodes: new Map([['n1', node]]),
			edges: [],
			extra: {},
		};
		const registry = registryOf([
			kind({
				kind: 'neural_loader_eeg',
				node_type: 'data_loader',
				palette_label: 'Load EEG',
				metadata: { signal_type: 'eeg' },
			}),
		]);
		const model = toFlowModel(document, registry, new Map([['n1', 'error']]));
		assert.equal(model.nodes[0]?.data.family, 'neural-eeg');
		assert.equal(model.nodes[0]?.data.severity, 'error');
		assert.equal(model.nodes[0]?.data.kindLabel, 'Load EEG');
	});
});
