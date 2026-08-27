/**
 * What counts as a pipeline document, and how to hand one to the CLI.
 *
 * The CLI takes a path and reads it from disk, but the editor's copy of a file
 * is the interesting one: diagnostics that lag a save are worse than none, and
 * previewing a buffer you just edited should show what you just edited. So an
 * unsaved document is mirrored to a scratch file and the CLI is pointed there.
 * Ranges are still computed against the editor's text, which is the same bytes.
 */

import * as crypto from 'node:crypto';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';

export const PIPELINE_LANGUAGE_ID = 'analysis-pipeline';
export const PIPELINE_EXTENSION = '.pipeline';

/** Schemes worth validating. Excludes diff/SCM views and our own previews. */
const SUPPORTED_SCHEMES = new Set(['file', 'untitled']);

export function isPipelineDocument(document: vscode.TextDocument): boolean {
	return SUPPORTED_SCHEMES.has(document.uri.scheme) && isPipelineUri(document.uri, document.languageId);
}

export function isPipelineUri(uri: vscode.Uri, languageId?: string): boolean {
	return languageId === PIPELINE_LANGUAGE_ID || uri.path.toLowerCase().endsWith(PIPELINE_EXTENSION);
}

export function basename(uri: vscode.Uri): string {
	return uri.path.split('/').at(-1) ?? uri.fsPath;
}

/** `demo.pipeline` -> `demo`. */
export function stem(uri: vscode.Uri): string {
	const name = basename(uri);
	const dot = name.lastIndexOf('.');
	return dot > 0 ? name.slice(0, dot) : name;
}

/**
 * Scratch copies of unsaved buffers, one per document and reused in place so a
 * fast typist does not churn through temp files.
 */
export class PipelineSnapshots implements vscode.Disposable {
	private readonly scratchRoot = vscode.Uri.file(path.join(os.tmpdir(), 'analysis-gui-vscode'));
	private readonly written = new Map<string, vscode.Uri>();
	private rootReady: Thenable<void> | undefined;

	constructor(private readonly log: vscode.LogOutputChannel) {}

	/**
	 * The path to give the CLI for this document: its own, when what is on disk
	 * matches the editor, and a scratch copy otherwise.
	 */
	public async pathFor(uri: vscode.Uri): Promise<string> {
		const document = vscode.workspace.textDocuments.find(
			(candidate) => candidate.uri.toString() === uri.toString(),
		);

		if (!document || (!document.isDirty && uri.scheme === 'file')) {
			return uri.fsPath;
		}

		return this.writeScratch(uri, document.getText());
	}

	/** Drops the scratch copy for a document, if it has one. */
	public async release(uri: vscode.Uri): Promise<void> {
		const key = uri.toString();
		const scratch = this.written.get(key);
		if (!scratch) {
			return;
		}

		this.written.delete(key);
		await this.remove(scratch);
	}

	public dispose(): void {
		const remaining = [...this.written.values()];
		this.written.clear();
		void Promise.all(remaining.map((scratch) => this.remove(scratch)));
	}

	private async writeScratch(uri: vscode.Uri, text: string): Promise<string> {
		this.rootReady ??= vscode.workspace.fs.createDirectory(this.scratchRoot);
		await this.rootReady;

		const key = uri.toString();
		// Name derived from the URI, not random, so repeated writes overwrite.
		const digest = crypto.createHash('sha1').update(key).digest('hex').slice(0, 12);
		const scratch =
			this.written.get(key) ??
			vscode.Uri.joinPath(this.scratchRoot, `${digest}-${basename(uri)}`);

		await vscode.workspace.fs.writeFile(scratch, Buffer.from(text, 'utf8'));
		this.written.set(key, scratch);
		return scratch.fsPath;
	}

	private async remove(scratch: vscode.Uri): Promise<void> {
		try {
			await vscode.workspace.fs.delete(scratch, { useTrash: false });
		} catch (error) {
			this.log.debug(`Could not remove scratch file ${scratch.fsPath}: ${String(error)}`);
		}
	}
}
