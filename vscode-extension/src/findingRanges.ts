/**
 * Maps a validation finding onto the span of `.pipeline` text it is about.
 *
 * The CLI reports findings in terms of the document's *logical* structure — a
 * node id, an index into `edges` — because it works on parsed JSON and has no
 * idea where anything sits in the file. Turning that back into a range means
 * re-parsing the same text with offsets, which is what `jsonc-parser` is for:
 * it produces a tree of `{offset, length}` nodes and tolerates the malformed
 * input we are specifically here to complain about.
 *
 * Nothing here imports `vscode`; the caller converts offsets to positions.
 */

import { findNodeAtLocation, parseTree, printParseErrorCode } from 'jsonc-parser';
import type { Node, ParseError } from 'jsonc-parser';
import type { Finding } from './validation';

/** A half-open span of the source text, in UTF-16 code units. */
export interface OffsetSpan {
	readonly offset: number;
	readonly length: number;
}

export interface SyntaxProblem extends OffsetSpan {
	readonly message: string;
}

/** Used when a finding cannot be placed: the document's first character. */
export const DOCUMENT_START: OffsetSpan = { offset: 0, length: 0 };

/**
 * Codes that describe a whole container rather than one of its members, and
 * the property they should highlight.
 */
const CONTAINER_FOR_CODE: Readonly<Record<string, string>> = {
	malformed_nodes: 'nodes',
	malformed_edges: 'edges',
	empty_pipeline: 'nodes',
};

/**
 * Resolution order, most specific first:
 *
 *   1. `edge_index` — the element of `edges` at that index, narrowed to the
 *      `source`/`target` value matching `node_id` when exactly one does. Edge
 *      findings are checked first because `dangling_edge` carries the id of a
 *      node that by definition is *not* in the document.
 *   2. `node_id` — the key in `nodes`, or failing that a node whose `id`
 *      property holds the value (so a `node_id_mismatch` still lands somewhere).
 *   3. the container the code implicates, for findings with neither.
 *   4. the start of the document.
 */
export function locateFinding(text: string, finding: Finding): OffsetSpan {
	return createFindingLocator(text)(finding);
}

/**
 * Parses once and returns a locator for many findings, which is the shape the
 * diagnostics layer wants: a validate run reports a whole batch against a
 * single revision of the text.
 */
export function createFindingLocator(text: string): (finding: Finding) => OffsetSpan {
	const root = parseTree(text);
	const textLength = text.length;

	return (finding) => clampSpan(root ? resolve(root, finding) : DOCUMENT_START, textLength);
}

function resolve(root: Node, finding: Finding): OffsetSpan {
	if (typeof finding.edge_index === 'number') {
		const located = locateEdge(root, finding.edge_index, finding.node_id);
		if (located) {
			return located;
		}
	}

	if (finding.node_id !== undefined) {
		const located = locateNode(root, finding.node_id);
		if (located) {
			return located;
		}
	}

	const container = CONTAINER_FOR_CODE[finding.code];
	if (container) {
		const located = propertyKeySpan(root, container);
		if (located) {
			return located;
		}
	}

	return DOCUMENT_START;
}

/**
 * First strict-JSON syntax error in the text, if any.
 *
 * Checking locally rather than asking the CLI keeps a half-typed file from
 * costing a Python process, and gives a real offset instead of the line/column
 * buried in CPython's decoder message. Comments and trailing commas are errors
 * here because `.pipeline` files are read by `json.load`, not by VS Code.
 */
export function findSyntaxProblem(text: string): SyntaxProblem | undefined {
	if (!text.trim()) {
		return { offset: 0, length: 0, message: 'The pipeline file is empty.' };
	}

	const errors: ParseError[] = [];
	parseTree(text, errors, { disallowComments: true, allowTrailingComma: false });

	const first = errors[0];
	if (!first) {
		return undefined;
	}

	return {
		offset: first.offset,
		length: Math.max(first.length, 1),
		message: `${printParseErrorCode(first.error)} — this file must be strict JSON.`,
	};
}

/** Clamps a span so it always names at least one character inside the text. */
export function clampSpan(span: OffsetSpan, textLength: number): OffsetSpan {
	const offset = Math.max(0, Math.min(span.offset, textLength));
	const length = Math.max(0, Math.min(span.length, textLength - offset));
	return { offset, length };
}

function locateEdge(root: Node, index: number, nodeId: string | undefined): OffsetSpan | undefined {
	const element = findNodeAtLocation(root, ['edges', index]);
	if (!element) {
		return propertyKeySpan(root, 'edges');
	}

	const narrowed = nodeId === undefined ? undefined : narrowToEndpoint(element, nodeId);
	return span(narrowed ?? element);
}

/**
 * The `source` or `target` inside an edge that names `nodeId`. Returns nothing
 * when both do — a self-loop is about the edge, not one of its ends — or when
 * the edge is too malformed to have endpoints.
 */
function narrowToEndpoint(edge: Node, nodeId: string): Node | undefined {
	const candidates: Node[] = [];

	if (edge.type === 'object') {
		for (const property of edge.children ?? []) {
			const key = property.children?.[0];
			const value = property.children?.[1];
			if (
				value &&
				(key?.value === 'source' || key?.value === 'target') &&
				value.value === nodeId
			) {
				candidates.push(value);
			}
		}
	} else if (edge.type === 'array') {
		// Legacy two-element `[source, target]` edges are still accepted.
		for (const child of edge.children ?? []) {
			if (child.value === nodeId) {
				candidates.push(child);
			}
		}
	}

	return candidates.length === 1 ? candidates[0] : undefined;
}

function locateNode(root: Node, nodeId: string): OffsetSpan | undefined {
	const nodes = findNodeAtLocation(root, ['nodes']);
	if (!nodes || nodes.type !== 'object') {
		return undefined;
	}

	for (const property of nodes.children ?? []) {
		const key = property.children?.[0];
		if (key?.value === nodeId) {
			return span(key);
		}
	}

	// No such key: the id may only appear as a node's `id` property, which is
	// exactly the case a `node_id_mismatch` describes from the other side.
	for (const property of nodes.children ?? []) {
		const value = property.children?.[1];
		const id = value ? findNodeAtLocation(value, ['id']) : undefined;
		if (id?.value === nodeId) {
			return span(id);
		}
	}

	return undefined;
}

/** The quoted key of a top-level property, e.g. `"edges"`. */
function propertyKeySpan(root: Node, name: string): OffsetSpan | undefined {
	const value = findNodeAtLocation(root, [name]);
	const key = value?.parent?.children?.[0];
	return key ? span(key) : undefined;
}

function span(node: Node): OffsetSpan {
	return { offset: node.offset, length: node.length };
}
