/**
 * Copy a starter `.pipeline` from the repo's `templates/` folder into the
 * workspace. The Python package owns those files; this command only notices
 * them when they exist.
 */

import * as vscode from 'vscode';
import { PIPELINE_EDITOR_VIEW_TYPE } from './pipelineEditor';
import { PIPELINE_EXTENSION } from './pipelineSource';

interface TemplatePick extends vscode.QuickPickItem {
	readonly uri: vscode.Uri;
}

/** `.pipeline` files under `templates/` of a workspace folder or the repo root. */
export async function listPipelineTemplates(extensionUri: vscode.Uri): Promise<vscode.Uri[]> {
	const found = await vscode.workspace.findFiles('templates/*.pipeline', '**/node_modules/**', 50);
	if (found.length > 0) {
		return found.sort((a, b) => a.fsPath.localeCompare(b.fsPath));
	}

	const fallbackDir = vscode.Uri.joinPath(extensionUri, '..', 'templates');
	try {
		const entries = await vscode.workspace.fs.readDirectory(fallbackDir);
		return entries
			.filter(([name, type]) => type === vscode.FileType.File && name.endsWith(PIPELINE_EXTENSION))
			.map(([name]) => vscode.Uri.joinPath(fallbackDir, name))
			.sort((a, b) => a.fsPath.localeCompare(b.fsPath));
	} catch {
		return [];
	}
}

export async function newPipelineFromTemplate(extensionUri: vscode.Uri): Promise<void> {
	const templates = await listPipelineTemplates(extensionUri);
	if (templates.length === 0) {
		void vscode.window.showInformationMessage(
			'No pipeline templates found. Add .pipeline files under templates/ at the workspace (repository) root.',
		);
		return;
	}

	const picked = await vscode.window.showQuickPick(templatePicks(templates), {
		title: 'New Pipeline from Template',
		placeHolder: 'Choose a starter pipeline to copy into the workspace',
		matchOnDescription: true,
	});
	if (!picked) {
		return;
	}

	const folder = vscode.workspace.workspaceFolders?.[0];
	const defaultDir = folder?.uri ?? vscode.Uri.joinPath(picked.uri, '..', '..');
	const target = await vscode.window.showSaveDialog({
		defaultUri: vscode.Uri.joinPath(defaultDir, basename(picked.uri)),
		saveLabel: 'Create Pipeline',
		filters: { 'Analysis Pipeline': ['pipeline'] },
		title: 'Save pipeline from template',
	});
	if (!target) {
		return;
	}

	await vscode.workspace.fs.copy(picked.uri, target, { overwrite: true });
	await vscode.commands.executeCommand('vscode.openWith', target, PIPELINE_EDITOR_VIEW_TYPE);
}

function templatePicks(templates: readonly vscode.Uri[]): TemplatePick[] {
	return templates.map((uri) => {
		const name = basename(uri);
		return {
			label: name.replace(/\.pipeline$/u, '').replaceAll('_', ' '),
			description: name,
			uri,
		};
	});
}

function basename(uri: vscode.Uri): string {
	const parts = uri.path.split('/');
	return parts[parts.length - 1] ?? uri.path;
}
