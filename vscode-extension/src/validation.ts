/**
 * The shape of `analysis_gui.cli validate --json`, and the pure logic that
 * turns its stdout into something the diagnostics layer can consume.
 *
 * No `vscode` import on purpose: everything here is testable under plain Node.
 */

export const DIAGNOSTIC_SOURCE = 'analysis-gui';

export type FindingSeverity = 'error' | 'warning';

export interface Finding {
	readonly severity: string;
	readonly code: string;
	readonly message: string;
	/** A key in the `nodes` object, or — for `dangling_edge` — a missing one. */
	readonly node_id?: string;
	/** Index into the `edges` array. */
	readonly edge_index?: number;
}

export interface ValidationReport {
	readonly valid: boolean;
	readonly file_schema_version?: number;
	readonly node_count?: number;
	readonly edge_count?: number;
	readonly findings: readonly Finding[];
	readonly summary?: { readonly errors: number; readonly warnings: number };
}

/**
 * A validate run lands in exactly one of three states. `cliError` is the shape
 * the CLI uses when it never got as far as validating — an unreadable file or
 * unparseable JSON — and carries no `findings` array at all.
 */
export type ValidationOutcome =
	| { readonly kind: 'report'; readonly report: ValidationReport }
	| { readonly kind: 'cliError'; readonly code: string; readonly message: string }
	| { readonly kind: 'unreadable'; readonly detail: string };

/** Codes the CLI uses when it could not even read the document. */
export const INVALID_JSON_CODE = 'invalid_json';

export function isErrorSeverity(finding: Finding): boolean {
	return finding.severity !== 'warning';
}

/**
 * Interprets validate's stdout. Never throws: a payload we do not recognise
 * becomes `unreadable` so the caller can surface one honest diagnostic instead
 * of silently reporting a clean file.
 */
export function interpretValidateOutput(stdout: string): ValidationOutcome {
	const trimmed = stdout.trim();
	if (!trimmed) {
		return { kind: 'unreadable', detail: 'The validate command produced no output.' };
	}

	let payload: unknown;
	try {
		payload = JSON.parse(trimmed);
	} catch (error) {
		return {
			kind: 'unreadable',
			detail: `Could not parse the validate payload as JSON: ${
				error instanceof Error ? error.message : String(error)
			}`,
		};
	}

	if (!isRecord(payload)) {
		return { kind: 'unreadable', detail: 'The validate payload was not a JSON object.' };
	}

	const error = payload['error'];
	if (isRecord(error)) {
		return {
			kind: 'cliError',
			code: asString(error['code']) ?? 'cli_error',
			message: asString(error['message']) ?? 'The pipeline could not be validated.',
		};
	}

	const rawFindings = payload['findings'];
	if (!Array.isArray(rawFindings)) {
		return { kind: 'unreadable', detail: 'The validate payload had no "findings" array.' };
	}

	return {
		kind: 'report',
		report: {
			valid: payload['valid'] === true,
			file_schema_version: asNumber(payload['file_schema_version']),
			node_count: asNumber(payload['node_count']),
			edge_count: asNumber(payload['edge_count']),
			findings: rawFindings.filter(isRecord).map(toFinding),
			summary: toSummary(payload['summary']),
		},
	};
}

/**
 * Pulls the byte offset out of a CPython `json` decode error, which always ends
 * in `(char N)`. Lets an `invalid_json` report from the CLI land on the offending
 * character instead of line 1.
 */
export function charOffsetFromJsonError(message: string): number | undefined {
	const match = /\(char (\d+)\)/.exec(message);
	if (!match?.[1]) {
		return undefined;
	}
	const offset = Number.parseInt(match[1], 10);
	return Number.isFinite(offset) ? offset : undefined;
}

/** One-line count of what a report found, for a status message. */
export function summarize(report: ValidationReport): string {
	const errors = report.summary?.errors ?? report.findings.filter(isErrorSeverity).length;
	const warnings = report.summary?.warnings ?? report.findings.length - errors;

	if (!errors && !warnings) {
		return 'no problems';
	}
	return [
		errors ? `${errors} ${errors === 1 ? 'error' : 'errors'}` : undefined,
		warnings ? `${warnings} ${warnings === 1 ? 'warning' : 'warnings'}` : undefined,
	]
		.filter((part): part is string => part !== undefined)
		.join(' and ');
}

function toFinding(raw: Record<string, unknown>): Finding {
	return {
		severity: asString(raw['severity']) ?? 'error',
		code: asString(raw['code']) ?? 'unknown',
		message: asString(raw['message']) ?? 'Unspecified validation problem.',
		node_id: asString(raw['node_id']),
		edge_index: asNumber(raw['edge_index']),
	};
}

function toSummary(raw: unknown): { errors: number; warnings: number } | undefined {
	if (!isRecord(raw)) {
		return undefined;
	}
	const errors = asNumber(raw['errors']);
	const warnings = asNumber(raw['warnings']);
	return errors === undefined || warnings === undefined ? undefined : { errors, warnings };
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
	return typeof value === 'string' ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
	return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
