/**
 * Pure argument builders for the Python CLI and desktop entry point.
 *
 * Kept free of `vscode` so the unit tests can assert spawn argv without an
 * Extension Development Host.
 */

/** `python -m analysis_gui.cli run <file> [--cwd dir]` */
export function runPipelineArgv(sourcePath: string, cwd?: string): string[] {
	const args = ['run', sourcePath];
	if (cwd) {
		args.push('--cwd', cwd);
	}
	return args;
}

/** `python -m analysis_gui.cli discover --json [--workspace dir] [--root dir ...]` */
export function discoverLibraryArgv(options: { workspace?: string; roots?: readonly string[] } = {}): string[] {
	const args = ['discover', '--json'];
	if (options.workspace) {
		args.push('--workspace', options.workspace);
	}
	for (const root of options.roots ?? []) {
		args.push('--root', root);
	}
	return args;
}

/** `python -m analysis_gui.cli similar <query> --json [--workspace dir] [--root dir ...]` */
export function similarCodeArgv(
	query: string,
	options: { workspace?: string; roots?: readonly string[]; limit?: number } = {},
): string[] {
	const args = ['similar', query, '--json'];
	if (options.workspace) {
		args.push('--workspace', options.workspace);
	}
	for (const root of options.roots ?? []) {
		args.push('--root', root);
	}
	if (options.limit !== undefined) {
		args.push('--limit', String(options.limit));
	}
	return args;
}

/** Positional file argument for the `analysis-gui` console script. */
export function desktopAppShellArgs(pipelineFsPath?: string): string[] {
	return pipelineFsPath ? [pipelineFsPath] : [];
}
