/**
 * End-to-end check of the Python bridge against a real `analysis_gui.cli`.
 *
 * The unit tests cover the pure logic with recorded payloads, which is exactly
 * the thing that goes stale when the Python side changes. This script closes
 * that gap by loading the compiled bridge, spawning a real interpreter, and
 * asserting the payload shapes the extension actually depends on — the parts
 * of the contract whose breakage would show up as an empty Problems panel.
 *
 * There is no Extension Development Host involved: `vscode` is replaced with
 * the handful of API surfaces the bridge touches. Anything beyond interpreter
 * resolution and process I/O is out of scope here and needs a real host.
 *
 *   node scripts/cli-contract-check.mjs [--python <interpreter>]
 *
 * Defaults to the repository's `.venv`. Requires `npm test` (or any build that
 * populates `out/`) to have run first.
 */

import { createRequire } from 'node:module';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const extensionRoot = path.resolve(here, '..');
const repoRoot = path.resolve(extensionRoot, '..');
const fixtures = path.join(extensionRoot, 'fixtures');

const pythonFlag = process.argv.indexOf('--python');
const interpreter =
	pythonFlag === -1
		? path.join(repoRoot, '.venv', 'bin', 'python')
		: path.resolve(process.argv[pythonFlag + 1] ?? '');

// --- the slice of `vscode` the bridge touches ------------------------------

const uri = (fsPath) => ({
	scheme: 'file',
	path: fsPath,
	fsPath,
	toString: () => `file://${fsPath}`,
});

const vscodeStub = {
	Uri: { file: uri },
	UIKind: { Desktop: 2, Web: 1 },
	env: { uiKind: 2, remoteName: undefined, clipboard: { writeText: async () => {} } },
	extensions: { getExtension: () => undefined },
	window: { showErrorMessage: async () => undefined },
	workspace: {
		workspaceFolders: [{ uri: uri(repoRoot) }],
		getWorkspaceFolder: () => ({ uri: uri(repoRoot) }),
		getConfiguration: () => ({
			get: (key, fallback) => (key === 'pythonPath' ? interpreter : fallback),
		}),
		fs: { stat: async () => ({}) },
	},
};

const Module = require('node:module');
const loadModule = Module._load;
Module._load = function (request, ...rest) {
	return request === 'vscode' ? vscodeStub : loadModule.call(this, request, ...rest);
};

const outDir = path.join(extensionRoot, 'out');
if (!existsSync(path.join(outDir, 'pythonBridge.js'))) {
	console.error('out/ is missing. Run `npm test` (or `tsc -p tsconfig.test.json`) first.');
	process.exit(1);
}

const { PythonBridge, describeFailure } = require(path.join(outDir, 'pythonBridge.js'));
const { interpretValidateOutput, charOffsetFromJsonError } = require(path.join(outDir, 'validation.js'));
const { createFindingLocator } = require(path.join(outDir, 'findingRanges.js'));

// --- harness ---------------------------------------------------------------

const silent = { info() {}, debug() {}, warn() {}, error() {}, show() {} };
const bridge = new PythonBridge(silent);

