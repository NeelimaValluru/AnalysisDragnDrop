/**
 * The VS Code side of the canvas — and the only file in the webview bundle that
 * knows VS Code exists.
 *
 * Everything under `app/` is a plain React application: a document and a
 * registry in, intents out. This module is the adapter that answers that
 * interface with `acquireVsCodeApi`. Serving the same canvas from a web page or
 * wrapping it as a Jupyter widget means writing a sibling of this file and
 * changing nothing under `app/`, which is the point of keeping the boundary
 * here rather than letting `postMessage` leak into the component tree.
 *
 * State restoration goes through `getState`/`setState` rather than
 * `retainContextWhenHidden`. Retaining context keeps a whole DOM and a React
 * tree alive for every hidden tab; the only thing worth preserving here is a
 * viewport and a selection, and both fit in `setState`. The document itself is
 * never persisted — the `TextDocument` is the single source of truth and is
 * re-sent on every load.
 */

import { StrictMode, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { Component } from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';
import './app/canvas.css';

import type { PipelineDocument } from '../pipeline/document';
import { parsePipelineDocument } from '../pipeline/document';
import type { NodeSeverity } from '../pipeline/graphModel';
import type { PipelineIntent } from '../pipeline/intents';
import { EMPTY_REGISTRY, parseDescribeEnvelope } from '../pipeline/nodeKinds';
import type { NodeKindRegistry } from '../pipeline/nodeKinds';
import type { CanvasViewState, HostToWebview, RunNodeState, WebviewToHost } from '../pipeline/protocol';
import { PipelineCanvas } from './app/PipelineCanvas';

interface VsCodeApi {
	postMessage(message: WebviewToHost): void;
	getState(): CanvasViewState | undefined;
	setState(state: CanvasViewState): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

// Exactly once per load: calling it twice throws, and VS Code gives no way to
// ask whether it has already been called.
const vscode = acquireVsCodeApi();

function VsCodeCanvasHost(): ReactElement {
	const [registry, setRegistry] = useState<NodeKindRegistry>(EMPTY_REGISTRY);
	const [document, setDocument] = useState<PipelineDocument | undefined>(undefined);
	const [parseError, setParseError] = useState<string | undefined>(undefined);
	const [marks, setMarks] = useState<ReadonlyMap<string, NodeSeverity>>(new Map());
	const [runStates, setRunStates] = useState<ReadonlyMap<string, RunNodeState>>(new Map());

	const initialViewState = useMemo(() => vscode.getState() ?? {}, []);

	useEffect(() => {
		const onMessage = (event: MessageEvent<HostToWebview>): void => {
			const message = event.data;
			switch (message.type) {
				case 'registry':
					setRegistry(parseDescribeEnvelope(message.payload) ?? EMPTY_REGISTRY);
					break;

				case 'document':
					try {
						setDocument(parsePipelineDocument(message.payload));
						setParseError(undefined);
					} catch (error) {
						// The host parsed it, so this is a shape the host
						// accepted and the canvas did not. Say so rather than
						// rendering an empty graph.
						setParseError(describeError(error));
					}
					break;

				case 'parseError':
					setParseError(message.message);
					break;

				case 'marks':
					setMarks(new Map(message.marks.map((mark) => [mark.nodeId, mark.severity])));
					break;

				case 'runProgress':
					setRunStates((previous) => {
						const next = new Map(previous);
						next.set(message.nodeId, message.state);
						return next;
					});
					break;
			}
		};

		window.addEventListener('message', onMessage);
		vscode.postMessage({ type: 'ready' });
		return () => {
			window.removeEventListener('message', onMessage);
		};
	}, []);

	const onIntent = useCallback((intent: PipelineIntent) => {
		vscode.postMessage({ type: 'intent', intent });
	}, []);

	const onViewStateChange = useCallback((state: CanvasViewState) => {
		vscode.setState(state);
	}, []);

	return (
		<PipelineCanvas
			document={document}
			parseError={parseError}
			registry={registry}
			marks={marks}
			runStates={runStates}
			initialViewState={initialViewState}
			onIntent={onIntent}
			onViewStateChange={onViewStateChange}
		/>
	);
}

/**
 * A blank webview is indistinguishable from a hung one, and a render failure in
 * a webview goes nowhere by default. This puts the message on screen and in the
 * extension's output channel.
 */
class FatalBoundary extends Component<{ children: ReactNode }, { message: string | undefined }> {
	public override state: { message: string | undefined } = { message: undefined };

	public static getDerivedStateFromError(error: unknown): { message: string } {
		return { message: describeError(error) };
	}

	public override componentDidCatch(error: unknown): void {
		vscode.postMessage({ type: 'log', level: 'error', message: `Canvas render failed: ${describeError(error)}` });
	}

	public override render(): ReactNode {
		if (this.state.message !== undefined) {
			return (
				<div className="fatal">
					<h1>The pipeline canvas could not render.</h1>
					<p>{this.state.message}</p>
					<p>Reopen this file with the built-in text editor to keep working; the file itself is untouched.</p>
				</div>
			);
		}
		return this.props.children;
	}
}

function describeError(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

const container = window.document.getElementById('root');
if (container) {
	createRoot(container).render(
		<StrictMode>
			<FatalBoundary>
				<VsCodeCanvasHost />
			</FatalBoundary>
		</StrictMode>,
	);
}
