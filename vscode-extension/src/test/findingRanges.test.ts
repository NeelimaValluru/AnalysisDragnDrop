/**
 * The finding-to-range mapping is the part of diagnostics most likely to be
 * subtly wrong, and it is pure text in / offsets out, so it is tested here
 * against literal JSON rather than in an Extension Development Host.
 *
 * Assertions are written as "the span covers this substring" so they survive
 * reformatting of the fixtures below.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import { createFindingLocator, findSyntaxProblem, locateFinding } from '../findingRanges';
import type { Finding } from '../validation';

const FIXTURES = join(__dirname, '..', '..', 'fixtures');

const DOCUMENT = `{
  "version": 1,
  "nodes": {
    "node-a": { "id": "node-a", "node_type": "data_loader" },
    "node-b": { "id": "declared-differently", "node_type": "visualizer" }
  },
  "edges": [
    { "source": "node-a", "source_port": null, "target": "node-b", "target_port": null },
    { "source": "node-b", "source_port": null, "target": "ghost", "target_port": null },
    { "source": "node-b", "source_port": null, "target": "node-b", "target_port": null },
    ["node-a", "ghost"]
  ]
}
`;

function finding(partial: Partial<Finding>): Finding {
	return { severity: 'error', code: 'unknown', message: 'message', ...partial };
}

/** The exact text a span selects, which is what the editor will highlight. */
function selected(text: string, target: Finding): string {
	const span = locateFinding(text, target);
	return text.slice(span.offset, span.offset + span.length);
}

describe('locateFinding', () => {
	it('points a node finding at the key in the nodes object', () => {
		const span = locateFinding(DOCUMENT, finding({ code: 'unknown_node_type', node_id: 'node-a' }));

		assert.equal(DOCUMENT.slice(span.offset, span.offset + span.length), '"node-a"');
		// The key inside `nodes`, not the one referenced from an edge.
		assert.ok(span.offset > DOCUMENT.indexOf('"nodes"'));
		assert.ok(span.offset < DOCUMENT.indexOf('"edges"'));
	});

	it('falls back to the id property when no key matches', () => {
		const span = locateFinding(
			DOCUMENT,
			finding({ code: 'node_id_mismatch', node_id: 'declared-differently' }),
		);

		assert.equal(DOCUMENT.slice(span.offset, span.offset + span.length), '"declared-differently"');
	});

	it('points an edge finding at the whole edge element', () => {
		assert.equal(
			selected(DOCUMENT, finding({ code: 'malformed_edge', edge_index: 1 })),
			'{ "source": "node-b", "source_port": null, "target": "ghost", "target_port": null }',
		);
	});

	it('narrows a dangling edge to the endpoint naming the missing node', () => {
		// `ghost` is not in `nodes` at all, so resolving by node_id could only
		// ever fail; the useful location is the reference itself.
		assert.equal(
			selected(DOCUMENT, finding({ code: 'dangling_edge', edge_index: 1, node_id: 'ghost' })),
			'"ghost"',
		);
	});

	it('keeps a self loop on the whole edge rather than one of two matches', () => {
		assert.equal(
			selected(DOCUMENT, finding({ code: 'self_loop', edge_index: 2, node_id: 'node-b' })),
			'{ "source": "node-b", "source_port": null, "target": "node-b", "target_port": null }',
		);
	});

	it('narrows inside a legacy two-element edge', () => {
		assert.equal(
			selected(DOCUMENT, finding({ code: 'dangling_edge', edge_index: 3, node_id: 'ghost' })),
			'"ghost"',
		);
	});

	it('prefers the edge over the node when a finding carries both', () => {
		const span = locateFinding(
			DOCUMENT,
			finding({ code: 'self_loop', edge_index: 2, node_id: 'node-b' }),
		);

		assert.ok(span.offset > DOCUMENT.indexOf('"edges"'));
	});

	it('points container findings at the container key', () => {
		assert.equal(selected(DOCUMENT, finding({ code: 'malformed_edges' })), '"edges"');
		assert.equal(selected(DOCUMENT, finding({ code: 'malformed_nodes' })), '"nodes"');
		assert.equal(selected(DOCUMENT, finding({ code: 'empty_pipeline' })), '"nodes"');
	});

	it('falls back to the document start instead of dropping a finding', () => {
		for (const unplaceable of [
			finding({ code: 'cycle_detected', message: 'Pipeline contains cycles' }),
			finding({ code: 'dangling_edge', edge_index: 99, node_id: 'nobody' }),
			finding({ code: 'unknown_node_type', node_id: 'no-such-node' }),
		]) {
			const span = locateFinding(DOCUMENT, unplaceable);
			assert.ok(span.offset >= 0 && span.offset <= DOCUMENT.length, unplaceable.code);
		}
	});

	it('points an out-of-range edge index at the edges key', () => {
		assert.equal(selected(DOCUMENT, finding({ code: 'malformed_edge', edge_index: 99 })), '"edges"');
	});

	it('never returns a span outside the text', () => {
		const locate = createFindingLocator(DOCUMENT);
		for (const index of [0, 1, 2, 3, 4, 500]) {
			const span = locate(finding({ code: 'malformed_edge', edge_index: index }));
			assert.ok(span.offset >= 0);
			assert.ok(span.offset + span.length <= DOCUMENT.length);
		}
	});

	it('degrades to the document start on unparseable text', () => {
		const span = locateFinding('{ "nodes": ', finding({ code: 'cycle_detected' }));
		assert.deepEqual(span, { offset: 0, length: 0 });
	});
});

