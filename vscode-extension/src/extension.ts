import * as vscode from 'vscode';
import { CodePreviewProvider } from './codePreview';
import { registerCommands } from './commands';
import { DescribeCache } from './describeCache';
import { PipelineDiagnostics } from './diagnostics';
import { PipelineEditorProvider } from './pipelineEditor';
import { PipelineSnapshots } from './pipelineSource';
import { PythonBridge } from './pythonBridge';

export function activate(context: vscode.ExtensionContext): void {
	const log = vscode.window.createOutputChannel('Analysis GUI', { log: true });
	context.subscriptions.push(log);
	log.info('Analysis GUI extension activated.');

	const bridge = new PythonBridge(log);
	void bridge.noteWorkspaceVenvHint();
	const snapshots = new PipelineSnapshots(log);
	context.subscriptions.push(snapshots);

	const preview = new CodePreviewProvider(bridge, snapshots, log);
	preview.register(context);

	const diagnostics = new PipelineDiagnostics(bridge, snapshots, log);
	diagnostics.register(context);

	const describeCache = new DescribeCache(bridge, log);
	context.subscriptions.push(describeCache);

	// The DAG canvas. `contributes.customEditors` declares it with
	// `"priority": "default"`, so double-clicking a `.pipeline` file opens the
	// canvas. Raw JSON remains available via "Reopen Editor With…".
	const editor = new PipelineEditorProvider(context, { describeCache, diagnostics, log });
	context.subscriptions.push(editor.register());

	registerCommands(context, { bridge, snapshots, preview, diagnostics, describeCache, editor, log });
}

export function deactivate(): void {
	// Nothing to do: every disposable is owned by the extension context.
}
