/**
 * Canvas run progress: topo order, NDJSON parsing, and a coarse synthetic clock.
 *
 * ## Wire protocol
 *
 * While `cli run` executes, the host posts this message to the webview (and
 * accepts the same shape as a single JSON line on the CLI's stderr or stdout):
 *
 * ```
 * { "type": "runProgress", "nodeId": "<id>", "state": "pending"|"running"|"ok"|"error" }
 * ```
 *
 * Snake_case aliases `run_progress` / `node_id` are also accepted, so a future
 * Python emitter can match the rest of the CLI without a translation layer.
 *
 * Today's CLI does not yet stream per-node events: it prints human logs on
 * stderr and a JSON receipt on stdout after the child exits. Until it does,
 * the host synthesises progress by walking node ids in topological order
 * (pending → pulse running → ok/error on process exit). The first real
 * `runProgress` line cancels the synthesizer so the two cannot fight.
 */

import type { PipelineDocument } from './pipeline/document';
import type { RunNodeState, RunProgressEvent } from './pipeline/protocol';

const STATES = new Set<RunNodeState>(['pending', 'running', 'ok', 'error']);

export interface ProgressClock {
	setInterval(handler: () => void, ms: number): unknown;
	clearInterval(id: unknown): void;
}

const defaultClock: ProgressClock = {
	setInterval: (handler, ms) => setInterval(handler, ms),
	clearInterval: (id) => {
		clearInterval(id as ReturnType<typeof setInterval>);
	},
};

/** Default pulse: long enough to read, short enough to finish a small DAG. */
export const SYNTHETIC_PROGRESS_MS = 480;

/**
 * Node ids in topological order, stable for a given document.
 *
 * Isolated nodes keep document order. A cycle cannot be ordered; leftover ids
 * are appended in document order so every node still gets a progress slot.
 */
export function topoNodeIds(document: PipelineDocument): string[] {
	const incoming = new Map<string, number>();
	const outgoing = new Map<string, string[]>();

	for (const id of document.nodes.keys()) {
		incoming.set(id, 0);
		outgoing.set(id, []);
	}

	for (const edge of document.edges) {
		if (edge.form === 'unknown') {
			continue;
		}
		if (!document.nodes.has(edge.source) || !document.nodes.has(edge.target)) {
			continue;
		}
		outgoing.get(edge.source)?.push(edge.target);
		incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
	}

	const ready: string[] = [];
	for (const id of document.nodes.keys()) {
		if ((incoming.get(id) ?? 0) === 0) {
			ready.push(id);
		}
	}

	const ordered: string[] = [];
	const placed = new Set<string>();
	while (ready.length > 0) {
		const id = ready.shift();
		if (id === undefined || placed.has(id)) {
			continue;
		}
		placed.add(id);
		ordered.push(id);
		for (const next of outgoing.get(id) ?? []) {
			const remaining = (incoming.get(next) ?? 1) - 1;
			incoming.set(next, remaining);
			if (remaining === 0) {
				ready.push(next);
			}
		}
	}

	for (const id of document.nodes.keys()) {
		if (!placed.has(id)) {
			ordered.push(id);
		}
	}

	return ordered;
}

/**
 * Parses one NDJSON line as a run-progress event.
 *
 * Pretty-printed CLI receipts are multi-line objects whose first line is `{`;
 * that is not valid JSON on its own, so they cannot be mistaken for progress.
 */
export function parseRunProgressLine(line: string): RunProgressEvent | undefined {
	const trimmed = line.trim();
	if (!trimmed.startsWith('{')) {
		return undefined;
	}

	let value: unknown;
	try {
		value = JSON.parse(trimmed) as unknown;
	} catch {
		return undefined;
	}

	if (!isRecord(value)) {
		return undefined;
	}

	const type = value['type'];
	if (type !== 'runProgress' && type !== 'run_progress') {
		return undefined;
	}

	const nodeId =
		typeof value['nodeId'] === 'string'
			? value['nodeId']
			: typeof value['node_id'] === 'string'
				? value['node_id']
				: undefined;
	const state = value['state'];
	if (!nodeId || typeof state !== 'string' || !STATES.has(state as RunNodeState)) {
		return undefined;
	}

	return { nodeId, state: state as RunNodeState };
}

export interface RunProgressDriver {
	/** Feed a stdout or stderr chunk; complete lines are parsed for events. */
	pushChunk(chunk: string): void;
	/** Mark remaining nodes terminal. Safe to call more than once. */
	finish(ok: boolean): void;
	dispose(): void;
}

/**
 * Starts every node `pending`, pulses `running` through `nodeIds` on a timer,
 * and completes on `finish`. A parsed CLI event disables the synthesizer.
 */
export function attachRunProgress(
	nodeIds: readonly string[],
	post: (event: RunProgressEvent) => void,
	options: { intervalMs?: number; clock?: ProgressClock } = {},
): RunProgressDriver {
	const clock = options.clock ?? defaultClock;
	const intervalMs = options.intervalMs ?? SYNTHETIC_PROGRESS_MS;
	const states = new Map<string, RunNodeState>();
	let synthetic = true;
	let pulseIndex = 0;
	let timer: unknown;
	let buffer = '';
	let finished = false;

	const emit = (nodeId: string, state: RunNodeState): void => {
		if (states.get(nodeId) === state) {
			return;
		}
		states.set(nodeId, state);
		post({ nodeId, state });
	};

	for (const id of nodeIds) {
		emit(id, 'pending');
	}

	const first = nodeIds[0];
	if (first !== undefined) {
		emit(first, 'running');
	}

	const stopTimer = (): void => {
		if (timer !== undefined) {
			clock.clearInterval(timer);
			timer = undefined;
		}
	};

	if (nodeIds.length > 1) {
		timer = clock.setInterval(() => {
			if (!synthetic || finished) {
				return;
			}
			const current = nodeIds[pulseIndex];
			if (current !== undefined && states.get(current) === 'running') {
				emit(current, 'ok');
			}
			pulseIndex += 1;
			const next = nodeIds[pulseIndex];
			if (next !== undefined) {
				emit(next, 'running');
			} else {
				stopTimer();
			}
		}, intervalMs);
	}

	const consumeLine = (line: string): void => {
		const event = parseRunProgressLine(line);
		if (!event) {
			return;
		}
		synthetic = false;
		stopTimer();
		emit(event.nodeId, event.state);
	};

	return {
		pushChunk(chunk: string) {
			if (finished) {
				return;
			}
			buffer += chunk;
			for (const line of takeCompleteLines()) {
				consumeLine(line);
			}
		},
		finish(ok: boolean) {
			if (finished) {
				return;
			}
			finished = true;
			stopTimer();
			if (buffer.trim()) {
				consumeLine(buffer);
			}
			buffer = '';
			const terminal: RunNodeState = ok ? 'ok' : 'error';
			for (const id of nodeIds) {
				const current = states.get(id);
				if (current === 'ok' || current === 'error') {
					continue;
				}
				emit(id, terminal);
			}
		},
		dispose() {
			stopTimer();
		},
	};

	function takeCompleteLines(): string[] {
		const lines: string[] = [];
		for (;;) {
			const match = /\r?\n/.exec(buffer);
			if (!match || match.index === undefined) {
				return lines;
			}
			const matched = match[0];
			if (matched === undefined) {
				return lines;
			}
			lines.push(buffer.slice(0, match.index));
			buffer = buffer.slice(match.index + matched.length);
		}
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}
