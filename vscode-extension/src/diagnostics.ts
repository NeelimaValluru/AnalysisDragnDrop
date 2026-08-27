/**
 * Validation findings as Problems-panel diagnostics.
 *
 * The expensive part of a validation is starting Python, so the work here is
 * mostly about not doing it: a strict-JSON check runs locally first and short
 * circuits a half-typed file, edits are debounced, and a run whose document has
 * moved on is discarded rather than written over newer results.
 */

import * as vscode from 'vscode';
import { clampSpan, createFindingLocator, findSyntaxProblem } from './findingRanges';
import type { OffsetSpan } from './findingRanges';
import { isPipelineDocument } from './pipelineSource';
import type { PipelineSnapshots } from './pipelineSource';
import { CONFIG_SECTION, PythonBridgeError } from './pythonBridge';
import type { CliRun, PythonBridge } from './pythonBridge';
import {
	DIAGNOSTIC_SOURCE,
	INVALID_JSON_CODE,
	charOffsetFromJsonError,
	interpretValidateOutput,
	isErrorSeverity,
} from './validation';
import type { Finding, ValidationOutcome } from './validation';

/**
 * Long enough that a burst of typing produces one run, short enough that the
 * Problems panel does not feel detached from the editor. Adjust together with
 * the measurements in the README.
 */
const CHANGE_DEBOUNCE_MS = 500;

const ENABLED_SETTING = 'diagnostics.enabled';
const SYNTAX_ERROR_CODE = 'invalid_json';

/** The findings for one document, as last reported. */
export interface DocumentFindings {
	readonly uri: vscode.Uri;
	readonly findings: readonly Finding[];
}

/** Result of an explicit Validate command, including the CLI run when Python ran. */
export interface ValidateNowResult {
	readonly outcome: ValidationOutcome;
	readonly run?: CliRun;
}

export class PipelineDiagnostics implements vscode.Disposable {
	private readonly collection = vscode.languages.createDiagnosticCollection('analysis-gui');
	private readonly pending = new Map<string, NodeJS.Timeout>();
	private readonly inFlight = new Map<string, vscode.CancellationTokenSource>();
	private readonly disposables: vscode.Disposable[] = [];
	/** The bridge being broken is a workspace problem, so say it once, not per keystroke. */
	private announcedBridgeFailure = false;

	/**
	 * The last findings per document, and an event when they change.
	 *
	 * The canvas marks nodes from these rather than running its own validation:
	 * a second `validate` per keystroke would double the Python cost to show the
	 * user something they are already being shown in the Problems panel. It is
	 * the same data, rendered in a second place — not a second source of truth.
	 */
	private readonly latest = new Map<string, DocumentFindings>();
	private readonly findingsEmitter = new vscode.EventEmitter<DocumentFindings>();
	public readonly onDidChangeFindings = this.findingsEmitter.event;

	constructor(
		private readonly bridge: PythonBridge,
		private readonly snapshots: PipelineSnapshots,
		private readonly log: vscode.LogOutputChannel,
	) {}

	public register(context: vscode.ExtensionContext): void {
		this.disposables.push(
			vscode.workspace.onDidOpenTextDocument((document) => {
				this.schedule(document, 0);
			}),
			vscode.workspace.onDidSaveTextDocument((document) => {
				this.schedule(document, 0);
			}),
			vscode.workspace.onDidChangeTextDocument((event) => {
				this.schedule(event.document, CHANGE_DEBOUNCE_MS);
			}),
			vscode.workspace.onDidCloseTextDocument((document) => {
				this.forget(document);
			}),
			vscode.workspace.onDidChangeConfiguration((event) => {
				if (
					event.affectsConfiguration(`${CONFIG_SECTION}.${ENABLED_SETTING}`) ||
					event.affectsConfiguration(`${CONFIG_SECTION}.pythonPath`)
				) {
					this.refreshAllOpen();
				}
			}),
		);

		context.subscriptions.push(this);
		this.refreshAllOpen();
	}

	/** Validates every open pipeline, honouring the current setting. */
	public refreshAllOpen(): void {
		for (const document of vscode.workspace.textDocuments) {
			if (isPipelineDocument(document)) {
				this.schedule(document, 0);
			}
		}
	}

