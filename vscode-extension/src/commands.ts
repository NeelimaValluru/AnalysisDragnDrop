/**
 * Command registrations.
 *
 * Validate, preview, export, run, discover, similar and the desktop launcher
 * all go through the Python bridge. Interpreter failures share
 * `PythonBridge.reportFailure`. `revealNodeSource` still waits on a node-to-
 * source mapping from the canvas.
 */

import * as path from 'node:path';
import * as vscode from 'vscode';
import type { CodePreviewProvider } from './codePreview';
import type { DescribeCache } from './describeCache';
import type { PipelineDiagnostics } from './diagnostics';
import { exportPipelineToPython } from './exportPython';
import { PIPELINE_EDITOR_VIEW_TYPE } from './pipelineEditor';
import type { PipelineEditorProvider } from './pipelineEditor';
import { PIPELINE_EXTENSION, basename, isPipelineDocument, isPipelineUri } from './pipelineSource';
import type { PipelineSnapshots } from './pipelineSource';
import type { CliEnvelope, PythonBridge } from './pythonBridge';
import { PythonBridgeError } from './pythonBridge';
import { attachRunProgress, topoNodeIds } from './runProgress';
import { newPipelineFromTemplate } from './templates';
import { summarize } from './validation';
import type { JsonObject } from './pipeline/document';
import { nodeToJson, parsePipelineText } from './pipeline/document';
import { nodeFromKind, parseNodeKind, uuid4 } from './pipeline/nodeKinds';

type CommandHandler = (...args: unknown[]) => void | Promise<void>;

let runChannel: vscode.OutputChannel | undefined;

export interface CommandDependencies {
	readonly bridge: PythonBridge;
	readonly snapshots: PipelineSnapshots;
	readonly preview: CodePreviewProvider;
	readonly diagnostics: PipelineDiagnostics;
	readonly describeCache: DescribeCache;
	readonly editor: PipelineEditorProvider;
	readonly log: vscode.LogOutputChannel;
}