let failures = 0;
const check = (label, condition, detail) => {
	if (condition) {
		console.log(`  ok    ${label}`);
	} else {
		failures += 1;
		console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ''}`);
	}
};

const scratch = mkdtempSync(path.join(tmpdir(), 'analysis-gui-contract-'));
const valid = uri(path.join(fixtures, 'valid.pipeline'));
const broken = uri(path.join(fixtures, 'broken.pipeline'));

try {
	console.log(`interpreter: ${interpreter}\n`);

	const resolved = await bridge.resolveInterpreter(valid);
	console.log('interpreter resolution');
	check('the explicit setting wins over PATH', resolved.origin === 'setting', resolved.origin);

	console.log('\ndescribe --json');
	const described = await bridge.describe(valid);
	const describePayload = bridge.parseJson(described) ?? {};
	check('exits 0', described.ok, `exit ${described.exitCode}`);
	check('schema_version is a number', typeof describePayload.schema_version === 'number');
	check('analysis_gui_version is a string', typeof describePayload.analysis_gui_version === 'string');
	check('node_types is a nonempty array', Array.isArray(describePayload.node_types) && describePayload.node_types.length > 0);

	console.log('\nvalidate a valid pipeline');
	const validRun = await bridge.validate(valid);
	const validOutcome = interpretValidateOutput(validRun.stdout);
	check('exits 0', validRun.ok, `exit ${validRun.exitCode}`);
	check('parses as a report', validOutcome.kind === 'report', validOutcome.kind);
	check('reports valid with no findings', validOutcome.kind === 'report' && validOutcome.report.valid && validOutcome.report.findings.length === 0);

	console.log('\nvalidate a broken pipeline');
	const brokenRun = await bridge.validate(broken);
	const brokenOutcome = interpretValidateOutput(brokenRun.stdout);
	check('exits 1', brokenRun.exitCode === 1, `exit ${brokenRun.exitCode}`);
	check('still emits JSON on stdout', brokenOutcome.kind === 'report', brokenOutcome.kind);
	check('writes human-readable errors to stderr', brokenRun.stderr.includes('error:'));

	if (brokenOutcome.kind === 'report') {
		const text = readFileSync(broken.fsPath, 'utf8');
		const locate = createFindingLocator(text);
		console.log('\n  finding -> source range');
		for (const finding of brokenOutcome.report.findings) {
			const span = locate(finding);
			const line = text.slice(0, span.offset).split('\n').length;
			const snippet = text.slice(span.offset, span.offset + span.length).replace(/\s+/g, ' ');
			const shown = snippet.length > 58 ? `${snippet.slice(0, 55)}...` : snippet;
			console.log(`    line ${String(line).padStart(2)}  ${finding.code.padEnd(18)} ${shown}`);
			check(`  ${finding.code} maps to a nonempty span`, span.length > 0);
		}
	}

	console.log('\ncodegen to stdout');
	const codegenRun = await bridge.codegen(valid);
	check('exits 0', codegenRun.ok, `exit ${codegenRun.exitCode}`);
	check('writes Python, not JSON, to stdout', codegenRun.stdout.includes('import pandas as pd') && !codegenRun.stdout.trimStart().startsWith('{'));

	console.log('\ncodegen -o');
	const target = uri(path.join(scratch, 'exported.py'));
	const exportRun = await bridge.codegen(valid, { outFile: target });
	const receipt = bridge.parseJson(exportRun) ?? {};
	check('exits 0', exportRun.ok, `exit ${exportRun.exitCode}`);
	check('writes the file', existsSync(target.fsPath));
	check('returns a JSON receipt naming output_path', receipt.output_path === target.fsPath, String(receipt.output_path));
	check('receipt carries line_count', typeof receipt.line_count === 'number');

	console.log('\nrun a self-contained pipeline');
	const runDir = path.join(scratch, 'run');
	mkdirSync(runDir);
	writeFileSync(path.join(runDir, 'data.csv'), 'a,b\n1,2\n3,4\n');
	const runPipeline = {
		version: 1,
		nodes: {
			loader: {
				id: 'loader',
				node_type: 'data_loader',
				label: 'Load CSV',
				description: '',
				parameters: {
					file_path: {
						name: 'file_path',
						param_type: 'file',
						default_value: null,
						value: 'data.csv',
						description: '',
						options: [],
					},
				},
				position: [0, 0],
				metadata: { file_format: 'csv' },
			},
		},
		edges: [],
	};
	const runPath = path.join(runDir, 'run.pipeline');
	writeFileSync(runPath, JSON.stringify(runPipeline));
	const executed = await bridge.runPipeline(uri(runPath));
	const runReceipt = bridge.parseJson(executed) ?? {};
	check('exits 0', executed.ok, `exit ${executed.exitCode}\n${executed.stderr}`);
	check('receipt command is run', runReceipt.command === 'run');
	check('child exit_code is 0', runReceipt.exit_code === 0);
	check('streams human logs on stderr', executed.stderr.includes('Running generated pipeline'));

	console.log('\ncodegen failure');
	const failedCodegen = await bridge.codegen(broken);
	check('exits nonzero', !failedCodegen.ok, `exit ${failedCodegen.exitCode}`);
	check('explains itself on stderr for the preview', failedCodegen.stderr.trim().length > 0, describeFailure(failedCodegen));

	console.log('\nvalidate unparseable JSON');
	const badPath = path.join(scratch, 'bad.pipeline');
	writeFileSync(badPath, '{ "version": 1, ');
	const badRun = await bridge.validate(uri(badPath));
	const badOutcome = interpretValidateOutput(badRun.stdout);
	check('exits 1', badRun.exitCode === 1, `exit ${badRun.exitCode}`);
	check('is an error envelope, not a findings report', badOutcome.kind === 'cliError', badOutcome.kind);
	check('uses the invalid_json code', badOutcome.kind === 'cliError' && badOutcome.code === 'invalid_json');
	check(
		'carries a character offset we can place',
		badOutcome.kind === 'cliError' && charOffsetFromJsonError(badOutcome.message) !== undefined,
	);

	console.log('\nlatency (what the change debounce has to cover)');
	const samples = [];
	for (let i = 0; i < 10; i += 1) {
		samples.push((await bridge.validate(broken)).durationMs);
	}
	samples.sort((a, b) => a - b);
	const mean = Math.round(samples.reduce((sum, value) => sum + value, 0) / samples.length);
	console.log(`    validate x${samples.length}: min ${samples[0]}ms  median ${samples[5]}ms  max ${samples.at(-1)}ms  mean ${mean}ms`);
	check('a validate round trip stays under the 500ms debounce', mean < 500, `${mean}ms`);
} finally {
	rmSync(scratch, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} check(s) failed.` : '\nAll contract checks passed.');
process.exit(failures ? 1 : 0);
