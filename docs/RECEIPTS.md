# Run receipts

`analysis-gui-cli run` always writes a JSON receipt. Default path: `<pipeline-stem>.run.json` next to the `.pipeline` file. Override with `--receipt`. The same object is printed on stdout.

This is **not** a new `.pipeline` schema version. New keys are optional; readers should use `.get()`. Full field list: module docstring of `analysis_gui.pipeline.receipt`.

## Always present

| Key | Meaning |
| --- | --- |
| `schema_version` | Pipeline document schema (currently `1`) |
| `analysis_gui_version` | Installed package version |
| `receipt_schema_version` | Receipt shape (currently `1`; bump only on breaking receipt changes) |
| `graph_hash` | SHA-256 of canonical pipeline JSON |
| `generated_code_hash` | SHA-256 of the generated Python |
| `input_files` | `{uri, resolved_path, sha256?}` for loader `file_path`s that resolve to a readable **file** (directories are listed without a hash) |
| `git_commit` | `git rev-parse HEAD` from the pipeline directory, or `null` |
| `model_summaries` | Per `model_call` node: provider, model, truncated prompt (never API keys) |
| `saved_figures` | PNGs from the Agg `plt.show` wrapper |
| `output_paths` | Figures plus the receipt path |
| `started_at` / `finished_at` | UTC ISO-8601 |
| `interpreter` / `interpreter_version` | Child Python |
| `environment` | **Actual** runtime: `python`, `analysis_gui`, `extras` availability |

## Optional pin on the pipeline

```json
{
  "version": 1,
  "environment": {
    "python": "3.11",
    "analysis_gui": "0.1.0",
    "extras": ["spike", "eeg"],
    "strict": false
  },
  "nodes": {},
  "edges": []
}
```

`requires` is an alias of `environment`. Absent = no pin (old files keep working). `run` **warns** on mismatch; `--strict-env` or `"strict": true` / `"on_mismatch": "error"` fails before the child starts.
