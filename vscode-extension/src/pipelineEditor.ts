/**
 * The pipeline canvas, as a `CustomTextEditorProvider`.
 *
 * ## Why `CustomTextEditorProvider` and not `CustomEditorProvider`
 *
 * A `.pipeline` file is text. Choosing the text-backed provider means VS Code's
 * `TextDocument` stays the single source of truth for its contents, and dirty
 * state, undo/redo, hot exit, revert, Git decorations and the diff editor all
 * keep working because they are the same machinery they always were. The
 * general `CustomEditorProvider` would hand back an opaque model and a backup
 * API, and every one of those would have to be re-implemented — for a format
 * that is JSON on disk and readable by hand. There is nothing to gain and a
 * long list of things to get wrong.
 *
 * ## Where the VS Code boundary sits
 *
 * Everything in this file. The webview bundle's `index.tsx` is the only other
 * VS Code-aware module, and it is an adapter over a plain React application
 * that knows nothing about editors. Between the two runs the intent protocol in
 * `pipeline/protocol.ts`, which is plain JSON both ways.
 *
 * ## Document synchronisation
 *
 * Webview to extension: semantic intents only — `moveNode`, `addNode`,
 * `deleteNode`, `addEdge`, `deleteEdge`, `setParam`. Each becomes one
 * `WorkspaceEdit`, so undo undoes the gesture rather than the file.
 *
 * Extension to webview: `onDidChangeTextDocument` for the bound document,
 * parsed and posted. Guarded against echo — see {@link CanvasSession.expect} —
 * because bouncing an edit the canvas just made would reset node positions
 * under the pointer and refresh the graph mid-drag.
 */

import * as vscode from 'vscode';
import type { DescribeCache } from './describeCache';
import type { PipelineDiagnostics } from './diagnostics';
import type { JsonValue } from './pipeline/document';
import { parsePipelineText, serializePipelineDocument } from './pipeline/document';
import { applyIntent, describeIntent } from './pipeline/intents';
import type { PipelineIntent } from './pipeline/intents';
import type { NodeSeverity } from './pipeline/graphModel';
import { isWebviewToHost } from './pipeline/protocol';
import type { HostToWebview, NodeMark } from './pipeline/protocol';
import { minimalTextEdit } from './pipeline/textEdit';
import { basename } from './pipelineSource';
import { isErrorSeverity } from './validation';
import type { Finding } from './validation';

export const PIPELINE_EDITOR_VIEW_TYPE = 'analysisGui.pipelineEditor';

export interface PipelineEditorDependencies {
	readonly describeCache: DescribeCache;
	readonly diagnostics: PipelineDiagnostics;
	readonly log: vscode.LogOutputChannel;
}

export class PipelineEditorProvider implements vscode.CustomTextEditorProvider {
	private readonly sessions = new Set<CanvasSession>();
	/**
	 * One in-flight edit per document. Two intents arriving in the same tick
	 * would otherwise both read the pre-edit text and the second would undo the
	 * first, so they are serialised per document rather than per session.
	 */
	private readonly editQueues = new Map<string, Promise<void>>();

	constructor(
		private readonly context: vscode.ExtensionContext,
		private readonly deps: PipelineEditorDependencies,
	) {}

	public register(): vscode.Disposable {
		return vscode.Disposable.from(
			vscode.window.registerCustomEditorProvider(PIPELINE_EDITOR_VIEW_TYPE, this, {
				// Not retained: the only thing worth keeping across a hidden tab
				// is the viewport and the selection, and the webview persists
				// both through `getState`/`setState`. Retaining context would
				// hold a live DOM and React tree per hidden canvas to save
				// re-reading a document VS Code already has in memory.
				webviewOptions: { retainContextWhenHidden: false },
				supportsMultipleEditorsPerDocument: true,
			}),
			vscode.workspace.onDidChangeTextDocument((event) => {
				this.onDocumentChanged(event.document);
			}),
			this.deps.diagnostics.onDidChangeFindings(({ uri, findings }) => {
				for (const session of this.sessions) {
					if (session.documentUri.toString() === uri.toString()) {
						session.post(marksMessage(findings));
					}
				}
			}),
		);
	}

	/** Re-sends the node-kind registry to every open canvas. */
	public refreshRegistry(): void {
		for (const session of this.sessions) {
			void this.postRegistry(session);
		}
	}

