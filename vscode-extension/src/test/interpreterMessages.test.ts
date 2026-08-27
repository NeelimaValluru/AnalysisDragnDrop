/**
 * Interpreter error copy, independent of the vscode API.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
	INSTALL_COMMAND,
	USAGE_EXIT_CODE,
	describeFailure,
	isAnalysisGuiMissing,
	needsInterpreterHelp,
	type CliFailureView,
} from '../interpreterMessages';

function run(partial: Partial<CliFailureView> & Pick<CliFailureView, 'stderr' | 'exitCode'>): CliFailureView {
	return {
		interpreter: { label: '/env/bin/python (selected in the Python extension)' },
		argv: ['-m', 'analysis_gui.cli', 'validate', 'demo.pipeline', '--json'],
		...partial,
	};
}

describe('isAnalysisGuiMissing', () => {
	it('matches CPython ModuleNotFoundError with quotes', () => {
		assert.equal(isAnalysisGuiMissing(run({ exitCode: 1, stderr: "ModuleNotFoundError: No module named 'analysis_gui'\n" })), true);
	});

	it('matches without quotes', () => {
		assert.equal(isAnalysisGuiMissing(run({ exitCode: 1, stderr: 'No module named analysis_gui' })), true);
	});

	it('ignores an unrelated traceback', () => {
		assert.equal(isAnalysisGuiMissing(run({ exitCode: 1, stderr: 'ValueError: cycle detected\n' })), false);
	});
});

describe('needsInterpreterHelp', () => {
	it('is true for a missing package', () => {
		assert.equal(
			needsInterpreterHelp(run({ exitCode: 1, stderr: "No module named 'analysis_gui'" })),
			true,
		);
	});

	it('is true for CLI usage exit 2', () => {
		assert.equal(needsInterpreterHelp(run({ exitCode: USAGE_EXIT_CODE, stderr: 'unrecognised arguments: --nope\n' })), true);
	});

	it('is false for a pipeline that simply failed', () => {
		assert.equal(needsInterpreterHelp(run({ exitCode: 1, stderr: 'error: generated pipeline exited with code 1\n' })), false);
	});
});

describe('describeFailure', () => {
	it('names the interpreter and the install command when the package is missing', () => {
		const message = describeFailure(run({ exitCode: 1, stderr: "No module named 'analysis_gui'" }));
		assert.match(message, /not importable by \/env\/bin\/python/);
		assert.match(message, new RegExp(INSTALL_COMMAND.replaceAll('.', '\\.')));
		assert.match(message, /analysisGui\.pythonPath/);
	});

	it('explains exit code 2 as an interpreter / install problem', () => {
		const message = describeFailure(run({ exitCode: 2, stderr: 'unrecognised arguments: --foo\n' }));
		assert.match(message, /exited with code 2/);
		assert.match(message, /usage error/);
		assert.match(message, /Select Interpreter|pythonPath|pip install/i);
	});

	it('keeps the last stderr line for ordinary failures', () => {
		const message = describeFailure(
			run({ exitCode: 1, stderr: 'Running generated pipeline\nerror: generated pipeline exited with code 1\n' }),
		);
		assert.match(message, /exited with code 1: error: generated pipeline exited with code 1/);
	});
});
