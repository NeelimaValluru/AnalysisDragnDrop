/**
 * The single place the extension shells out to Python.
 *
 * Every subprocess goes through `PythonBridge` so interpreter resolution,
 * logging, and error reporting stay consistent. Nothing in here understands the
 * `.pipeline` schema: the bridge moves bytes and exit codes, and callers decide
 * what the JSON means.
 *
 * CLI contract (implemented in the Python package as `analysis_gui/cli.py`):
 *
 *   python -m analysis_gui.cli codegen <file.pipeline> [-o out.py] [--json]
 *   python -m analysis_gui.cli validate <file.pipeline> --json
 *   python -m analysis_gui.cli describe --json
 *   python -m analysis_gui.cli discover --json [--workspace dir] [--root dir]
 *   python -m analysis_gui.cli similar "<query>" --json
 *   python -m analysis_gui.cli run <file.pipeline> [--cwd dir]
 *
 * Subcommands write JSON to stdout and human-readable messages to stderr, and
 * every JSON payload includes `schema_version` and `analysis_gui_version`. The
 * one exception is `codegen` with neither `-o` nor `--json`, which writes the
 * generated Python itself to stdout so it can be piped straight to a file.
 *
 * `run` may also emit NDJSON progress lines on stderr (or stdout):
 *
 *   {"type":"runProgress","nodeId":"<id>","state":"pending"|"running"|"ok"|"error"}
 *
 * The host parses those when present; otherwise it synthesises the same
 * messages from document topo order. See `runProgress.ts`.
 *
 * Exit codes: 0 success, 1 failure, 2 usage error.
 */

import { spawn } from 'node:child_process';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { desktopAppShellArgs, discoverLibraryArgv, runPipelineArgv, similarCodeArgv } from './cliArgs';
import {
	INSTALL_COMMAND,
	describeFailure as describeCliFailure,
	needsInterpreterHelp,
} from './interpreterMessages';

export {
	INSTALL_COMMAND,
	USAGE_EXIT_CODE,
	describeFailure,
	isAnalysisGuiMissing,
	needsInterpreterHelp,
} from './interpreterMessages';

export const PYTHON_EXTENSION_ID = 'ms-python.python';
export const CONFIG_SECTION = 'analysisGui';

const CLI_MODULE = 'analysis_gui.cli';
const DESKTOP_ENTRY_POINT = 'analysis-gui';
const FALLBACK_INTERPRETER = process.platform === 'win32' ? 'python' : 'python3';

const SELECT_INTERPRETER = 'Select Interpreter';
const SET_PYTHON_PATH = 'Set Python Path';
const COPY_INSTALL = 'Copy Install Command';
const SHOW_LOG = 'Show Log';

export type InterpreterOrigin = 'python-extension' | 'setting' | 'path';

export interface ResolvedInterpreter {
	/** Executable to spawn. Absolute unless we fell back to PATH lookup. */
	readonly command: string;
	readonly origin: InterpreterOrigin;
	/** Human-readable form for logs and error messages. */
	readonly label: string;
}

export interface CliRun {
	readonly ok: boolean;
	readonly interpreter: ResolvedInterpreter;
	/** Arguments after the interpreter, i.e. `['-m', 'analysis_gui.cli', ...]`. */
	readonly argv: readonly string[];
	readonly exitCode: number | null;
	readonly signal: NodeJS.Signals | null;
	readonly stdout: string;
	readonly stderr: string;
	readonly durationMs: number;
}

/**
 * Fields the CLI contract guarantees on every payload. Deliberately open-ended:
 * the pipeline schema is still in flux, so callers narrow further themselves.
 */
export interface CliEnvelope {
	/** Integer, not a string: the CLI emits `SCHEMA_VERSION` verbatim. */
	readonly schema_version?: number;
	readonly analysis_gui_version?: string;
	readonly [key: string]: unknown;
}

export interface ValidateOptions {
	/** Path to feed the CLI, when it differs from `pipeline.fsPath`. */
	readonly sourcePath?: string;
	readonly token?: vscode.CancellationToken;
}

export interface CodegenOptions extends ValidateOptions {
	/** Passed as `-o`; makes the CLI write the file and print a JSON receipt. */
	readonly outFile?: vscode.Uri;
	/** Passed as `--json`; wraps the generated code in an envelope. */
	readonly json?: boolean;
}

