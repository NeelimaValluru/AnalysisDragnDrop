# Analysis GUI — VS Code extension

VS Code front end for [AnalysisGUI](../README.md). This will grow into the primary
UI for building analysis pipelines, replacing the PyQt6 desktop app over time.

**Status: usable.** Validation diagnostics, the DAG canvas, generated-code preview,
Python export, Run, Discover Library Nodes and Find Similar Code are wired through
the CLI. Node-to-source navigation is still a stub — see [What is stubbed](#what-is-stubbed).

## Build and run

Requires Node.js 20.19+ (the version bundled with recent VS Code) and a Python
environment with `analysis_gui` installed (`pip install -e .` from the repo root).

```bash
cd vscode-extension
npm install
npm run compile
```

Then **open the `vscode-extension` folder in VS Code** (not the repo root — the
launch config and tasks live here) and press <kbd>F5</kbd>. That runs the `watch`
task and opens an Extension Development Host with the AnalysisGUI repo root as its
workspace, so any `.pipeline` file in the project is available immediately.

Reload the dev host with <kbd>Cmd/Ctrl</kbd>+<kbd>R</kbd> after changing extension
code. Extension output goes to the **Analysis GUI** channel in the Output panel.

| Script              | What it does                                              |
| ------------------- | --------------------------------------------------------- |
| `npm run compile`   | One-off esbuild bundle to `dist/extension.js`              |
| `npm run watch`     | Incremental rebuilds (the default build task, used by F5)   |
| `npm run typecheck` | `tsc --noEmit` — types only, esbuild never checks types     |
| `npm run lint`      | ESLint with type-aware `typescript-eslint` rules            |
| `npm test`          | Unit tests for the pure logic, on Node's built-in runner    |
| `npm run check:cli` | End-to-end check of the bridge against a real Python CLI    |
| `npm run package`   | typecheck + lint + minified production bundle              |

`npm run package` produces the production bundle, not a `.vsix`. To build an
installable archive once this is worth distributing: `npx @vscode/vsce package`.

### Tests

`npm test` compiles through `tsconfig.test.json` — the one place `noEmit` is
off — and runs `node --test` over the result. Node's built-in runner rather than
Mocha plus `@vscode/test-electron` because everything worth testing so far is
pure: JSON text and a finding in, offsets out. That needs no editor, and an
Electron download in the loop would make the tests something people skip.

The trade-off is that anything touching the `vscode` API is untested. When the
first piece of real editor behaviour needs coverage, add `@vscode/test-cli`
alongside this rather than replacing it.

`npm run check:cli` is the other half: it loads the compiled bridge with a stub
`vscode` module, spawns a real interpreter (`../.venv/bin/python` by default,
`--python <path>` to override) and asserts the payload shapes the extension
depends on. Run it after changing anything in the Python package — a contract
break shows up here as a failed check instead of as an empty Problems panel.

### Why esbuild instead of plain `tsc`

The DAG canvas bundles React and `@xyflow/react` into a webview script, which
needs a bundler. esbuild emits without type checking, so `npm run typecheck` is
the thing that actually validates types — run it in CI, not just `compile`.

## Contribution surface

- **Language** `analysis-pipeline` for `.pipeline` files, alias "Analysis Pipeline",
  with `language-configuration.json` for brackets and auto-closing pairs.
- **Commands**, all under the "Analysis GUI" category: `analysisGui.openCanvas`,
  `analysisGui.refreshNodeKinds`, `analysisGui.previewCode`,
  `analysisGui.exportPython`, `analysisGui.runPipeline`, `analysisGui.validate`,
  `analysisGui.launchGui`, `analysisGui.revealNodeSource`,
  `analysisGui.discoverLibraryNodes`, `analysisGui.findSimilarCode`,
  `analysisGui.newFromTemplate`.
- **Menus**: `openCanvas`, `previewCode` and `runPipeline` in the editor title bar
  (`openCanvas` only when the canvas is not already the active editor), `launchGui`
  and `runPipeline` in the explorer context menu, all gated on
  `resourceExtname == .pipeline`.
- **Settings**: `analysisGui.pythonPath`, `analysisGui.autoPreviewCode`,
  `analysisGui.diagnostics.enabled`. All three are read at the point of use and
  scoped to the resource, so a workspace can override them per folder.
- **Activation**: `onLanguage:analysis-pipeline` and `workspaceContains:**/*.pipeline`.
- **URI scheme** `analysis-pipeline-codegen:`, registered in code rather than in
  the manifest, backing the read-only code preview.
- **Test fixtures** in `fixtures/`: one valid pipeline and one that trips six
  different findings, used by both `npm test` and `npm run check:cli`.

### Syntax highlighting reuses the built-in JSON grammar

`.pipeline` files are JSON, so `syntaxes/analysis-pipeline.tmLanguage.json` is a
three-line grammar that does nothing but `include` the built-in `source.json`.

It does **not** claim `scopeName: "source.json"` itself. VS Code keys grammars by
scope name in a single global registry, so a second extension registering
`source.json` overwrites the built-in JSON extension's grammar file mapping — it
logs `Overwriting grammar scope name to file mapping` and breaks highlighting for
every `.json` file in the window. Including the scope from a distinct scope name
(`source.json.analysis-pipeline`) gets identical tokens with no global side
effects, and because theme scope matching is prefix-based on dot segments, any
JSON-specific theme rules still apply.

### The canvas opens by default

Double-clicking a `.pipeline` file opens the DAG canvas (`analysisGui.pipelineEditor`,
`"priority": "default"`). The raw JSON is still a custom text editor: use
**Reopen Editor With…** → the default text editor (or **Open Pipeline Canvas**
from the title bar to go the other way). **Run Pipeline** posts `runProgress`
to that canvas so node cards show pending / running / ok / error.

`esbuild.js` takes a list of build targets; the webview bundle is a second entry
in that list.

## Python interop

`src/pythonBridge.ts` is the only place the extension starts a Python process.
Nothing in it parses the `.pipeline` schema — it moves arguments in and
stdout/stderr/exit codes out, and callers decide what the JSON means. That is why
the schema work happening in the Python package can't break it.

### Interpreter resolution

In order, first match wins:

1. **`analysisGui.pythonPath`**, expanding `~` and `${workspaceFolder}`. An
   explicit setting wins over everything else: the environment that can import
   `analysis_gui` is often not the one you want selected for editing Python, and
   a setting that silently loses to another extension's state is worse than no
   setting. Empty is the default, which hands control to the next entry.
2. **The `ms-python.python` extension**, via
   `environments.getActiveEnvironmentPath(resource)`, then `resolveEnvironment()`
   to turn an environment folder into an actual executable. This is the
   interpreter shown in the status bar and changed with "Python: Select
   Interpreter". `ms-python.python` is listed in `extensionDependencies`, so it
   is always installed.
3. **`python3`** (`python` on Windows) from `PATH`.

Failing to spawn at all raises `PythonBridgeError` pointing at "Python: Select
Interpreter" or the setting. Exiting nonzero returns a `CliRun`. Validate, Run
and Preview (the explicit command, not auto-refresh) all go through
`PythonBridge.reportFailure` rather than inventing their own dialogs.

When `analysis_gui` is not importable, or the CLI exits 2 (usage error), the
error is actionable:

1. **Select Interpreter** — runs `Python: Select Interpreter`.
2. **Set Python Path** — opens settings at `analysisGui.pythonPath`.
3. **Copy Install Command** — copies `pip install -e .` to the clipboard.
4. **Show Log** — reveals the Analysis GUI output channel.

Automatic diagnostics use the same dialog, but only once per session so a
keystroke storm does not stack notifications.

On activate, if a workspace `.venv/bin/python` (or `.venv/Scripts/python.exe`)
exists and `analysisGui.pythonPath` is empty, the Output channel mentions that
path. It does **not** override the Python extension's selected interpreter.

### CLI contract

The bridge targets `analysis_gui/cli.py`:

```
python -m analysis_gui.cli codegen <file.pipeline>            # Python on stdout
python -m analysis_gui.cli codegen <file.pipeline> -o out.py  # writes the file, JSON receipt
python -m analysis_gui.cli codegen <file.pipeline> --json     # {"code": "...", ...}
python -m analysis_gui.cli validate <file.pipeline> --json
python -m analysis_gui.cli describe --json
python -m analysis_gui.cli discover --json [--workspace dir] [--root dir]
python -m analysis_gui.cli similar "<query>" --json
python -m analysis_gui.cli run <file.pipeline> [--cwd dir]    # JSON receipt; logs on stderr
```

Exit codes are 0 success, 1 failure, 2 usage. JSON goes to stdout and
human-readable messages to stderr, and every payload carries `schema_version`
(an integer) and `analysis_gui_version`. `CliEnvelope` types only those two
fields and leaves the rest open. The one exception to "JSON on stdout" is
`codegen` with neither `-o` nor `--json`, which writes the generated Python
itself so it can be piped straight into a file.

`run` generates the same Python `codegen` would, then executes it in a
subprocess of the same interpreter. The child's working directory is the
pipeline file's folder (or `--cwd`) so relative CSV paths resolve. Matplotlib
is forced to the Agg backend and `plt.show()` writes
`<pipeline-stem>_fig_<n>.png` next to the pipeline instead of opening a window,
so a headless or CI run cannot hang on a display. Unsaved buffers use the same
scratch-file path as diagnostics; `--cwd` stays the original document's
directory.

While `run` is in flight the host posts per-node progress to the canvas:

```
{ "type": "runProgress", "nodeId": "<id>", "state": "pending"|"running"|"ok"|"error" }
```

The same object is the NDJSON line protocol on stderr (or stdout). Snake_case
`run_progress` / `node_id` is accepted so a future Python emitter can match the
rest of the CLI. Today's CLI does not yet stream per-node events, so the host
synthesises pulses in topological order from the open document (all pending,
then running on a timer, then ok/error when the process exits). The first real
progress line cancels the synthesizer. Node cards show running / ok / error;
edges animate while a node is running, unless `prefers-reduced-motion` is set.

A run that never got as far as reading the pipeline — a missing file,
unparseable JSON — returns an envelope with an `error: {code, message}` object
and **no** `findings` array. Treating that as an empty findings list would
report a broken file as clean, so `interpretValidateOutput` keeps the two cases
distinct and has a third for "we do not recognise this at all".

### Unsaved buffers

The CLI reads from disk, but the interesting copy of a file is the one in the
editor. `PipelineSnapshots` mirrors a dirty document to a scratch file under the
system temp directory and points the CLI at that, reusing one path per document
so a fast typist does not fill the directory. Ranges are still computed against
the editor's text, which is the same bytes. A saved document is passed through
untouched, so the common case costs nothing.

### Launching the desktop app

`analysisGui.launchGui` starts the `analysis-gui` console script, looked up next to
the resolved interpreter (`<env>/bin/analysis-gui`, or `Scripts\` on Windows) and
falling back to `PATH`. It runs as a terminal's `shellPath`, which surfaces Qt
startup errors instead of swallowing them and sidesteps shell quoting on paths with
spaces.

It refuses to run when `vscode.env.uiKind` is `Web` or `vscode.env.remoteName` is
set, with a message explaining why: the extension host is not on the machine with
the display, so a Qt window has nowhere to open. When invoked from a `.pipeline`
file (explorer context menu or an open editor) the file's `fsPath` is passed as
the optional positional argument `analysis-gui [PIPELINE]`.

## Validation diagnostics

`validate` findings become `vscode.Diagnostic`s in the Problems panel, with
`source` set to `analysis-gui` and `code` set to the finding's code
(`dangling_edge`, `cycle_detected`, and so on) so they can be filtered.

### Placing a finding in the text

A finding says *what* is wrong in terms of the parsed document — a `node_id`, an
index into `edges` — and knows nothing about where that sits in the file. Turning
one into a range means re-parsing the same text with offsets, which is what
`jsonc-parser` is for: it yields a tree of `{offset, length}` nodes and tolerates
the malformed input we are there to complain about in the first place.

`findingRanges.ts` resolves in this order, most specific first:

1. **`edge_index`** — the element of `edges` at that index, narrowed to the
   `source` or `target` value matching `node_id` when exactly one of them does.
   Edges are checked before nodes because `dangling_edge` carries the id of a
   node that by definition is *not* in the document; looking it up under `nodes`
   could only ever miss. Narrowing deliberately gives up when both endpoints
   match, because a self-loop is about the edge, not one end of it. Legacy
   two-element `[source, target]` edges are handled the same way.
2. **`node_id`** — the key in the `nodes` object. Failing that, a node whose `id`
   property holds the value, which is what a `node_id_mismatch` looks like from
   the other side.
3. **The container the code implicates** — `malformed_nodes` and `empty_pipeline`
   land on the `"nodes"` key, `malformed_edges` on `"edges"`.
4. **The start of the document.** A finding that cannot be placed is still
   reported; dropping it would turn a validation error into silence.

Every span is clamped to the document, so a stale offset can never produce a
range past the end of the text. This is all pure text-in/offsets-out, and it is
where most of `npm test` goes.

### When to spend a Python process

Diagnostics refresh on open, on save, and 500ms after the last keystroke. Two
things keep that from being wasteful:

- A **strict-JSON check runs locally first**. Mid-edit a file is usually not
  valid JSON at all, which can only ever produce a parse error, so `jsonc-parser`
  answers it directly and Python is never started. That is also what keeps a
  half-typed file from producing a wall of findings or leaving a stale set
  behind — it is replaced by exactly one diagnostic on the offending character.
  Comments and trailing commas count as errors here, because the CLI reads these
  files with `json.load`, not with VS Code's JSONC parser.
- A **run whose document has moved on is discarded** rather than written over
  newer results, and the previous run is cancelled when a new one starts.

Measured on this machine (`npm run check:cli`, and by hand at larger sizes), a
full `validate` round trip is **~36ms median** and is dominated by interpreter
startup: 25 nodes 35ms, 100 nodes 36ms, 400 nodes 45ms. Debouncing at 500ms is
comfortable, and the native pre-check means the noisiest case costs nothing at
all. A native TypeScript pre-check for structural errors is the escape hatch if
this ever stops being true; there is no reason to build one now. Note that the
floor is whatever the chosen interpreter costs to start — a conda environment
with a heavy `sitecustomize` will be slower than the numbers above.

`analysisGui.diagnostics.enabled` is read per resource and applied immediately
when toggled: turning it off clears the collection, turning it on revalidates
every open pipeline. It governs the Problems panel, not the command — running
**Validate Pipeline** with the setting off still validates and still reports a
summary, it just does not write anything into a panel you asked to keep clear. Closing a document clears its diagnostics and its scratch
file. If Python itself fails — the package is not importable, say — diagnostics
are cleared rather than invented, and the failure is reported once per session
instead of once per keystroke.

## Code preview

`analysisGui.previewCode` opens a `TextDocumentContentProvider` document on the
`analysis-pipeline-codegen:` scheme, beside the source. A virtual document
rather than a scratch file makes it read-only by construction: there is no path
on disk it could be mistaken for and no way to save over one. Exporting is a
separate, explicit act.

The URI carries the source in its query string and ends in `.py`, which is what
gets it Python highlighting. Content is byte-identical to what an export writes,
so it can be copied or diffed without surprises; a failed `codegen` renders its
stderr as a comment block, because a blank editor reads as "your pipeline
generates nothing".

Saving the pipeline refreshes the preview 400ms later, gated on
`analysisGui.autoPreviewCode`. With that off, the preview only updates when the
command is invoked, which always regenerates.

## Export

`analysisGui.exportPython` offers a save dialog defaulting to the pipeline's own
name with a `.py` extension in the same directory, then runs `codegen -o` and
reports the JSON receipt. Overwrite confirmation is the save dialog's native
behaviour, so reaching the CLI at all means the user already agreed to replace
the file; a write failure comes back as a nonzero exit and is surfaced with the
CLI's own message.

The CLI does the writing rather than the extension so that an export from the
editor and an export from a terminal cannot drift apart.

## Run

`analysisGui.runPipeline` (**Analysis GUI: Run Pipeline**) is on the editor
title bar and the command palette whenever a `.pipeline` file is active. It
calls `analysis-gui-cli run` through the same interpreter resolution as
validate/codegen, streams stderr into the **Analysis GUI: Run** output channel,
posts `runProgress` to any open canvas on that file, and reports success or
failure with a notification. If the run saved figures, the notification offers
to open them. Interpreter and usage-error failures use the same
Select Interpreter / Set Python Path / Copy Install Command dialog as Validate
and Preview.

Dirty buffers are mirrored to a scratch file first, exactly as diagnostics do;
the working directory stays the original file's folder so relative data paths
still resolve.

## Discover and similar code

`analysisGui.discoverLibraryNodes` runs `discover --json` and offers indexed
library chunks in a Quick Pick. Choosing one adds it to the open pipeline (or
copies the kind id if no `.pipeline` file is active).

`analysisGui.findSimilarCode` uses the current text selection, or an input box,
as the query for `similar --json`, then adds the picked hit as a `custom_code`
node the same way.

Both commands share interpreter resolution and `reportFailure` with Validate /
Run / Preview.

## Templates

`analysisGui.newFromTemplate` (**Analysis GUI: New Pipeline from Template**)
looks for `templates/*.pipeline` at the workspace root (the AnalysisGUI repo
when you press F5). Pick one, choose where to save the copy, and it opens on
the canvas. If that folder is missing, the command says so rather than failing
obscurely.

## What is stubbed

| Command                            | Current behaviour                                      | Needs                                 |
| ---------------------------------- | ------------------------------------------------------ | ------------------------------------- |
| `analysisGui.validate`             | Diagnostics plus a summary; interpreter help on failure | —                                    |
| `analysisGui.previewCode`          | Read-only virtual document; interpreter help on failure | —                                    |
| `analysisGui.exportPython`         | Save dialog plus `codegen -o`                          | —                                     |
| `analysisGui.runPipeline`          | CLI `run`, live output, canvas progress, figure prompt | Per-node events from the CLI (optional) |
| `analysisGui.openCanvas`           | Reopens the file with the DAG canvas                   | —                                     |
| `analysisGui.refreshNodeKinds`     | Invalidates the `describe` cache                       | —                                     |
| `analysisGui.discoverLibraryNodes` | Quick Pick of `discover --json` hits                   | —                                     |
| `analysisGui.findSimilarCode`      | Quick Pick of `similar --json` hits                    | —                                     |
| `analysisGui.newFromTemplate`      | Copies `templates/*.pipeline` into the workspace       | Template files at the repo root       |
| `analysisGui.launchGui`            | Opens the desktop app on the current file              | —                                     |
| `analysisGui.revealNodeSource`     | Information message; reads a `nodeId` arg              | Node-to-source mapping from the canvas |

`describe --json` feeds the canvas palette through `DescribeCache`.

Untested by anything automated: every code path that touches the `vscode` API.
The unit tests cover the pure logic (including run-progress parsing and
interpreter error copy) and `npm run check:cli` covers the bridge against a
real interpreter, but nothing has run the extension inside an editor except by
hand.
