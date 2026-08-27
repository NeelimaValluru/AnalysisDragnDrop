# Getting started

Install and usage live in the **[root README](README.md)**. Short path:

```bash
pip install -e ".[dev]"
analysis-gui-cli describe --json
analysis-gui-cli validate templates/csv_cluster.pipeline
```

Open `vscode-extension/` in VS Code and press **F5** for the canvas. Do not treat the PyQt `analysis-gui` entry point as the primary UI.