export interface RunPipelineOptions extends ValidateOptions {
	/**
	 * Working directory for the generated script. Defaults to the pipeline
	 * file's directory so relative CSV paths resolve. Pass the original
	 * document's folder when `sourcePath` is a scratch copy of an unsaved buffer.
	 */
	readonly cwd?: string;
	readonly onStdout?: (chunk: string) => void;
	readonly onStderr?: (chunk: string) => void;
}

export interface DiscoverOptions {
	readonly resource?: vscode.Uri;
	readonly workspace?: string;
	readonly roots?: readonly string[];
	readonly token?: vscode.CancellationToken;
}

export interface SimilarOptions extends DiscoverOptions {
	readonly limit?: number;
}

export interface RunCliOptions {
	/** Document the command acts on; scopes settings and picks the interpreter. */
	readonly resource?: vscode.Uri;
	readonly cwd?: string;
	readonly interpreter?: ResolvedInterpreter;
	readonly token?: vscode.CancellationToken;
	readonly onStdout?: (chunk: string) => void;
	readonly onStderr?: (chunk: string) => void;
}

/** Failure to *start* Python, as opposed to Python running and exiting nonzero. */
export class PythonBridgeError extends Error {
	public readonly detail: string | undefined;
	/** When false, this is not an interpreter problem (e.g. no display for Qt). */
	public readonly interpreterHelp: boolean;

	constructor(message: string, detail?: string, interpreterHelp = true) {
		super(message);
		this.name = 'PythonBridgeError';
		this.detail = detail;
		this.interpreterHelp = interpreterHelp;
	}
}

/**
 * Slice of the `ms-python.python` API we depend on, declared structurally so the
 * extension needs no runtime dependency on `@vscode/python-extension`.
 */
interface PythonEnvironmentPath {
	readonly id: string;
	readonly path: string;
}

interface ResolvedPythonEnvironment {
	readonly executable?: {
		readonly uri?: vscode.Uri;
	};
}

interface PythonExtensionApi {
	readonly environments: {
		getActiveEnvironmentPath(resource?: vscode.Uri): PythonEnvironmentPath | undefined;
		resolveEnvironment(env: PythonEnvironmentPath): Thenable<ResolvedPythonEnvironment | undefined>;
	};
}

export class PythonBridge {
	constructor(private readonly log: vscode.LogOutputChannel) {}

	/**
	 * Resolution order, highest priority first:
	 *
	 *   1. the `analysisGui.pythonPath` setting
	 *   2. the environment selected in the `ms-python.python` extension
	 *   3. `python3` (`python` on Windows) from PATH
	 *
	 * The setting wins because it is an explicit, project-specific choice: the
	 * environment that can import `analysis_gui` is not always the one the user
	 * wants selected for editing Python. Leaving it empty — the default — hands
	 * control back to "Python: Select Interpreter".
	 */
	public async resolveInterpreter(resource?: vscode.Uri): Promise<ResolvedInterpreter> {
		const fromSetting = this.interpreterFromSetting(resource);
		if (fromSetting) {
			return fromSetting;
		}

		const fromPythonExtension = await this.interpreterFromPythonExtension(resource);
		if (fromPythonExtension) {
			return fromPythonExtension;
		}

		return {
			command: FALLBACK_INTERPRETER,
			origin: 'path',
			label: `${FALLBACK_INTERPRETER} (resolved from PATH)`,
		};
	}

	/** Runs `python -m analysis_gui.cli <args>` and captures the whole result. */
	public async runCli(args: readonly string[], options: RunCliOptions = {}): Promise<CliRun> {
		const interpreter = options.interpreter ?? (await this.resolveInterpreter(options.resource));
		const argv = ['-m', CLI_MODULE, ...args];
		const cwd = options.cwd ?? this.defaultCwd(options.resource);

		this.log.info(`Running: ${interpreter.command} ${argv.join(' ')}`);
		const run = await this.spawnCapture(interpreter, argv, cwd, options);

		this.log.info(
			`Exited with code ${String(run.exitCode)}${run.signal ? ` (signal ${run.signal})` : ''} in ${run.durationMs}ms`,
		);
		if (run.stderr.trim()) {
			this.log.debug(`stderr: ${run.stderr.trim()}`);
		}

		return run;
	}

