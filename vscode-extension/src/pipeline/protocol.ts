/**
 * The messages that cross the webview boundary.
 *
 * `Webview.postMessage` is a JSON channel — VS Code stringifies, it is not a
 * structured clone — so every payload here is plain JSON. In particular the
 * document travels as its raw parsed value and each side runs
 * `parsePipelineDocument` on it, rather than trying to ship a model whose
 * ordered containers are `Map`s.
 *
 * This file is the whole contract. It imports nothing from `vscode` and nothing
 * from React: the host adapter and the canvas adapter each import it, and
 * neither imports the other.
 */

import type { JsonValue } from './document';
import type { PipelineIntent } from './intents';
import type { NodeSeverity } from './graphModel';

export interface NodeMark {
	readonly nodeId: string;
	readonly severity: NodeSeverity;
}

/**
 * Per-node execution state posted while `cli run` is in flight.
 *
 * The same JSON object is the NDJSON line protocol on the CLI's stderr
 * (`type` + `nodeId` + `state`). Host-synthesised pulses use this shape too,
 * so a future per-node emitter does not need a second message.
 */
export type RunNodeState = 'pending' | 'running' | 'ok' | 'error';

export interface RunProgressEvent {
	readonly nodeId: string;
	readonly state: RunNodeState;
}

export type HostToWebview =
	/** The node-kind registry from `describe --json`. Sent once per webview load. */
	| { readonly type: 'registry'; readonly payload: JsonValue }
	/**
	 * The document as it now stands. `revision` is the `TextDocument` version it
	 * was read from; it comes back on nothing, and exists so the webview can log
	 * out-of-order delivery rather than guess.
	 */
	| { readonly type: 'document'; readonly payload: JsonValue; readonly revision: number }
	/**
	 * The text is not a document right now. The canvas keeps showing the last
	 * good graph, dimmed, behind the message — a blank panel while someone is
	 * mid-keystroke in another tab reads as data loss.
	 */
	| { readonly type: 'parseError'; readonly message: string; readonly revision: number }
	/** Per-node validation state, mirrored from the diagnostics already computed. */
	| { readonly type: 'marks'; readonly marks: readonly NodeMark[] }
	/** Coarse (or CLI-streamed) execution state for one node. */
	| { readonly type: 'runProgress'; readonly nodeId: string; readonly state: RunNodeState };

export type WebviewToHost =
	/** Sent once the canvas has mounted and can receive state. */
	| { readonly type: 'ready' }
	| { readonly type: 'intent'; readonly intent: PipelineIntent }
	/** Something the canvas could not handle, for the output channel. */
	| { readonly type: 'log'; readonly level: 'info' | 'warn' | 'error'; readonly message: string };

/**
 * View state the webview persists through `getState`/`setState`.
 *
 * Kept deliberately small and entirely derivable-from-nothing: losing it costs
 * a scroll position, never an edit. That is what makes
 * `retainContextWhenHidden` unnecessary here.
 */
export interface CanvasViewState {
	readonly viewport?: { readonly x: number; readonly y: number; readonly zoom: number };
	readonly selectedNodeId?: string;
	readonly paletteOpen?: boolean;
}

export function isWebviewToHost(value: unknown): value is WebviewToHost {
	if (typeof value !== 'object' || value === null) {
		return false;
	}
	const type = (value as { type?: unknown }).type;
	return type === 'ready' || type === 'intent' || type === 'log';
}
