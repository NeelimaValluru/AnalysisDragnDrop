/**
 * Writing a pipeline's generated Python to a file the user picks.
 *
 * The CLI's `-o` does the writing rather than the extension: it is the same
 * code path the command line uses, so an export from the editor and an export
 * from a terminal cannot drift, and the JSON receipt reports back what landed.
 */

import * as vscode from 'vscode';
import { basename, stem } from './pipelineSource';
import type { PipelineSnapshots } from './pipelineSource';
import { describeFailure } from './pythonBridge';
import type { CliEnvelope, PythonBridge } from './pythonBridge';

interface CodegenReceipt extends CliEnvelope {
	readonly output_path?: string;
	readonly line_count?: number;
}

export async function exportPipelineToPython(
	pipeline: vscode.Uri,
	bridge: PythonBridge,
	snapshots: PipelineSnapshots,
	log: vscode.LogOutputChannel,
): Promise<void> {
	// Same directory, same name, `.py` instead of `.pipeline`. The dialog is
	// also where overwrite confirmation happens — VS Code prompts natively, so
	// reaching the CLI at all means the user already agreed to replace the file.
	const defaultUri = vscode.Uri.joinPath(pipeline, '..', `${stem(pipeline)}.py`);

	const target = await vscode.window.showSaveDialog({
		defaultUri,
		title: 'Export Pipeline to Python',
		saveLabel: 'Export',
		filters: { Python: ['py'] },
	});
	if (!target) {
		return;
	}

	const sourcePath = await snapshots.pathFor(pipeline);
	const run = await vscode.window.withProgress(
		{ location: vscode.ProgressLocation.Window, title: `Exporting ${basename(pipeline)}…` },
		(_progress, token) => bridge.codegen(pipeline, { outFile: target, sourcePath, token }),
	);

	if (!run.ok) {
		log.error(`Export of ${pipeline.fsPath} failed: ${describeFailure(run)}`);
		await bridge.reportFailure(run);
		return;
	}

	const receipt = bridge.parseJson<CodegenReceipt>(run);
	const written = receipt?.output_path ?? target.fsPath;
	const lines = receipt?.line_count;
	log.info(`Exported ${pipeline.fsPath} to ${written}.`);

	const choice = await vscode.window.showInformationMessage(
		`Exported ${basename(pipeline)} to ${basename(target)}${
			lines === undefined ? '' : ` (${lines} ${lines === 1 ? 'line' : 'lines'})`
		}.`,
		'Open File',
	);

	if (choice === 'Open File') {
		const document = await vscode.workspace.openTextDocument(vscode.Uri.file(written));
		await vscode.window.showTextDocument(document, { preview: false });
	}
}
