# SpikeInterface on this canvas

SpikeInterface is an **optional extra**: `pip install 'analysis-gui[spike]'`. Generated code imports `analysis_gui.neural.spikeinterface_nodes`, which lazy-imports SI and raises a pip hint if it is missing. Codegen `compile()`s without SI installed.

## What we wrap

| Stage | Node | SI API |
| --- | --- | --- |
| Recording | SI Recording | `read_binary`, `read_nwb_recording`, SpikeGLX, Open Ephys, Intan, Blackrock, Neuralynx, MEArec, BIDS; anything else is `read_<custom_format>` |
| Preprocess | SI Preprocess | bandpass / highpass / notch / CAR / whiten / phase_shift / blank_saturation / **`correct_motion`** |
| Sort | SI Sort | `run_sorter` — **binaries not vendored** |
| Analyzer | SI Analyzer | `create_sorting_analyzer` (not the removed `WaveformExtractor`) |
| Metrics | SI Quality Metrics | `SortingAnalyzer.compute("quality_metrics")` |
| Curate | SI Curate | `select_units` on metric thresholds |
| Export | SI Export | Phy, NWB via neuroconv, **SortingView call** (`plot_sorting_summary(..., backend="sortingview")`), `export_report` |
| Compare | SI Compare | `compare_two_sorters` (in the palette) |

A `.nwb` path or a BIDS root (`dataset_description.json`) is inferred when format is still the default.

## What we do not do

- Embed SortingView / figurl as a VS Code webview.
- Ship Kilosort, MountainSort, or other sorter binaries.
- Replace SpikeInterface-GUI.

Numpy spike PSTH/ISI nodes remain a separate, SI-free path (`Load Spike` → `Spike Statistics`).
