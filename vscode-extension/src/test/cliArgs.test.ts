/**
 * Spawn argv for `analysis-gui-cli run`, kept free of `vscode`.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { desktopAppShellArgs, discoverLibraryArgv, runPipelineArgv, similarCodeArgv } from '../cliArgs';

describe('runPipelineArgv', () => {
	it('is `run` plus the file the CLI should read', () => {
		assert.deepEqual(runPipelineArgv('/tmp/demo.pipeline'), ['run', '/tmp/demo.pipeline']);
	});

	it('passes --cwd so relative CSV paths resolve against the original file', () => {
		assert.deepEqual(runPipelineArgv('/tmp/scratch.pipeline', '/project/data'), [
			'run',
			'/tmp/scratch.pipeline',
			'--cwd',
			'/project/data',
		]);
	});
});

describe('desktopAppShellArgs', () => {
	it('passes the pipeline path as the desktop console script argument', () => {
		assert.deepEqual(desktopAppShellArgs('/Users/me/demo.pipeline'), ['/Users/me/demo.pipeline']);
	});

	it('launches with no args when no file is selected', () => {
		assert.deepEqual(desktopAppShellArgs(undefined), []);
		assert.deepEqual(desktopAppShellArgs(), []);
	});
});

describe('discoverLibraryArgv', () => {
	it('is discover --json with no extra roots', () => {
		assert.deepEqual(discoverLibraryArgv(), ['discover', '--json']);
	});

	it('passes workspace and repeated --root', () => {
		assert.deepEqual(discoverLibraryArgv({ workspace: '/proj', roots: ['/lib', '/other'] }), [
			'discover',
			'--json',
			'--workspace',
			'/proj',
			'--root',
			'/lib',
			'--root',
			'/other',
		]);
	});
});

describe('similarCodeArgv', () => {
	it('is similar plus the query and --json', () => {
		assert.deepEqual(similarCodeArgv('bandpass eeg filter'), ['similar', 'bandpass eeg filter', '--json']);
	});

	it('passes --limit when given', () => {
		assert.deepEqual(similarCodeArgv('filter', { limit: 5 }), ['similar', 'filter', '--json', '--limit', '5']);
	});
});