	/**
	 * Runs a validation immediately and returns what came back, so the
	 * `analysisGui.validate` command can report a summary. Diagnostics are
	 * updated as a side effect exactly as they are for an automatic run.
	 *
	 * Does not pop the interpreter dialog: the command owns that, so a click
	 * on Validate is not swallowed by the once-per-session automatic banner.
	 */
	public async validateNow(document: vscode.TextDocument): Promise<ValidateNowResult | undefined> {
		this.cancelPending(document.uri);
		// Explicitly asking to validate should answer even with the setting off:
		// what it governs is the Problems panel, not the command.
		return this.validate(document, this.isEnabled(document), { announce: false });
	}

	/** The findings last reported for a document, for a canvas opening on it. */
	public findingsFor(uri: vscode.Uri): readonly Finding[] {
		return this.latest.get(uri.toString())?.findings ?? [];
	}

	public dispose(): void {
		this.findingsEmitter.dispose();
		for (const timer of this.pending.values()) {
			clearTimeout(timer);
		}
		this.pending.clear();
		for (const source of this.inFlight.values()) {
			source.cancel();
			source.dispose();
		}
		this.inFlight.clear();

		this.collection.dispose();
		for (const disposable of this.disposables) {
			disposable.dispose();
		}
	}

	private schedule(document: vscode.TextDocument, delayMs: number): void {
		if (!isPipelineDocument(document)) {
			return;
		}

		if (!this.isEnabled(document)) {
			this.collection.delete(document.uri);
			return;
		}

		this.cancelPending(document.uri);
		const timer = setTimeout(() => {
			this.pending.delete(document.uri.toString());
			void this.validate(document, true);
		}, delayMs);
		this.pending.set(document.uri.toString(), timer);
	}

	/**
	 * `publish` is false only when the command runs with the setting off: the
	 * validation still happens so the command can report, but nothing is written
	 * to a Problems panel the user asked to keep clear.
	 */
	private async validate(
		document: vscode.TextDocument,
		publish: boolean,
		options: { announce?: boolean } = {},
	): Promise<ValidateNowResult | undefined> {
		const announce = options.announce !== false;
		const show = (diagnostics: vscode.Diagnostic[] | undefined): void => {
			if (publish && diagnostics) {
				this.collection.set(document.uri, diagnostics);
			} else {
				this.collection.delete(document.uri);
			}
		};

		const text = document.getText();

		// A file that is not JSON yet cannot produce findings, only a parse
		// failure — so answer it here and leave Python alone.
		const syntax = findSyntaxProblem(text);
		if (syntax) {
			// No node owns a syntax error, so a canvas has nothing to mark.
			this.publishFindings(document.uri, []);
			show([
				this.diagnostic(
					document,
					syntax,
					syntax.message,
					vscode.DiagnosticSeverity.Error,
					SYNTAX_ERROR_CODE,
				),
			]);
			return { outcome: { kind: 'cliError', code: SYNTAX_ERROR_CODE, message: syntax.message } };
		}

		const key = document.uri.toString();
		const tokenSource = new vscode.CancellationTokenSource();
		this.inFlight.get(key)?.cancel();
		this.inFlight.get(key)?.dispose();
		this.inFlight.set(key, tokenSource);

		const versionAtStart = document.version;
		try {
			const sourcePath = await this.snapshots.pathFor(document.uri);
			const run = await this.bridge.validate(document.uri, {
				sourcePath,
				token: tokenSource.token,
			});

			// A newer revision is already on its way; its result is the truth.
			if (tokenSource.token.isCancellationRequested || document.version !== versionAtStart) {
				return undefined;
			}

			const outcome = interpretValidateOutput(run.stdout);
			if (outcome.kind === 'unreadable') {
				// Python itself failed, which says nothing about the document.
				this.log.warn(`Validation of ${document.uri.fsPath} was inconclusive: ${outcome.detail}`);
				this.publishFindings(document.uri, []);
				show(undefined);
				if (announce) {
					this.announceBridgeFailure(run);
				}
				return { outcome, run };
			}

			this.publishFindings(document.uri, outcome.kind === 'report' ? outcome.report.findings : []);
			show(this.toDiagnostics(document, text, outcome));
			return { outcome, run };
		} catch (error) {
			this.log.warn(`Could not validate ${document.uri.fsPath}: ${describe(error)}`);
			this.publishFindings(document.uri, []);
			show(undefined);
			if (announce) {
				if (error instanceof PythonBridgeError) {
					this.announceStartFailure(error);
				} else {
					this.log.error(`Pipeline diagnostics are unavailable: ${describe(error)}`);
				}
			}
			return undefined;
		} finally {
			if (this.inFlight.get(key) === tokenSource) {
				this.inFlight.delete(key);
			}
			tokenSource.dispose();
		}
	}

