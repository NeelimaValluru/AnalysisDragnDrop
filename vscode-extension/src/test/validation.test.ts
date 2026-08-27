/**
 * Reading `validate --json`. The payloads below are copied verbatim from real
 * CLI runs against `fixtures/`, so a change to the contract breaks these tests
 * rather than the Problems panel.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { charOffsetFromJsonError, interpretValidateOutput, summarize } from '../validation';

const VALID = `{
  "schema_version": 1,
  "analysis_gui_version": "0.1.0",
  "status": "ok",
  "command": "validate",
  "file": "fixtures/valid.pipeline",
  "valid": true,
  "file_schema_version": 1,
  "node_count": 3,
  "edge_count": 2,
  "findings": [],
  "summary": { "errors": 0, "warnings": 0 }
}`;

const BROKEN = `{
  "schema_version": 1,
  "analysis_gui_version": "0.1.0",
  "status": "error",
  "command": "validate",
  "file": "fixtures/broken.pipeline",
  "valid": false,
  "file_schema_version": 1,
  "node_count": 3,
  "edge_count": 6,
  "findings": [
    { "severity": "error", "code": "dangling_edge",
      "message": "Edge 1 references missing target node 'ghost'",
      "edge_index": 1, "node_id": "ghost" },
    { "severity": "warning", "code": "duplicate_edge",
      "message": "Edge 3 duplicates an earlier edge", "edge_index": 3 }
  ],
  "summary": { "errors": 1, "warnings": 1 }
}`;

const UNREADABLE_FILE = `{
  "schema_version": 1,
  "analysis_gui_version": "0.1.0",
  "status": "error",
  "command": "validate",
  "error": {
    "code": "invalid_json",
    "message": "/tmp/bad.pipeline is not valid JSON: Expecting property name enclosed in double quotes: line 1 column 17 (char 16)"
  }
}`;

describe('interpretValidateOutput', () => {
	it('reads a clean report', () => {
		const outcome = interpretValidateOutput(VALID);
		assert.equal(outcome.kind, 'report');
		assert.ok(outcome.kind === 'report');
		assert.equal(outcome.report.valid, true);
		assert.deepEqual(outcome.report.findings, []);
		assert.deepEqual(outcome.report.summary, { errors: 0, warnings: 0 });
	});

	it('reads findings, keeping severity and location fields', () => {
		const outcome = interpretValidateOutput(BROKEN);
		assert.ok(outcome.kind === 'report');
		assert.equal(outcome.report.valid, false);
		assert.equal(outcome.report.findings.length, 2);

		const [dangling, duplicate] = outcome.report.findings;
		assert.equal(dangling?.code, 'dangling_edge');
		assert.equal(dangling?.edge_index, 1);
		assert.equal(dangling?.node_id, 'ghost');
		assert.equal(duplicate?.severity, 'warning');
		assert.equal(duplicate?.node_id, undefined);
	});

	it('distinguishes a CLI error envelope from a report', () => {
		const outcome = interpretValidateOutput(UNREADABLE_FILE);
		assert.ok(outcome.kind === 'cliError');
		assert.equal(outcome.code, 'invalid_json');
		assert.match(outcome.message, /not valid JSON/);
	});

	it('treats anything else as inconclusive rather than as a clean file', () => {
		// The dangerous failure mode is reporting "no problems" when Python
		// never ran, so every unrecognised shape has to be distinguishable.
		for (const stdout of ['', '   ', 'Traceback (most recent call last):', '[]', '{"status":"ok"}']) {
			assert.equal(interpretValidateOutput(stdout).kind, 'unreadable', JSON.stringify(stdout));
		}
	});

	it('survives findings with missing or wrongly typed fields', () => {
		const outcome = interpretValidateOutput(
			'{"valid": false, "findings": [{}, {"severity": 5, "edge_index": "2"}, null, 7]}',
		);
		assert.ok(outcome.kind === 'report');
		assert.equal(outcome.report.findings.length, 2);
		assert.equal(outcome.report.findings[0]?.severity, 'error');
		assert.equal(outcome.report.findings[1]?.edge_index, undefined);
	});
});

describe('charOffsetFromJsonError', () => {
	it('extracts the offset CPython reports', () => {
		assert.equal(
			charOffsetFromJsonError(
				'/tmp/bad.pipeline is not valid JSON: Expecting property name enclosed in double quotes: line 1 column 17 (char 16)',
			),
			16,
		);
	});

	it('returns nothing when the message has no offset', () => {
		assert.equal(charOffsetFromJsonError('Could not read /tmp/x: Permission denied'), undefined);
	});
});

describe('summarize', () => {
	it('counts errors and warnings', () => {
		assert.equal(summarize({ valid: false, findings: [], summary: { errors: 2, warnings: 1 } }), '2 errors and 1 warning');
		assert.equal(summarize({ valid: false, findings: [], summary: { errors: 1, warnings: 0 } }), '1 error');
		assert.equal(summarize({ valid: true, findings: [], summary: { errors: 0, warnings: 0 } }), 'no problems');
	});

	it('falls back to counting findings when there is no summary', () => {
		assert.equal(
			summarize({
				valid: false,
				findings: [
					{ severity: 'error', code: 'a', message: 'a' },
					{ severity: 'warning', code: 'b', message: 'b' },
				],
			}),
			'1 error and 1 warning',
		);
	});
});
