// @ts-check
const esbuild = require('esbuild');
const fs = require('node:fs');

const production = process.argv.includes('--production');
const watch = process.argv.includes('--watch');

/**
 * Prints build results in the format the `connor4312.esbuild-problem-matchers`
 * extension parses, so watch-mode errors land in the Problems panel.
 * @type {import('esbuild').Plugin}
 */
const problemMatcherPlugin = {
	name: 'problem-matcher',
	setup(build) {
		build.onStart(() => {
			console.log('[watch] build started');
		});
		build.onEnd((result) => {
			for (const { text, location } of result.errors) {
				console.error(`✘ [ERROR] ${text}`);
				if (location) {
					console.error(`    ${location.file}:${location.line}:${location.column}:`);
				}
			}
			for (const { text, location } of result.warnings) {
				console.warn(`▲ [WARNING] ${text}`);
				if (location) {
					console.warn(`    ${location.file}:${location.line}:${location.column}:`);
				}
			}
			console.log('[watch] build finished');
		});
	},
};

/**
 * Fails the build if the webview bundle can construct code from strings.
 *
 * The webview runs under `script-src 'nonce-...'` with no `'unsafe-eval'`, so
 * `eval` or `new Function` in the bundle is not a style problem — it is a blank
 * canvas and a console error at runtime. Asserting it here means the CSP cannot
 * be quietly invalidated by a dependency upgrade, which is the only realistic
 * way this breaks.
 * @type {import('esbuild').Plugin}
 */
const noEvalPlugin = {
	name: 'no-eval',
	setup(build) {
		build.onEnd((result) => {
			const outfile = build.initialOptions.outfile;
			if (result.errors.length > 0 || !outfile || !fs.existsSync(outfile)) {
				return;
			}

			const source = fs.readFileSync(outfile, 'utf8');
			// The lookbehind keeps `x.eval(` and `_eval(` out of it; a real
			// indirect call site is always one of these two forms.
			const offenders = [/(?<![.\w$])eval\s*\(/, /new\s+Function\s*\(/].filter((pattern) => pattern.test(source));
			if (offenders.length > 0) {
				console.error(
					`✘ [ERROR] ${outfile} matches ${offenders.map(String).join(' and ')}, which the webview CSP forbids.`,
				);
				process.exitCode = 1;
			}
		});
	},
};

/**
 * One entry per bundle.
 *
 * The extension host and the webview are separate builds on purpose: one is
 * CommonJS on Node with `vscode` external, the other an ES module in a browser
 * with React bundled in. They share source under `src/pipeline/` but nothing
 * about how that source is compiled.
 * @type {import('esbuild').BuildOptions[]}
 */
const targets = [
	{
		entryPoints: ['src/extension.ts'],
		outfile: 'dist/extension.js',
		bundle: true,
		format: 'cjs',
		platform: 'node',
		// Prefer jsonc-parser's ESM build. Its UMD `main` keeps
		// `require('./impl/format')` inside a factory, which esbuild does not
		// rewrite, so the compiled extension then fails to activate with
		// "Cannot find module './impl/format'".
		mainFields: ['module', 'main'],
		// Kept at the Node version shipped by the oldest VS Code we support
		// (engines.vscode ^1.110.0), not the Node used to build.
		target: 'node20',
		external: ['vscode'],
		minify: production,
		sourcemap: !production,
		sourcesContent: false,
		logLevel: 'silent',
		plugins: [problemMatcherPlugin],
	},
	{
		entryPoints: ['src/webview/index.tsx'],
		// The CSS the entry imports lands beside this as `dist/webview.css`.
		outfile: 'dist/webview.js',
		bundle: true,
		format: 'esm',
		platform: 'browser',
		// The Chromium in the Electron shipped with our minimum VS Code.
		target: ['es2022', 'chrome114'],
		jsx: 'automatic',
		// React reads this at module scope. Left undefined it compiles to a
		// reference to `process`, which in a browser is a blank webview.
		define: { 'process.env.NODE_ENV': production ? '"production"' : '"development"' },
		minify: production,
		// Inline rather than a sibling `.js.map`, which would have to be
		// reachable through `localResourceRoots` to be worth anything.
		sourcemap: production ? false : 'inline',
		logLevel: 'silent',
		plugins: [problemMatcherPlugin, noEvalPlugin],
	},
];

async function main() {
	if (watch) {
		const contexts = await Promise.all(targets.map((target) => esbuild.context(target)));
		await Promise.all(contexts.map((context) => context.watch()));
		return;
	}

	await Promise.all(targets.map((target) => esbuild.build(target)));
}

main().catch((error) => {
	console.error(error);
	process.exit(1);
});