	private toDiagnostics(
		document: vscode.TextDocument,
		text: string,
		outcome: ValidationOutcome,
	): vscode.Diagnostic[] {
		if (outcome.kind === 'cliError') {
			const offset =
				outcome.code === INVALID_JSON_CODE ? charOffsetFromJsonError(outcome.message) : undefined;
			return [
				this.diagnostic(
					document,
					{ offset: offset ?? 0, length: offset === undefined ? 0 : 1 },
					outcome.message,
					vscode.DiagnosticSeverity.Error,
					outcome.code,
				),
			];
		}

		if (outcome.kind !== 'report') {
			return [];
		}

		const locate = createFindingLocator(text);
		return outcome.report.findings.map((finding) =>
			this.diagnostic(
				document,
				locate(finding),
				finding.message,
				severityOf(finding),
				finding.code,
			),
		);
	}

	private diagnostic(
		document: vscode.TextDocument,
		span: OffsetSpan,
		message: string,
		severity: vscode.DiagnosticSeverity,
		code: string,
	): vscode.Diagnostic {
		const safe = clampSpan(span, document.getText().length);
		const range = new vscode.Range(
			document.positionAt(safe.offset),
			document.positionAt(safe.offset + safe.length),
		);

		const diagnostic = new vscode.Diagnostic(range, message, severity);
		diagnostic.source = DIAGNOSTIC_SOURCE;
		diagnostic.code = code;
		return diagnostic;
	}

	private announceBridgeFailure(run: CliRun): void {
		if (this.announcedBridgeFailure) {
			return;
		}
		this.announcedBridgeFailure = true;
		void this.bridge.reportFailure(run);
	}

	private announceStartFailure(error: PythonBridgeError): void {
		if (this.announcedBridgeFailure) {
			return;
		}
		this.announcedBridgeFailure = true;
		void this.bridge.reportStartFailure(error);
	}

	/** Records findings and notifies listeners only when they actually changed. */
	private publishFindings(uri: vscode.Uri, findings: readonly Finding[]): void {
		const key = uri.toString();
		const previous = this.latest.get(key);
		if (previous && sameFindings(previous.findings, findings)) {
			return;
		}

		const entry: DocumentFindings = { uri, findings };
		this.latest.set(key, entry);
		this.findingsEmitter.fire(entry);
	}

	private cancelPending(uri: vscode.Uri): void {
		const key = uri.toString();
		const timer = this.pending.get(key);
		if (timer) {
			clearTimeout(timer);
			this.pending.delete(key);
		}
	}

	private forget(document: vscode.TextDocument): void {
		if (!isPipelineDocument(document)) {
			return;
		}

		const key = document.uri.toString();
		this.cancelPending(document.uri);
		this.inFlight.get(key)?.cancel();
		this.collection.delete(document.uri);
		this.latest.delete(key);
		void this.snapshots.release(document.uri);
	}

	private isEnabled(document: vscode.TextDocument): boolean {
		return vscode.workspace
			.getConfiguration(CONFIG_SECTION, document.uri)
			.get<boolean>(ENABLED_SETTING, true);
	}
}

function sameFindings(a: readonly Finding[], b: readonly Finding[]): boolean {
	return a.length === b.length && JSON.stringify(a) === JSON.stringify(b);
}

function severityOf(finding: Finding): vscode.DiagnosticSeverity {
	return isErrorSeverity(finding) ? vscode.DiagnosticSeverity.Error : vscode.DiagnosticSeverity.Warning;
}

function describe(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}