describe('locateFinding against the shipped fixtures', () => {
	const broken = readFileSync(join(FIXTURES, 'broken.pipeline'), 'utf8');

	// Exactly the findings the real CLI reports for this file.
	const reported: readonly Finding[] = [
		{
			severity: 'error',
			code: 'unknown_node_type',
			message: "Node 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' has unknown node_type 'not_a_real_type'",
			node_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
		},
		{
			severity: 'error',
			code: 'node_id_mismatch',
			message: "Node stored under key 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' declares id ...",
			node_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
		},
		{
			severity: 'error',
			code: 'dangling_edge',
			message: "Edge 1 references missing target node 'ghost'",
			edge_index: 1,
			node_id: 'ghost',
		},
		{
			severity: 'error',
			code: 'self_loop',
			message: "Edge 2 connects node 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' to itself",
			edge_index: 2,
			node_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
		},
		{
			severity: 'warning',
			code: 'duplicate_edge',
			message: 'Edge 3 duplicates an earlier edge',
			edge_index: 3,
		},
		{
			severity: 'error',
			code: 'malformed_edge',
			message: 'Edge 5 is neither an edge object nor a [source, target] pair',
			edge_index: 5,
		},
	];

	it('places every finding the CLI reports on a nonempty span', () => {
		const locate = createFindingLocator(broken);

		for (const item of reported) {
			const span = locate(item);
			assert.ok(span.length > 0, `${item.code} landed on an empty span`);
			assert.ok(span.offset + span.length <= broken.length, `${item.code} ran past the text`);
		}
	});

	it('puts the malformed edge on the bare 42', () => {
		const span = locateFinding(broken, reported[5]!);
		assert.equal(broken.slice(span.offset, span.offset + span.length), '42');
	});

	it('puts the dangling edge on the ghost reference', () => {
		const span = locateFinding(broken, reported[2]!);
		assert.equal(broken.slice(span.offset, span.offset + span.length), '"ghost"');
	});

	it('finds no syntax problem in either shipped fixture', () => {
		assert.equal(findSyntaxProblem(broken), undefined);
		assert.equal(findSyntaxProblem(readFileSync(join(FIXTURES, 'valid.pipeline'), 'utf8')), undefined);
	});
});

describe('findSyntaxProblem', () => {
	it('locates an unterminated object', () => {
		const problem = findSyntaxProblem('{ "version": 1, ');
		assert.ok(problem);
		assert.ok(problem.offset > 0);
	});

	it('rejects JSONC niceties, because the CLI uses json.load', () => {
		assert.ok(findSyntaxProblem('{ "version": 1, } // trailing'));
		assert.ok(findSyntaxProblem('{ "nodes": {}, }'));
	});

	it('reports an empty document rather than pretending it parses', () => {
		assert.ok(findSyntaxProblem('   \n  '));
	});

	it('accepts well-formed JSON', () => {
		assert.equal(findSyntaxProblem('{ "version": 1, "nodes": {}, "edges": [] }'), undefined);
	});
});