export function registerCommands(context: vscode.ExtensionContext, deps: CommandDependencies): void {
	const { bridge, snapshots, preview, diagnostics, describeCache, editor, log } = deps;

	const register = (command: string, handler: CommandHandler): void => {
		context.subscriptions.push(
			vscode.commands.registerCommand(command, async (...args: unknown[]) => {
		try {
			await handler(...args);
		} catch (error) {
			await showCommandError(command, error, log, bridge);
		}
			}),
		);
	};

	register('analysisGui.validate', async (target) => {
		const pipeline = await resolvePipelineUri(target);
		if (!pipeline) {
			return;
		}

		// Opening without showing gets a TextDocument for a file invoked from
		// the explorer, so ranges and diagnostics work the same either way.
		const document = await vscode.workspace.openTextDocument(pipeline);

		const result = await vscode.window.withProgress(
			{ location: vscode.ProgressLocation.Window, title: 'Validating pipeline…' },
			() => diagnostics.validateNow(document),
		);

		if (!result) {
			return;
		}

		const outcome = result.outcome;

		if (outcome.kind === 'unreadable') {
			if (result.run) {
				await bridge.reportFailure(result.run);
			} else {
				void vscode.window.showErrorMessage(`${basename(pipeline)}: ${outcome.detail}`);
			}
			return;
		}

		if (outcome.kind === 'cliError') {
			void vscode.window.showErrorMessage(`${basename(pipeline)}: ${outcome.message}`);
			return;
		}
		if (outcome.kind !== 'report') {
			return;
		}

		if (outcome.report.valid && !outcome.report.findings.length) {
			void vscode.window.showInformationMessage(`${basename(pipeline)} is valid — no problems found.`);
			return;
		}

		const choice = await vscode.window.showWarningMessage(
			`${basename(pipeline)}: ${summarize(outcome.report)}.`,
			'Show Problems',
		);
		if (choice === 'Show Problems') {
			await vscode.commands.executeCommand('workbench.actions.view.problems');
		}
	});

	register('analysisGui.previewCode', async (target) => {
		const pipeline = await resolvePipelineUri(target);
		if (pipeline) {
			await preview.show(pipeline);
		}
	});

	register('analysisGui.exportPython', async (target) => {
		const pipeline = await resolvePipelineUri(target);
		if (pipeline) {
			await exportPipelineToPython(pipeline, bridge, snapshots, log);
		}
	});

	register('analysisGui.runPipeline', async (target) => {
		const pipeline = await resolvePipelineUri(target);
		if (pipeline) {
			await runPipelineFromEditor(pipeline, bridge, snapshots, editor, context, log);
		}
	});

	register('analysisGui.launchGui', async (target) => {
		// Useful without a file (empty builder) and with one (explorer /
		// editor). The desktop entry point takes an optional positional path.
		const pipeline = await resolvePipelineUri(target, { required: false });
		await bridge.launchDesktopApp(pipeline);
	});

	register('analysisGui.openCanvas', async (target) => {
		const pipeline = await resolvePipelineUri(target);
		if (pipeline) {
			// The canvas is the default editor for `.pipeline` files. This
			// command is the way back in from the text editor (or from the
			// explorer) without hunting through "Reopen Editor With…".
			await vscode.commands.executeCommand('vscode.openWith', pipeline, PIPELINE_EDITOR_VIEW_TYPE);
		}
	});

	register('analysisGui.refreshNodeKinds', () => {
		// For `pip install -e .` of a package that grew a node kind while VS
		// Code stayed open. Nothing else can change the answer, which is why
		// the cache does not otherwise expire.
		describeCache.invalidate('the refresh command was invoked');
		editor.refreshRegistry();
		void vscode.window.showInformationMessage('Reloaded the Analysis GUI node kinds.');
	});

	register('analysisGui.revealNodeSource', (target) => {
		// The webview will call this with a node id once the canvas exists.
		const nodeId = readNodeId(target);
		void vscode.window.showInformationMessage(
			nodeId
				? `Revealing the source of node "${nodeId}" is not implemented yet.`
				: 'Reveal Node Source needs a node selected on the pipeline canvas, which does not exist yet.',
		);
	});

	register('analysisGui.discoverLibraryNodes', async () => {
		await discoverLibraryNodes(bridge, editor, log);
	});

	register('analysisGui.findSimilarCode', async () => {
		await findSimilarCode(bridge, editor, log);
	});

	register('analysisGui.newFromTemplate', async () => {
		await newPipelineFromTemplate(context.extensionUri);
	});
}

interface LibraryHit {
	readonly kind?: unknown;
	readonly chunk_kind?: unknown;
	readonly palette_label?: unknown;
	readonly label?: unknown;
	readonly description?: unknown;
	readonly qualified_name?: unknown;
	readonly score?: unknown;
	readonly source_path?: unknown;
	readonly tags?: unknown;
	readonly span?: unknown;
	readonly preview?: unknown;
}

async function discoverLibraryNodes(
	bridge: PythonBridge,
	editor: PipelineEditorProvider,
	log: vscode.LogOutputChannel,
): Promise<void> {
	const run = await vscode.window.withProgress(
		{ location: vscode.ProgressLocation.Window, title: 'Discovering library nodes…' },
		() => bridge.discover(),
	);
	if (!run.ok) {
		await bridge.reportFailure(run);
		return;
	}

	const payload = bridge.parseJson(run);
	const kinds = asObjectArray(payload?.['kinds']);
	if (kinds.length === 0) {
		void vscode.window.showInformationMessage('No analysis-step chunks found in the configured library roots.');
		return;
	}

	const picked = await vscode.window.showQuickPick(kindQuickPickItems(kinds), {
		title: 'Discovered library nodes',
		placeHolder: `Indexed ${kinds.length} chunks — type to filter`,
		matchOnDescription: true,
		matchOnDetail: true,
	});
	if (!picked) {
		return;
	}

	await addDiscoveredKindToPipeline(picked.spec, editor, log);
}

