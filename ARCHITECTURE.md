# Architecture Overview

The **primary UI is the VS Code extension** (`vscode-extension/`). The PyQt6
package under `analysis_gui.ui` is legacy. The engine (`analysis_gui.pipeline`,
`analysis_gui.cli`) must stay headless: no PyQt6, no MNE/SpikeInterface at
import time.

See the **[README](README.md)** for install, CLI, receipts, and extras.

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Analysis GUI Application                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                    ▼         ▼         ▼
            ┌──────────┐ ┌──────────┐ ┌──────────┐
            │   UI     │ │ Pipeline │ │Repository│
            │ Package  │ │  System  │ │ Manager  │
            └──────────┘ └──────────┘ └──────────┘
                 │             │            │
                 ▼             ▼            ▼
        ┌─────────────────┐ ┌──────────┐ ┌──────────┐
        │ PyQt6 Canvas    │ │  Node    │ │Repository│
        │ & Widgets       │ │ Factories│ │ Storage  │
        └─────────────────┘ └──────────┘ └──────────┘
                 │             │            │
                 └─────────────┼────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │  Pipeline Graph      │
                    │ (DAG Structure)      │
                    │                      │
                    │ • Nodes              │
                    │ • Edges              │
                    │ • Validation         │
                    │ • Serialization      │
                    └──────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
            ┌──────────────────┐  ┌──────────────────┐
            │  Code Generator  │  │ Model Integration│
            │                  │  │                  │
            │ • Imports        │  │ • Claude Client  │
            │ • Node Code      │  │ • GPT Client     │
            │ • Execution Flow │  │ • Open Weights   │
            └──────────────────┘  └──────────────────┘
                    │                     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │ Executable Python    │
                    │ Code Output          │
                    └──────────────────────┘
```

## Module Dependencies

```
main.py (Entry Point)
    │
    ├── ui/main_window.py
    │   ├── ui/canvas/__init__.py (PipelineCanvas)
    │   ├── ui/widgets/__init__.py (NodePalette, PropertyInspector)
    │   ├── pipeline/__init__.py
    │   │   ├── node.py (Node, NodeType, NodeParameter)
    │   │   ├── graph.py (PipelineGraph)
    │   │   └── code_generator.py (CodeGenerator)
    │   ├── repository/__init__.py (RepositoryManager)
    │   └── models/__init__.py (ModelIntegration)
```

## Node Type Hierarchy

```
┌────────────────────────────────────────────────────────┐
│                    Node (Base Class)                   │
│                                                        │
│ • id: str (unique identifier)                         │
│ • node_type: NodeType (enum)                          │
│ • label: str                                          │
│ • parameters: Dict[str, NodeParameter]                │
│ • position: tuple (x, y for canvas)                   │
│ • metadata: Dict[str, Any]                            │
└────────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
    ┌───▼───┐       ┌────▼─────┐    ┌────▼──────┐
    │DATA_  │       │PREPROCESS │    │ ANALYZER  │
    │LOADER │       │ OR        │    │           │
    └───────┘       └──────────┘    └───────────┘
        │                │                │
    CSV Loader      • Normalize        • Correlation
                    • Handle Missing   • Clustering
                    • Feature Select   • Regression
                    • Train/Test Split
    
    ┌──────────┐    ┌────────────┐    ┌──────────┐
    │VISUALIZER│    │MODEL_CALL  │    │CUSTOM_   │
    │          │    │            │    │CODE      │
    └──────────┘    └────────────┘    └──────────┘
                    • Claude
                    • GPT
                    • Open Weights
```

## Pipeline Execution Flow

```
User builds pipeline visually on canvas
            │
            ▼
User clicks "Generate Code"
            │
            ▼
MainWindow.generate_and_show_code()
            │
            ▼
CodeGenerator.generate()
            │
            ├─► Validate pipeline (is_valid)
            │
            ├─► Get topological order
            │
            ├─► For each node in order:
            │   ├─► Determine node type
            │   ├─► Call appropriate _generate_*() method
            │   ├─► Generate node-specific Python code
            │
            ├─► Combine all code
            │
            ▼
Return complete Python script to code view
            │
            ▼
User can copy/save/execute the code
```

## State Management

```
MainWindow
    │
    ├── pipeline: PipelineGraph
    │   ├── nodes: Dict[str, Node]
    │   └── edges: List[Tuple[str, str]]
    │
    ├── selected_node: Optional[Node]
    │
    ├── canvas: PipelineCanvas
    │   ├── nodes: Dict[str, PipelineNodeGraphic]
    │   └── edges: List[Tuple]
    │
    ├── node_palette: NodePalette
    │
    ├── property_inspector: PropertyInspector
    │
    └── code_view: QPlainTextEdit
```

## Data Persistence

```
Pipeline File (.pipeline)
    │
    ├── JSON Format
    │   │
    │   ├── "nodes": {
    │   │   "node-id": {
    │   │       "id": str,
    │   │       "node_type": str,
    │   │       "label": str,
    │   │       "description": str,
    │   │       "parameters": {...},
    │   │       "position": [x, y],
    │   │       "metadata": {...}
    │   │   }
    │   │}
    │   │
    │   └── "edges": [
    │       [source_id, target_id],
    │       ...
    │   ]
```

## Configuration Files

```
Analysis GUI Config Location: ~/.analysis_gui/

    ├── repositories.json
    │   └── User code repository metadata
    │
    └── pipelines/
        └── Saved pipeline files (.pipeline)
```

## Extensibility Points

```
1. Add New Node Type
   └─ Edit: pipeline/node.py
      └─ Add factory method to Node class

2. Add New Preprocessor
   └─ Edit: pipeline/node.py
      └─ Add to create_preprocessor() method
      └─ Add code generation in code_generator.py

3. Add New Analyzer
   └─ Similar to preprocessor

4. Add Data Format Support
   └─ Edit: pipeline/node.py
      └─ Extend create_data_loader()

5. Add AI Model Provider
   └─ Edit: models/__init__.py
      └─ Add new client class
      └─ Update ModelIntegration.call_model()

6. Customize UI
   └─ Edit: ui/main_window.py
      └─ Add new widgets or dialogs
      └─ Modify layout
```

This architecture ensures:
✅ Separation of concerns (UI, logic, data)
✅ Easy extensibility (add nodes/models/formats)
✅ Testability (pipeline logic independent of UI)
✅ Scalability (supports complex pipelines)
✅ Persistence (save/load pipelines)
