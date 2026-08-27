/**
 * A read-only view of what `codegen` produces for a pipeline.
 *
 * This is a `TextDocumentContentProvider` on its own URI scheme rather than a
 * real file, which makes the preview read-only by construction: there is no
 * path on disk it could be mistaken for, and no way to save over one. Exporting
 * is a separate, explicit act — see `exportPython.ts`.
 *
 * Content is byte-identical to what an export writes, so the preview can be
 * copied or diffed without surprises. Several node kinds currently generate
 * pass-through no-ops; the preview shows that plainly, which is the point.
 */

import * as vscode from 'vscode';
import { basename, isPipelineDocument, stem } from './pipelineSource';
import type { PipelineSnapshots } from './pipelineSource';
import { CONFIG_SECTION, PythonBridgeError, describeFailure } from './pythonBridge';
import type { PythonBridge } from './pythonBridge';

export const CODEGEN_SCHEME = 'analysis-pipeline-codegen';

/** Matches the diagnostics debounce in spirit: one refresh per burst of saves. */
const REFRESH_DEBOUNCE_MS = 400;

const AUTO_REFRESH_SETTING = 'autoPreviewCode';

/** The preview URI for a pipeline. The source is carried in the query string. */
export function previewUriFor(pipeline: vscode.Uri): vscode.Uri {
	return vscode.Uri.from({
		scheme: CODEGEN_SCHEME,
		// The `.py` suffix is what gets the document Python highlighting, and
		// the tab label says where it came from.
		path: `/${stem(pipeline)}.generated.py`,
		query: pipeline.toString(),
	});
}

function sourceOf(preview: vscode.Uri): vscode.Uri | undefined {
	try {
		return preview.query ? vscode.Uri.parse(preview.query, true) : undefined;
	} catch {
		return undefined;
	}
}

export class CodePreviewProvider implements vscode.TextDocumentContentProvider, vscode.Disposable {
	private readonly changeEmitter = new vscode.EventEmitter<vscode.Uri>();
	public readonly onDidChange = this.changeEmitter.event;

	private readonly refreshTimers = new Map<string, NodeJS.Timeout>();
	private readonly pendingContent = new Map<string, string>();
	private readonly disposables: vscode.Disposable[] = [];

	constructor(
		private readonly bridge: PythonBridge,
		private readonly snapshots: PipelineSnapshots,
		private readonly log: vscode.LogOutputChannel,
	) {}

	public register(context: vscode.ExtensionContext): void {
		this.disposables.push(
			vscode.workspace.registerTextDocumentContentProvider(CODEGEN_SCHEME, this),
			vscode.workspace.onDidSaveTextDocument((document) => {
				if (isPipelineDocument(document)) {
					this.refreshOnSave(document.uri);
				}
			}),
		);
		context.subscriptions.push(this);
	}

	/** Opens (or re-renders) the preview for a pipeline, beside its source. */
	public async show(pipeline: vscode.Uri): Promise<void> {
		const uri = previewUriFor(pipeline);

		// Explicit invocation always regenerates, whatever autoPreviewCode says,
		// and reports interpreter/CLI failures the same way Validate and Run do.
		const generated = await this.generate(pipeline, { reportFailures: true });
		this.pendingContent.set(uri.toString(), generated);
		this.changeEmitter.fire(uri);

		const document = await vscode.workspace.openTextDocument(uri);
		if (document.languageId !== 'python') {
			await vscode.languages.setTextDocumentLanguage(document, 'python');
		}

		await vscode.window.showTextDocument(document, {
			viewColumn: vscode.ViewColumn.Beside,
			preserveFocus: true,
			preview: false,
		});
	}

	public async provideTextDocumentContent(
		uri: vscode.Uri,
		token: vscode.CancellationToken,
	): Promise<string> {
		const cached = this.pendingContent.get(uri.toString());
		if (cached !== undefined) {
			this.pendingContent.delete(uri.toString());
			return cached;
		}

		const pipeline = sourceOf(uri);
		if (!pipeline) {
			return comment(['No pipeline is associated with this preview.']);
		}

		return this.generate(pipeline, { reportFailures: false, token });
	}

	private async generate(
		pipeline: vscode.Uri,
		options: { reportFailures: boolean; token?: vscode.CancellationToken },
	): Promise<string> {
		try {
			const sourcePath = await this.snapshots.pathFor(pipeline);
			const run = await this.bridge.codegen(pipeline, { sourcePath, token: options.token });

			if (run.ok) {
				return run.stdout;
			}

			// A blank editor would look like "your pipeline generates nothing".
			this.log.warn(`Code preview failed for ${pipeline.fsPath}: ${describeFailure(run)}`);
			if (options.reportFailures) {
				await this.bridge.reportFailure(run);
			}
			return comment([
				`Code generation failed for ${basename(pipeline)}.`,
				'',
				...(run.stderr.trim() || describeFailure(run)).split(/\r?\n/),
			]);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			this.log.error(`Code preview could not run for ${pipeline.fsPath}: ${message}`);
			if (options.reportFailures) {
				if (error instanceof PythonBridgeError) {
					await this.bridge.reportStartFailure(error);
				} else {
					await this.bridge.offerInterpreterHelp(
						`Code generation could not be started for ${basename(pipeline)}: ${message}`,
					);
				}
			}
			return comment([`Code generation could not be started for ${basename(pipeline)}.`, '', message]);
		}
	}

	public dispose(): void {
		for (const timer of this.refreshTimers.values()) {
			clearTimeout(timer);
		}
		this.refreshTimers.clear();
		this.changeEmitter.dispose();
		for (const disposable of this.disposables) {
			disposable.dispose();
		}
	}

	/**
	 * Firing for a URI nobody has open is a no-op in VS Code, so there is no
	 * bookkeeping of which previews are visible.
	 */
	private refreshOnSave(pipeline: vscode.Uri): void {
		if (!this.autoRefreshEnabled(pipeline)) {
			return;
		}

		const key = pipeline.toString();
		const existing = this.refreshTimers.get(key);
		if (existing) {
			clearTimeout(existing);
		}

		this.refreshTimers.set(
			key,
			setTimeout(() => {
				this.refreshTimers.delete(key);
				this.changeEmitter.fire(previewUriFor(pipeline));
			}, REFRESH_DEBOUNCE_MS),
		);
	}

	private autoRefreshEnabled(resource: vscode.Uri): boolean {
		return vscode.workspace
			.getConfiguration(CONFIG_SECTION, resource)
			.get<boolean>(AUTO_REFRESH_SETTING, true);
	}
}

function comment(lines: readonly string[]): string {
	return `${lines.map((line) => (line ? `# ${line}` : '#')).join('\n')}\n`;
}