async function findSimilarCode(
	bridge: PythonBridge,
	editor: PipelineEditorProvider,
	log: vscode.LogOutputChannel,
): Promise<void> {
	const query = await similarQueryFromEditor();
	if (query === undefined) {
		return;
	}
	if (!query.trim()) {
		void vscode.window.showWarningMessage('Enter a snippet or description to search for similar code.');
		return;
	}

	const run = await vscode.window.withProgress(
		{ location: vscode.ProgressLocation.Window, title: 'Finding similar library code…' },
		() => bridge.similar(query.trim()),
	);
	if (!run.ok) {
		await bridge.reportFailure(run);
		return;
	}

	const payload = bridge.parseJson(run);
	const hits = asObjectArray(payload?.['hits']);
	if (hits.length === 0) {
		void vscode.window.showInformationMessage(`No similar code for “${query.trim()}”.`);
		return;
	}

	const picked = await vscode.window.showQuickPick(kindQuickPickItems(hits, { includeScore: true }), {
		title: `Code like “${query.trim()}”`,
		placeHolder: 'Select a match to add as a custom_code node',
		matchOnDescription: true,
		matchOnDetail: true,
	});
	if (!picked) {
		return;
	}

	await addDiscoveredKindToPipeline(picked.spec, editor, log);
}

async function similarQueryFromEditor(): Promise<string | undefined> {
	const active = vscode.window.activeTextEditor;
	const selected = active && !active.selection.isEmpty ? active.document.getText(active.selection) : '';
	if (selected.trim()) {
		return selected.length > 500 ? selected.slice(0, 500) : selected;
	}
	return vscode.window.showInputBox({
		title: 'Find similar code',
		prompt: 'Describe the analysis step, or paste a snippet',
		placeHolder: 'bandpass eeg filter',
	});
}

function kindQuickPickItems(
	kinds: readonly JsonObject[],
	options: { includeScore?: boolean } = {},
): Array<vscode.QuickPickItem & { spec: JsonObject }> {
	return kinds.map((spec) => {
		const hit = spec as LibraryHit;
		const chunkKind = asString(hit.chunk_kind);
		const span = spanLabel(hit.span);
		const label = asString(hit.palette_label) ?? asString(hit.label) ?? asString(hit.kind) ?? 'chunk';
		const description = [chunkKind, span, asString(hit.kind) ?? asString(hit.qualified_name)]
			.filter(Boolean)
			.join(' · ');
		const tags = Array.isArray(hit.tags) ? hit.tags.filter((tag): tag is string => typeof tag === 'string') : [];
		const score =
			options.includeScore && typeof hit.score === 'number' ? `score ${hit.score.toFixed(3)}` : undefined;
		const preview = asString(hit.preview);
		const previewLine = preview ? preview.split('\n')[0] : undefined;
		const detail = [score, asString(hit.description) ?? previewLine, tags.join(', ')].filter(Boolean).join(' · ');
		return { label, description, detail, spec };
	});
}

