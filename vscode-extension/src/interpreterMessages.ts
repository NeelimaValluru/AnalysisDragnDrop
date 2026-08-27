/**
 * Actionable copy for a failed CLI run, kept free of `vscode` so it can be
 * tested under `node:test`.
 *
 * The buttons that act on these messages live on `PythonBridge.reportFailure`.
 */

/** Matches CPython's ModuleNotFoundError text for our package, quoted or not. */
export const MISSING_MODULE_PATTERN = /No module named ['"]?analysis_gui/;

export const INSTALL_COMMAND = 'pip install -e .';

/** argparse usage error, per `analysis_gui.cli`. */
export const USAGE_EXIT_CODE = 2;

const CLI_NAME = 'analysis_gui.cli';

/** The fields `describeFailure` reads. A `CliRun` satisfies this. */
export interface CliFailureView {
	readonly interpreter: { readonly label: string };
	readonly argv: readonly string[];
	readonly exitCode: number | null;
	readonly stderr: string;
}

/** True when the run failed because `analysis_gui` is not importable. */
export function isAnalysisGuiMissing(run: CliFailureView): boolean {
	return MISSING_MODULE_PATTERN.test(run.stderr);
}

/**
 * True when the user should pick an interpreter, set `analysisGui.pythonPath`,
 * or install the package — as opposed to a pipeline that simply failed.
 */
export function needsInterpreterHelp(run: CliFailureView): boolean {
	return isAnalysisGuiMissing(run) || run.exitCode === USAGE_EXIT_CODE;
}

/** One-line, actionable description of a nonzero exit. */
export function describeFailure(run: CliFailureView): string {
	if (isAnalysisGuiMissing(run)) {
		return (
			`The analysis_gui package is not importable by ${run.interpreter.label}. ` +
			`Pick a Python interpreter, set analysisGui.pythonPath, or run "${INSTALL_COMMAND}" ` +
			`from the AnalysisGUI repository root in that environment.`
		);
	}

	if (run.exitCode === USAGE_EXIT_CODE) {
		return (
			`${CLI_NAME} exited with code ${String(USAGE_EXIT_CODE)} (usage error) under ${run.interpreter.label}. ` +
			`The selected interpreter may not have analysis_gui installed, or the CLI is too old. ` +
			`Pick a Python interpreter, set analysisGui.pythonPath, or run "${INSTALL_COMMAND}" ` +
			`from the AnalysisGUI repository root.`
		);
	}

	const subcommand = run.argv[2] ?? CLI_NAME;
	// Last line, not first: for a traceback that is the actual exception message.
	const lastStderrLine = run.stderr.trim().split(/\r?\n/).at(-1);
	const suffix = lastStderrLine ? `: ${lastStderrLine}` : '.';
	return `${CLI_NAME} ${subcommand} exited with code ${String(run.exitCode)}${suffix}`;
}