	public resolveCustomTextEditor(
		document: vscode.TextDocument,
		panel: vscode.WebviewPanel,
		token: vscode.CancellationToken,
	): void {
		const session = new CanvasSession(document.uri, panel);
		this.sessions.add(session);

		panel.webview.options = {
			enableScripts: true,
			// Nothing outside the built bundle is reachable, which is the other
			// half of the CSP: `default-src 'none'` says what may be loaded and
			// this says from where.
			localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'dist')],
		};
		panel.webview.html = this.html(panel.webview);

		panel.webview.onDidReceiveMessage((raw: unknown) => {
			if (!isWebviewToHost(raw)) {
				this.deps.log.warn(`Ignoring an unrecognised message from the pipeline canvas: ${JSON.stringify(raw)}`);
				return;
			}

			switch (raw.type) {
				case 'ready':
					void this.postInitialState(session, document);
					break;
				case 'intent':
					void this.enqueue(document, raw.intent);
					break;
				case 'log':
					this.deps.log[raw.level](`[canvas ${basename(document.uri)}] ${raw.message}`);
					break;
			}
		});

		panel.onDidDispose(() => {
			this.sessions.delete(session);
		});

		token.onCancellationRequested(() => {
			this.sessions.delete(session);
		});
	}

	private async postInitialState(session: CanvasSession, document: vscode.TextDocument): Promise<void> {
		// Order matters only in that the registry should be there before the
		// first render of the graph; the canvas copes either way, but a palette
		// that pops in a beat later looks broken.
		await this.postRegistry(session);
		this.postDocument(session, document);
		session.post(marksMessage(this.deps.diagnostics.findingsFor(document.uri)));
	}

	private async postRegistry(session: CanvasSession): Promise<void> {
		const payload = await this.deps.describeCache.get(session.documentUri);
		if (payload !== undefined) {
			session.post({ type: 'registry', payload });
		}
	}

	private postDocument(session: CanvasSession, document: vscode.TextDocument): void {
		const text = document.getText();
		try {
			session.post({ type: 'document', payload: JSON.parse(text) as JsonValue, revision: document.version });
		} catch (error) {
			// Someone is mid-keystroke in a text editor on the same file. The
			// canvas keeps its last good graph and says why it is frozen.
			session.post({
				type: 'parseError',
				message: error instanceof Error ? error.message : String(error),
				revision: document.version,
			});
		}
	}

	private onDocumentChanged(document: vscode.TextDocument): void {
		for (const session of this.sessions) {
			if (session.documentUri.toString() !== document.uri.toString()) {
				continue;
			}
			if (session.consumeEcho(document.getText())) {
				// This session caused this change. Sending it back would reset
				// React Flow's node positions from the document while the
				// pointer is still moving them.
				continue;
			}
			this.postDocument(session, document);
		}
	}

	private enqueue(document: vscode.TextDocument, intent: PipelineIntent): Promise<void> {
		const key = document.uri.toString();
		const previous = this.editQueues.get(key) ?? Promise.resolve();
		const next = previous.then(() => this.applyIntentToDocument(document, intent));
		this.editQueues.set(
			key,
			next.finally(() => {
				if (this.editQueues.get(key) === next) {
					this.editQueues.delete(key);
				}
			}),
		);
		return next;
	}

	/** Apply a canvas intent from the host (commands, not just the webview). */
	public applyIntent(document: vscode.TextDocument, intent: PipelineIntent): Promise<void> {
		return this.enqueue(document, intent);
	}

	/** Posts a host message to every open canvas on this document. */
	public postToUri(uri: vscode.Uri, message: HostToWebview): void {
		for (const session of this.sessions) {
			if (session.documentUri.toString() === uri.toString()) {
				session.post(message);
			}
		}
	}

	/**
	 * Replays one intent against the document and writes the result as a single
	 * `WorkspaceEdit`.
	 *
	 * The document is re-read here rather than trusted from the webview, so an
	 * edit made in a text editor between the gesture and this call is not lost.
	 */
	private async applyIntentToDocument(document: vscode.TextDocument, intent: PipelineIntent): Promise<void> {
		if (document.isClosed) {
			return;
		}

		const text = document.getText();
		let current;
		try {
			current = parsePipelineText(text);
		} catch (error) {
			this.deps.log.warn(
				`Dropped "${describeIntent(intent)}": ${basename(document.uri)} is not parseable right now ` +
					`(${error instanceof Error ? error.message : String(error)}).`,
			);
			return;
		}

		const next = applyIntent(current, intent);
		if (!next) {
			return;
		}

		const serialized = serializePipelineDocument(next, { trailingNewline: text.endsWith('\n') });
		const splice = minimalTextEdit(text, serialized);
		if (!splice) {
			return;
		}

		const edit = new vscode.WorkspaceEdit();
		edit.replace(
			document.uri,
			new vscode.Range(document.positionAt(splice.start), document.positionAt(splice.end)),
			splice.text,
			{ label: describeIntent(intent), needsConfirmation: false },
		);

		// Registered *before* the edit lands: `onDidChangeTextDocument` can fire
		// synchronously inside `applyEdit`.
		for (const session of this.sessions) {
			if (session.documentUri.toString() === document.uri.toString()) {
				session.expect(serialized);
			}
		}

		const applied = await vscode.workspace.applyEdit(edit);
		if (!applied) {
			this.deps.log.warn(`"${describeIntent(intent)}" was rejected by the editor.`);
			for (const session of this.sessions) {
				session.forgetEcho();
			}
			// The canvas applied it optimistically, so put it back in step.
			this.onDocumentChanged(document);
		}
	}

	/**
	 * The webview host page.
	 *
	 * The CSP is the strict one:
	 *
	 *   default-src 'none'   nothing loads unless something below allows it
	 *   script-src  a nonce  no 'unsafe-eval', no 'unsafe-inline', no host
	 *                        source — only the one tag carrying this load's
	 *                        nonce runs, and the nonce is fresh per load
	 *   style-src   cspSource + 'unsafe-inline'
	 *   img-src     cspSource + data:
	 *   font-src    cspSource
	 *
	 * `'unsafe-inline'` on styles is not laziness and not avoidable: React Flow
	 * positions every node and edge with an inline `style` attribute, and CSP
	 * `style-src` governs style attributes as well as `<style>` elements. It is
	 * scoped to styles only — scripts get no such allowance — and no script can
	 * be injected through a style attribute.
	 *
	 * Everything is served from `dist/`; there is no CDN and no network origin
	 * in the policy at all.
	 */
	private html(webview: vscode.Webview): string {
		const nonce = createNonce();
		const script = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview.js'));
		const style = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview.css'));

		const csp = [
			`default-src 'none'`,
			`img-src ${webview.cspSource} data:`,
			`font-src ${webview.cspSource}`,
			`style-src ${webview.cspSource} 'unsafe-inline'`,
			`script-src 'nonce-${nonce}'`,
		].join('; ');

		return `<!DOCTYPE html>
<html lang="en">
	<head>
		<meta charset="UTF-8" />
		<meta http-equiv="Content-Security-Policy" content="${csp}" />
		<meta name="viewport" content="width=device-width, initial-scale=1.0" />
		<link href="${style.toString()}" rel="stylesheet" />
		<title>Analysis Pipeline</title>
	</head>
	<body>
		<div id="root"></div>
		<script type="module" nonce="${nonce}" src="${script.toString()}"></script>
	</body>
</html>`;
	}
}