async function addDiscoveredKindToPipeline(
	raw: JsonObject,
	editor: PipelineEditorProvider,
	log: vscode.LogOutputChannel,
): Promise<void> {
	const pipeline = await resolvePipelineUri(undefined, { required: false });
	const kindId = asString(raw['kind']) ?? '';
	if (!pipeline) {
		await vscode.env.clipboard.writeText(kindId);
		void vscode.window.showInformationMessage(
			kindId
				? `Copied ${kindId}. Open a .pipeline file to add it as a node.`
				: 'Open a .pipeline file to add a discovered node.',
		);
		return;
	}

	const document = await vscode.workspace.openTextDocument(pipeline);
	const parsed = parseNodeKind(raw);
	const nodeId = uuid4();
	const node = parsed
		? nodeFromKind(parsed, nodeId, [160, 80])
		: fallbackCustomCodeNode(raw, nodeId);

	try {
		await editor.applyIntent(document, { kind: 'addNode', nodeId, node: nodeToJson(node) });
		await vscode.commands.executeCommand('vscode.openWith', pipeline, PIPELINE_EDITOR_VIEW_TYPE);
		void vscode.window.showInformationMessage(`Added ${node.label} (${kindId || 'custom_code'}).`);
	} catch (error) {
		log.warn(`Could not add discovered node: ${error instanceof Error ? error.message : String(error)}`);
		await vscode.env.clipboard.writeText(kindId);
		void vscode.window.showInformationMessage(`Copied ${kindId}. Could not add it to the canvas.`);
	}
}

function fallbackCustomCodeNode(raw: JsonObject, id: string) {
	const functionName = asString(raw['label']) ?? 'process';
	return nodeFromKind(
		{
			kind: asString(raw['kind']) ?? 'custom_code',
			palette_label: functionName,
			in_palette: false,
			node_type: 'custom_code',
			label: functionName,
			description: asString(raw['description']) ?? '',
			metadata: {},
			parameters: [
				{
					name: 'function_name',
					param_type: 'string',
					default_value: functionName,
					description: 'Name of the function to call',
					options: [],
				},
			],
			inputs: [],
			outputs: [],
		},
		id,
		[160, 80],
	);
}