	/**
	 * `sourcePath` is separate from `pipeline` so callers can generate from an
	 * unsaved buffer written to a scratch file while still resolving settings
	 * and the interpreter against the document's real location.
	 */
	public async codegen(pipeline: vscode.Uri, options: CodegenOptions = {}): Promise<CliRun> {
		const args = [
			'codegen',
			options.sourcePath ?? pipeline.fsPath,
			...(options.outFile ? ['-o', options.outFile.fsPath] : []),
			...(options.json ? ['--json'] : []),
		];
		return this.runCli(args, { resource: pipeline, token: options.token });
	}

	public async validate(pipeline: vscode.Uri, options: ValidateOptions = {}): Promise<CliRun> {
		const args = ['validate', options.sourcePath ?? pipeline.fsPath, '--json'];
		return this.runCli(args, { resource: pipeline, token: options.token });
	}

	public async describe(resource?: vscode.Uri, token?: vscode.CancellationToken): Promise<CliRun> {
		return this.runCli(['describe', '--json'], { resource, token });
	}

	public async discover(options: DiscoverOptions = {}): Promise<CliRun> {
		const workspace = options.workspace ?? this.defaultCwd(options.resource);
		return this.runCli(discoverLibraryArgv({ workspace, roots: options.roots }), {
			resource: options.resource,
			cwd: workspace,
			token: options.token,
		});
	}

	public async similar(query: string, options: SimilarOptions = {}): Promise<CliRun> {
		const workspace = options.workspace ?? this.defaultCwd(options.resource);
		return this.runCli(similarCodeArgv(query, { workspace, roots: options.roots, limit: options.limit }), {
			resource: options.resource,
			cwd: workspace,
			token: options.token,
		});
	}

	/**
	 * Generate and execute a pipeline. `sourcePath` is the file the CLI reads
	 * (a scratch copy for unsaved buffers); `cwd` is where relative data paths
	 * resolve, which should stay the original document's directory.
	 */
	public async runPipeline(pipeline: vscode.Uri, options: RunPipelineOptions = {}): Promise<CliRun> {
		const sourcePath = options.sourcePath ?? pipeline.fsPath;
		const cwd = options.cwd ?? path.dirname(pipeline.fsPath);
		return this.runCli(runPipelineArgv(sourcePath, cwd), {
			resource: pipeline,
			cwd,
			token: options.token,
			onStdout: options.onStdout,
			onStderr: options.onStderr,
		});
	}

	/**
	 * Parses stdout as the CLI's JSON envelope. Returns `undefined` rather than
	 * throwing so a malformed payload degrades to "no data" instead of breaking
	 * the caller; the reason is always logged.
	 */
	public parseJson<T extends CliEnvelope = CliEnvelope>(run: CliRun): T | undefined {
		if (!run.stdout.trim()) {
			this.log.warn('Expected JSON on stdout but the CLI produced none.');
			return undefined;
		}

		try {
			return JSON.parse(run.stdout) as T;
		} catch (error) {
			this.log.warn(`Could not parse CLI stdout as JSON: ${errorMessage(error)}`);
			return undefined;
		}
	}

	/**
	 * When `analysisGui.pythonPath` is empty and the workspace has a `.venv`
	 * interpreter, mention it in the log. Never writes the setting: the Python
	 * extension's selection stays in charge unless the user opts in.
	 */
	public async noteWorkspaceVenvHint(): Promise<void> {
		const configured = vscode.workspace.getConfiguration(CONFIG_SECTION).get<string>('pythonPath', '').trim();
		if (configured) {
			return;
		}

		const folder = vscode.workspace.workspaceFolders?.[0];
		if (!folder || folder.uri.scheme !== 'file') {
			return;
		}

		const relative =
			process.platform === 'win32' ? ['.venv', 'Scripts', 'python.exe'] : ['.venv', 'bin', 'python'];
		const candidate = vscode.Uri.joinPath(folder.uri, ...relative);
		if (!(await fileExists(candidate.fsPath))) {
			return;
		}

		this.log.info(
			`Workspace has ${candidate.fsPath}. It is not selected automatically — pick it with ` +
				`"Python: Select Interpreter" or set ${CONFIG_SECTION}.pythonPath if that environment ` +
				`is the one that can import analysis_gui.`,
		);
	}

	/** Shows a failed run to the user with the most actionable next step. */
	public async reportFailure(run: CliRun): Promise<void> {
		const message = describeCliFailure(run);
		if (needsInterpreterHelp(run)) {
			await this.offerInterpreterHelp(message);
			return;
		}

		const choice = await vscode.window.showErrorMessage(message, SHOW_LOG);
		if (choice === SHOW_LOG) {
			this.log.show(true);
		}
	}

