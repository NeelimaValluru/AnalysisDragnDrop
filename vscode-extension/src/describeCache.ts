/**
 * One `describe --json` run, shared by every canvas.
 *
 * `describe` is a pure query — it reads no file and depends only on the
 * installed package — so spawning an interpreter for it on every webview load
 * would put ~35ms and a process onto opening a tab, repeatedly, for an answer
 * that did not change. It is cached for the session.
 *
 * Invalidation is deliberately narrow, because the only things that can change
 * the answer are the interpreter and the package behind it:
 *
 * - the `analysisGui.pythonPath` setting changes, or the Python extension's
 *   selected environment changes — a different interpreter is a different
 *   registry;
 * - `analysisGui.refreshNodeKinds` is invoked, which is the escape hatch for
 *   `pip install -e .` of a package that grew a node kind while VS Code stayed
 *   open. Editing a `.pipeline` file cannot change the answer, so nothing about
 *   a document invalidates this.
 *
 * A failed run is not cached: the usual cause is an interpreter that cannot
 * import `analysis_gui` yet, and that is exactly the case where the next
 * attempt should try again rather than serve a remembered failure.
 */

import * as vscode from 'vscode';
import type { JsonValue } from './pipeline/document';
import { CONFIG_SECTION, PYTHON_EXTENSION_ID, PythonBridgeError } from './pythonBridge';
import type { PythonBridge } from './pythonBridge';

/**
 * The raw `describe` envelope, forwarded to the webview and parsed there.
 *
 * Kept unstructured on purpose: the cache's job is to avoid a second process,
 * not to understand the payload. `nodeKinds.parseDescribeEnvelope` is the one
 * place that decides what the envelope means.
 */
export type DescribePayload = JsonValue;

export class DescribeCache implements vscode.Disposable {
	private cached: DescribePayload | undefined;
	private inFlight: Promise<DescribePayload | undefined> | undefined;
	private readonly disposables: vscode.Disposable[] = [];

	constructor(
		private readonly bridge: PythonBridge,
		private readonly log: vscode.LogOutputChannel,
	) {
		this.disposables.push(
			vscode.workspace.onDidChangeConfiguration((event) => {
				if (event.affectsConfiguration(`${CONFIG_SECTION}.pythonPath`)) {
					this.invalidate('the interpreter setting changed');
				}
			}),
		);

		// The Python extension has no public "the environment changed" event on
		// its exported API surface, so watch for the extension becoming active
		// as the cheap approximation and rely on the explicit refresh command
		// for the rest. Getting this wrong costs a stale palette, not an edit.
		const python = vscode.extensions.getExtension(PYTHON_EXTENSION_ID);
		if (python && !python.isActive) {
			void python.activate().then(
				() => {
					this.invalidate('the Python extension activated');
				},
				() => {
					/* Nothing to invalidate if it never started. */
				},
			);
		}
	}

	/**
	 * The registry envelope, running `describe` at most once concurrently.
	 * `undefined` when the CLI could not be reached or did not return JSON.
	 */
	public async get(resource?: vscode.Uri): Promise<DescribePayload | undefined> {
		if (this.cached !== undefined) {
			return this.cached;
		}
		this.inFlight ??= this.load(resource);
		try {
			return await this.inFlight;
		} finally {
			this.inFlight = undefined;
		}
	}

	public invalidate(reason: string): void {
		if (this.cached !== undefined) {
			this.log.info(`Discarding the cached node-kind registry: ${reason}.`);
		}
		this.cached = undefined;
	}

	public dispose(): void {
		for (const disposable of this.disposables) {
			disposable.dispose();
		}
	}

	private async load(resource?: vscode.Uri): Promise<DescribePayload | undefined> {
		try {
			const run = await this.bridge.describe(resource);
			if (!run.ok) {
				this.log.warn('Could not read the node-kind registry; the palette will be empty.');
				void this.bridge.reportFailure(run);
				return undefined;
			}

			const payload = this.bridge.parseJson(run);
			if (payload === undefined) {
				return undefined;
			}

			// It came from `JSON.parse`, so it is JSON by construction; the
			// bridge just types it loosely because it does not read it.
			this.cached = payload as JsonValue;
			return this.cached;
		} catch (error) {
			this.log.warn(`Could not run describe: ${error instanceof Error ? error.message : String(error)}`);
			if (error instanceof PythonBridgeError) {
				void this.bridge.reportStartFailure(error);
			}
			return undefined;
		}
	}
}