/** One open canvas. Owns the echo guard, because it is per panel, not per file. */
class CanvasSession {
	/**
	 * The exact text this session's last edit produced.
	 *
	 * Comparing content rather than tracking `document.version` is deliberate:
	 * versions advance for reasons that have nothing to do with us (a save
	 * participant, a formatter) and a version-based guard would swallow a real
	 * external change. Content cannot be wrong about what it is.
	 */
	private echo: string | undefined;

	constructor(
		public readonly documentUri: vscode.Uri,
		private readonly panel: vscode.WebviewPanel,
	) {}

	public post(message: HostToWebview): void {
		void this.panel.webview.postMessage(message);
	}

	public expect(text: string): void {
		this.echo = text;
	}

	public forgetEcho(): void {
		this.echo = undefined;
	}

	/** True when `text` is this session's own edit coming back. Consumes the guard. */
	public consumeEcho(text: string): boolean {
		if (this.echo === undefined || this.echo !== text) {
			return false;
		}
		this.echo = undefined;
		return true;
	}
}

/**
 * Maps findings onto nodes.
 *
 * Only findings that name a node can mark one; `malformed_edges` and friends
 * belong to the file and stay in the Problems panel. An error outranks a
 * warning on the same node.
 */
function marksMessage(findings: readonly Finding[]): HostToWebview {
	const worst = new Map<string, NodeSeverity>();
	for (const finding of findings) {
		if (!finding.node_id) {
			continue;
		}
		const severity: NodeSeverity = isErrorSeverity(finding) ? 'error' : 'warning';
		if (severity === 'error' || !worst.has(finding.node_id)) {
			worst.set(finding.node_id, severity);
		}
	}

	const marks: NodeMark[] = [...worst].map(([nodeId, severity]) => ({ nodeId, severity }));
	return { type: 'marks', marks };
}

/**
 * A fresh nonce per webview load.
 *
 * `crypto.randomUUID` rather than `Math.random`: a predictable nonce is the
 * same as no nonce, and this is the only thing standing between the page and an
 * injected `<script>`.
 */
function createNonce(): string {
	return globalThis.crypto.randomUUID().replaceAll('-', '');
}