function asObjectArray(value: unknown): JsonObject[] {
	if (!Array.isArray(value)) {
		return [];
	}
	return value.filter((item): item is JsonObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
}

function asString(value: unknown): string | undefined {
	return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function spanLabel(value: unknown): string | undefined {
	if (!value || typeof value !== 'object' || Array.isArray(value)) {
		return undefined;
	}
	const span = value as { start?: unknown; end?: unknown };
	if (typeof span.start !== 'number' || typeof span.end !== 'number') {
		return undefined;
	}
	return `${span.start}-${span.end}`;
}

interface RunReceipt extends CliEnvelope {
	readonly saved_figures?: unknown;
	readonly exit_code?: unknown;
}

async function runPipelineFromEditor(
	pipeline: vscode.Uri,
	bridge: PythonBridge,
	snapshots: PipelineSnapshots,
	editor: PipelineEditorProvider,
	context: vscode.ExtensionContext,
	log: vscode.LogOutputChannel,
): Promise<void> {
	const channel = runOutputChannel(context);
	channel.clear();
	channel.appendLine(`Running ${basename(pipeline)}…`);
	channel.show(true);

	const sourcePath = await snapshots.pathFor(pipeline);
	const cwd = executionCwd(pipeline);

	let nodeIds: string[] = [];
	try {
		const document = await vscode.workspace.openTextDocument(pipeline);
		nodeIds = topoNodeIds(parsePipelineText(document.getText()));
	} catch (error) {
		log.debug(
			`No synthetic run progress for ${pipeline.fsPath}: ${error instanceof Error ? error.message : String(error)}`,
		);
	}

	const progress = attachRunProgress(nodeIds, (event) => {
		editor.postToUri(pipeline, { type: 'runProgress', nodeId: event.nodeId, state: event.state });
	});

	let run;
	try {
		run = await vscode.window.withProgress(
			{ location: vscode.ProgressLocation.Window, title: `Running ${basename(pipeline)}…` },
			(_progress, token) =>
				bridge.runPipeline(pipeline, {
					sourcePath,
					cwd,
					token,
					onStdout: (chunk) => {
						progress.pushChunk(chunk);
					},
					onStderr: (chunk) => {
						progress.pushChunk(chunk);
						channel.append(chunk);
					},
				}),
		);
		progress.finish(run.ok);
	} catch (error) {
		progress.finish(false);
		throw error;
	} finally {
		progress.dispose();
	}

	const receipt = bridge.parseJson<RunReceipt>(run);
	if (receipt) {
		channel.appendLine('');
		channel.appendLine(JSON.stringify(receipt, null, 2));
	} else if (run.stdout.trim()) {
		channel.appendLine('');
		channel.append(run.stdout);
	}

	if (!run.ok) {
		log.error(`Run of ${pipeline.fsPath} failed: ${run.stderr.trim() || run.stdout.trim()}`);
		await bridge.reportFailure(run);
		return;
	}

	const figures = savedFigurePaths(receipt);
	const summary = figures.length
		? `${basename(pipeline)} finished. Saved ${figures.length} figure${figures.length === 1 ? '' : 's'}.`
		: `${basename(pipeline)} finished successfully.`;

	const actions = figures.length ? (['Open Figures', 'Show Output'] as const) : (['Show Output'] as const);
	const choice = await vscode.window.showInformationMessage(summary, ...actions);

	if (choice === 'Open Figures') {
		for (const figure of figures) {
			await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(figure));
		}
	} else if (choice === 'Show Output') {
		channel.show(true);
	}
}

function runOutputChannel(context: vscode.ExtensionContext): vscode.OutputChannel {
	if (!runChannel) {
		runChannel = vscode.window.createOutputChannel('Analysis GUI: Run');
		context.subscriptions.push(runChannel);
	}
	return runChannel;
}

function executionCwd(pipeline: vscode.Uri): string | undefined {
	if (pipeline.scheme === 'file') {
		return path.dirname(pipeline.fsPath);
	}
	return vscode.workspace.getWorkspaceFolder(pipeline)?.uri.fsPath ?? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function savedFigurePaths(receipt: RunReceipt | undefined): string[] {
	const raw = receipt?.saved_figures;
	if (!Array.isArray(raw)) {
		return [];
	}
	return raw.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

/**
 * Figures out which pipeline a command should act on: an explicit URI from a
 * menu, otherwise the active editor (text or custom canvas).
 */
async function resolvePipelineUri(
	target: unknown,
	options: { required?: boolean } = {},
): Promise<vscode.Uri | undefined> {
	if (target instanceof vscode.Uri) {
		return target;
	}

	const document = vscode.window.activeTextEditor?.document;
	if (document && isPipelineDocument(document)) {
		return document.uri;
	}

	const tabUri = uriFromActiveTab();
	if (tabUri && isPipelineUri(tabUri)) {
		return tabUri;
	}

	if (options.required !== false) {
		await vscode.window.showWarningMessage(`Open a ${PIPELINE_EXTENSION} file first.`);
	}

	return undefined;
}

function uriFromActiveTab(): vscode.Uri | undefined {
	const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
	if (input instanceof vscode.TabInputText || input instanceof vscode.TabInputCustom) {
		return input.uri;
	}
	return undefined;
}

function readNodeId(target: unknown): string | undefined {
	if (typeof target === 'string') {
		return target;
	}
	if (target && typeof target === 'object' && 'nodeId' in target) {
		const { nodeId } = target as { nodeId?: unknown };
		return typeof nodeId === 'string' ? nodeId : undefined;
	}
	return undefined;
}

async function showCommandError(
	command: string,
	error: unknown,
	log: vscode.LogOutputChannel,
	bridge: PythonBridge,
): Promise<void> {
	if (error instanceof PythonBridgeError) {
		log.error(`${command}: ${error.message}${error.detail ? ` ${error.detail}` : ''}`);
		await bridge.reportStartFailure(error);
		return;
	}

	const message = error instanceof Error ? error.message : String(error);
	log.error(`${command} failed: ${message}`);
	const choice = await vscode.window.showErrorMessage(`${command} failed: ${message}`, 'Show Log');
	if (choice === 'Show Log') {
		log.show(true);
	}
}