	/**
	 * Spawn never started (ENOENT, and similar). Same actions as a missing
	 * package: the interpreter on file is the thing to fix.
	 */
	public async reportStartFailure(error: PythonBridgeError): Promise<void> {
		const message = error.detail ? `${error.message} ${error.detail}` : error.message;
		if (error.interpreterHelp) {
			await this.offerInterpreterHelp(message);
			return;
		}
		await vscode.window.showErrorMessage(message);
	}

	public async offerInterpreterHelp(message: string): Promise<void> {
		const choice = await vscode.window.showErrorMessage(
			message,
			SELECT_INTERPRETER,
			SET_PYTHON_PATH,
			COPY_INSTALL,
			SHOW_LOG,
		);

		if (choice === SELECT_INTERPRETER) {
			await vscode.commands.executeCommand('python.setInterpreter');
		} else if (choice === SET_PYTHON_PATH) {
			await vscode.commands.executeCommand('workbench.action.openSettings', `${CONFIG_SECTION}.pythonPath`);
		} else if (choice === COPY_INSTALL) {
			await vscode.env.clipboard.writeText(INSTALL_COMMAND);
			void vscode.window.showInformationMessage(`Copied "${INSTALL_COMMAND}" to the clipboard.`);
		} else if (choice === SHOW_LOG) {
			this.log.show(true);
		}
	}

	/**
	 * Starts the `analysis-gui` console script in a terminal.
	 *
	 * A terminal rather than a detached `spawn` so Qt's startup errors are
	 * visible, and `shellPath` rather than `sendText` so there is no shell
	 * quoting to get wrong on paths containing spaces.
	 */
	public async launchDesktopApp(resource?: vscode.Uri): Promise<void> {
		const blocker = describeDesktopBlocker();
		if (blocker) {
			throw new PythonBridgeError(blocker.message, blocker.detail, false);
		}

		const interpreter = await this.resolveInterpreter(resource);
		const executable = await this.locateDesktopEntryPoint(interpreter);

		this.log.info(`Launching desktop app: ${executable}${resource ? ` ${resource.fsPath}` : ''}`);
		const terminal = vscode.window.createTerminal({
			name: 'Analysis GUI',
			iconPath: new vscode.ThemeIcon('window'),
			shellPath: executable,
			shellArgs: desktopAppShellArgs(resource?.scheme === 'file' ? resource.fsPath : undefined),
			cwd: this.defaultCwd(resource),
		});
		terminal.show(true);
	}

	private async interpreterFromPythonExtension(resource?: vscode.Uri): Promise<ResolvedInterpreter | undefined> {
		const extension = vscode.extensions.getExtension<PythonExtensionApi>(PYTHON_EXTENSION_ID);
		if (!extension) {
			this.log.debug(`${PYTHON_EXTENSION_ID} is not installed; skipping its interpreter API.`);
			return undefined;
		}

		try {
			const api = extension.isActive ? extension.exports : await extension.activate();
			const environmentPath = api.environments.getActiveEnvironmentPath(resource);
			if (!environmentPath?.path) {
				this.log.debug(`${PYTHON_EXTENSION_ID} reports no active environment.`);
				return undefined;
			}

			// `path` may point at an environment folder rather than an executable,
			// so ask the extension to resolve it before spawning.
			const resolved = await api.environments.resolveEnvironment(environmentPath);
			const command = resolved?.executable?.uri?.fsPath ?? environmentPath.path;

			return {
				command,
				origin: 'python-extension',
				label: `${command} (selected in the Python extension)`,
			};
		} catch (error) {
			this.log.warn(`Could not read the interpreter from ${PYTHON_EXTENSION_ID}: ${errorMessage(error)}`);
			return undefined;
		}
	}

	private interpreterFromSetting(resource?: vscode.Uri): ResolvedInterpreter | undefined {
		const configured = vscode.workspace
			.getConfiguration(CONFIG_SECTION, resource)
			.get<string>('pythonPath', '')
			.trim();
		if (!configured) {
			return undefined;
		}

		const command = expandPath(configured, resource);
		return {
			command,
			origin: 'setting',
			label: `${command} (${CONFIG_SECTION}.pythonPath)`,
		};
	}

