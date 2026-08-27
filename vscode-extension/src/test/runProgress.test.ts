/**
 * Run-progress protocol: topo order, NDJSON parse, synthetic clock.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { PipelineDocument, PipelineNode } from '../pipeline/document';
import type { RunProgressEvent } from '../pipeline/protocol';
import { attachRunProgress, parseRunProgressLine, topoNodeIds } from '../runProgress';

function node(id: string): PipelineNode {
	return {
		id,
		node_type: 'custom_code',
		label: id,
		description: '',
		parameters: new Map(),
		position: [0, 0],
		metadata: {},
		extra: {},
	};
}

function document(ids: readonly string[], edges: ReadonlyArray<readonly [string, string]>): PipelineDocument {
	return {
		version: 1,
		nodes: new Map(ids.map((id) => [id, node(id)])),
		edges: edges.map(([source, target]) => ({
			form: 'object',
			source,
			source_port: null,
			target,
			target_port: null,
			extra: {},
		})),
		extra: {},
	};
}

describe('topoNodeIds', () => {
	it('orders a chain by edges, keeping document order for ties', () => {
		const ids = topoNodeIds(document(['a', 'b', 'c'], [['a', 'b'], ['b', 'c']]));
		assert.deepEqual(ids, ['a', 'b', 'c']);
	});

	it('places independent roots in document order', () => {
		const ids = topoNodeIds(document(['left', 'right', 'sink'], [['left', 'sink'], ['right', 'sink']]));
		assert.deepEqual(ids, ['left', 'right', 'sink']);
	});

	it('appends cycle leftovers instead of dropping them', () => {
		const ids = topoNodeIds(document(['start', 'loop-a', 'loop-b'], [['loop-a', 'loop-b'], ['loop-b', 'loop-a']]));
		assert.deepEqual(ids, ['start', 'loop-a', 'loop-b']);
	});

	it('skips dangling and unknown edges', () => {
		const doc: PipelineDocument = {
			version: 1,
			nodes: new Map([
				['a', node('a')],
				['b', node('b')],
			]),
			edges: [
				{ form: 'object', source: 'a', source_port: null, target: 'ghost', target_port: null, extra: {} },
				{ form: 'unknown', raw: 42 },
				{ form: 'object', source: 'a', source_port: null, target: 'b', target_port: null, extra: {} },
			],
			extra: {},
		};
		assert.deepEqual(topoNodeIds(doc), ['a', 'b']);
	});
});

describe('parseRunProgressLine', () => {
	it('accepts the documented camelCase object', () => {
		assert.deepEqual(parseRunProgressLine('{"type":"runProgress","nodeId":"n1","state":"running"}'), {
			nodeId: 'n1',
			state: 'running',
		});
	});

	it('accepts snake_case aliases from a future Python emitter', () => {
		assert.deepEqual(parseRunProgressLine('{"type":"run_progress","node_id":"n1","state":"ok"}'), {
			nodeId: 'n1',
			state: 'ok',
		});
	});

	it('ignores a pretty-printed receipt opening brace', () => {
		assert.equal(parseRunProgressLine('{'), undefined);
	});

	it('ignores a compact receipt that is not progress', () => {
		assert.equal(
			parseRunProgressLine('{"schema_version":1,"status":"ok","command":"run","exit_code":0}'),
			undefined,
		);
	});

	it('rejects an unknown state', () => {
		assert.equal(parseRunProgressLine('{"type":"runProgress","nodeId":"n1","state":"skipped"}'), undefined);
	});
});

describe('attachRunProgress', () => {
	it('starts pending then running, pulses on the clock, and completes on finish', () => {
		const posted: RunProgressEvent[] = [];
		const timers: Array<{ handler: () => void }> = [];
		const driver = attachRunProgress(['a', 'b', 'c'], (event) => posted.push(event), {
			intervalMs: 10,
			clock: {
				setInterval(handler) {
					timers.push({ handler });
					return timers.length;
				},
				clearInterval() {
					/* the test drives ticks by hand */
				},
			},
		});

		assert.deepEqual(posted, [
			{ nodeId: 'a', state: 'pending' },
			{ nodeId: 'b', state: 'pending' },
			{ nodeId: 'c', state: 'pending' },
			{ nodeId: 'a', state: 'running' },
		]);

		timers[0]?.handler();
		assert.deepEqual(posted.at(-2), { nodeId: 'a', state: 'ok' });
		assert.deepEqual(posted.at(-1), { nodeId: 'b', state: 'running' });

		driver.finish(true);
		assert.equal(posted.filter((event) => event.nodeId === 'c' && event.state === 'ok').length, 1);
		assert.equal(posted.filter((event) => event.state === 'error').length, 0);
		driver.dispose();
	});

	it('marks remaining nodes error when the process fails', () => {
		const posted: RunProgressEvent[] = [];
		const driver = attachRunProgress(['a', 'b'], (event) => posted.push(event), {
			clock: {
				setInterval() {
					return 1;
				},
				clearInterval() {
					/* unused */
				},
			},
		});
		driver.finish(false);
		assert.deepEqual(
			posted.filter((event) => event.state === 'error'),
			[
				{ nodeId: 'a', state: 'error' },
				{ nodeId: 'b', state: 'error' },
			],
		);
		driver.dispose();
	});

	it('lets a CLI JSON line cancel the synthesizer', () => {
		const posted: RunProgressEvent[] = [];
		let ticks = 0;
		const driver = attachRunProgress(['a', 'b'], (event) => posted.push(event), {
			clock: {
				setInterval(handler) {
					return { fire: handler };
				},
				clearInterval() {
					ticks += 1;
				},
			},
		});

		driver.pushChunk('{"type":"runProgress","nodeId":"a","state":"ok"}\n');
		assert.ok(ticks >= 1);
		const before = posted.length;
		driver.pushChunk('noise\n{"type":"runProgress","nodeId":"b","state":"running"}\n');
		assert.deepEqual(posted.slice(before), [{ nodeId: 'b', state: 'running' }]);
		driver.dispose();
	});

	it('reassembles a progress line split across chunks', () => {
		const posted: RunProgressEvent[] = [];
		const driver = attachRunProgress(['n1'], (event) => posted.push(event));
		driver.pushChunk('{"type":"runProgress","nodeId":"n1",');
		driver.pushChunk('"state":"ok"}\n');
		assert.ok(posted.some((event) => event.nodeId === 'n1' && event.state === 'ok'));
		driver.dispose();
	});
});