	private spawnCapture(
		interpreter: ResolvedInterpreter,
		argv: readonly string[],
		cwd: string | undefined,
		options: RunCliOptions,
	): Promise<CliRun> {
		return new Promise<CliRun>((resolve, reject) => {
			const startedAt = Date.now();
			const child = spawn(interpreter.command, [...argv], {
				cwd,
				env: {
					...process.env,
					PYTHONIOENCODING: 'utf-8',
					PYTHONUNBUFFERED: '1',
				},
				shell: false,
				windowsHide: true,
			});

			let stdout = '';
			let stderr = '';
			child.stdout?.setEncoding('utf8');
			child.stderr?.setEncoding('utf8');
			child.stdout?.on('data', (chunk: string) => {
				stdout += chunk;
				options.onStdout?.(chunk);
			});
			child.stderr?.on('data', (chunk: string) => {
				stderr += chunk;
				options.onStderr?.(chunk);
			});

			const cancellation = options.token?.onCancellationRequested(() => {
				child.kill();
			});

			child.on('error', (error) => {
				cancellation?.dispose();
				reject(startFailure(interpreter, error));
			});

			child.on('close', (exitCode, signal) => {
				cancellation?.dispose();
				resolve({
					ok: exitCode === 0,
					interpreter,
					argv,
					exitCode,
					signal,
					stdout,
					stderr,
					durationMs: Date.now() - startedAt,
				});
			});
		});
	}

	private async locateDesktopEntryPoint(interpreter: ResolvedInterpreter): Promise<string> {
		if (path.isAbsolute(interpreter.command)) {
			const binDirectory = path.dirname(interpreter.command);
			const name = process.platform === 'win32' ? `${DESKTOP_ENTRY_POINT}.exe` : DESKTOP_ENTRY_POINT;
			const candidates =
				process.platform === 'win32'
					? [path.join(binDirectory, name), path.join(binDirectory, 'Scripts', name)]
					: [path.join(binDirectory, name)];

			for (const candidate of candidates) {
				if (await fileExists(candidate)) {
					return candidate;
				}
			}

			this.log.debug(`No ${DESKTOP_ENTRY_POINT} script beside ${interpreter.command}; falling back to PATH.`);
		}

		return DESKTOP_ENTRY_POINT;
	}

	private defaultCwd(resource?: vscode.Uri): string | undefined {
		const folder = (resource ? vscode.workspace.getWorkspaceFolder(resource) : undefined) ?? vscode.workspace.workspaceFolders?.[0];
		return folder?.uri.scheme === 'file' ? folder.uri.fsPath : undefined;
	}
}

/**
 * The desktop app opens an OS window, which only works when the extension host
 * runs on the same machine as the UI.
 */
function describeDesktopBlocker(): { message: string; detail: string } | undefined {
	if (vscode.env.uiKind === vscode.UIKind.Web) {
		return {
			message: 'The Analysis GUI desktop app cannot be launched from a browser-based editor.',
			detail: 'Open the workspace in desktop VS Code, or run "analysis-gui" from a local terminal.',
		};
	}

	if (vscode.env.remoteName !== undefined) {
		return {
			message: `The Analysis GUI desktop app cannot be launched from a remote workspace (${vscode.env.remoteName}).`,
			detail:
				'This extension runs on the remote machine, so the app window would have no display to open on. ' +
				'Run "analysis-gui" on your local machine instead.',
		};
	}

	return undefined;
}

function startFailure(interpreter: ResolvedInterpreter, error: Error): PythonBridgeError {
	const code = (error as NodeJS.ErrnoException).code;
	if (code === 'ENOENT') {
		return new PythonBridgeError(
			`Could not find a Python interpreter at ${interpreter.label}.`,
			'Pick one with "Python: Select Interpreter", or set "analysisGui.pythonPath" to a full path.',
		);
	}

	return new PythonBridgeError(`Could not start ${interpreter.label}: ${error.message}`);
}

function expandPath(raw: string, resource?: vscode.Uri): string {
	const folder = (resource ? vscode.workspace.getWorkspaceFolder(resource) : undefined) ?? vscode.workspace.workspaceFolders?.[0];

	let value = raw;
	if (folder) {
		value = value.replaceAll('${workspaceFolder}', folder.uri.fsPath);
	}
	if (value === '~' || value.startsWith(`~${path.sep}`) || value.startsWith('~/')) {
		value = path.join(os.homedir(), value.slice(1));
	}

	return value;
}

async function fileExists(candidate: string): Promise<boolean> {
	try {
		await vscode.workspace.fs.stat(vscode.Uri.file(candidate));
		return true;
	} catch {
		return false;
	}
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
