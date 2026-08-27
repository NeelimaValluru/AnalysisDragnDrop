# Analysis GUI - File-by-File Architecture Guide

## Overview: How This Project Works

Think of it as **3 main layers**:

```
┌─────────────────────────────────────┐
│   UI LAYER (PyQt6)                  │
│   Users see this, interact with it  │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   PIPELINE LAYER                    │
│   Stores and manages workflows      │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   CODE GENERATION LAYER             │
│   Turns pipeline into Python code   │
└─────────────────────────────────────┘
```

---

## LAYER 1: ENTRY POINT

### File: `src/analysis_gui/main.py`
**Purpose:** How the app starts
**What it does:** 
- Creates a PyQt6 application
- Opens the main window
- Runs the event loop

**Key functions:**
- `run_gui()` - Launches the GUI
- `main()` - Console script entry point (called by `analysis-gui` command)

**How to use:**
```bash
analysis-gui  # Calls main.py → main() → run_gui()
```

---

## LAYER 2: USER INTERFACE

### File: `src/analysis_gui/ui/main_window.py`
**Purpose:** The main application window
**What it does:**
- Creates the window layout (3 panels)
- Manages user interactions
- Connects UI to pipeline logic

**Structure:**
```
┌─────────────────────────────────────────┐
│              MainWindow                  │
├─────────────┬─────────────┬──────────────┤
│  Left Panel │ Center      │ Right Panel  │
│             │  Canvas     │              │
│ Node        │             │ Properties   │
│ Palette     │ (where you  │ Inspector    │
│             │  drag nodes)│              │
│             │             │ + Code View  │
└─────────────┴─────────────┴──────────────┘
```

**Key methods:**
- `init_ui()` - Sets up the interface
- `on_node_dropped()` - When user drops a node
- `on_node_selected()` - When user clicks a node
- `generate_and_show_code()` - Generates Python code
- `save_pipeline()` / `open_pipeline()` - File operations

**What to understand:**
1. User drags node from left panel
2. Node drops on canvas
3. Selected node shows properties on right
4. "Generated Code" button creates Python

---

### File: `src/analysis_gui/ui/canvas/__init__.py`
**Purpose:** The drawing area where nodes appear
**What it does:**
- Shows nodes visually on the canvas
- Handles dragging and dropping
- Draws connections between nodes

**Key classes:**
- `PipelineNodeGraphic` - Visual representation of a single node
- `PipelineCanvas` - The canvas they sit on

**What to understand:**
- PyQt6's QGraphicsView/QGraphicsScene for drawing
- Nodes are colored by type (blue for data, green for preprocessing, etc.)
- Canvas accepts drag-drop events

---

### File: `src/analysis_gui/ui/widgets/__init__.py`
**Purpose:** Reusable UI components
**What it does:**
- Creates the node palette (left panel)
- Creates the property inspector (right panel)

**Key classes:**
- `NodePalette` - List of node types you can drag
- `PropertyInspector` - Shows node properties when selected

---

## LAYER 3: PIPELINE LOGIC

### File: `src/analysis_gui/pipeline/node.py`
**Purpose:** Defines what a "node" is
**What it does:**
- Defines the `Node` class (a single block/step in the pipeline)
- Has factory methods to create different node types
- Stores node parameters (settings)

**Key classes:**
- `NodeType` - Enum: DATA_LOADER, PREPROCESSOR, ANALYZER, etc.
- `NodeParameter` - A single parameter for a node (name, type, default value)
- `Node` - Represents one block in the pipeline

**Factory methods (create different node types):**
```python
Node.create_data_loader("csv")              # CSV loading node
Node.create_preprocessor("normalize")       # Normalization node
Node.create_analyzer("clustering")          # Clustering node
Node.create_model_call("claude")            # Claude API call node
Node.create_custom_code()                   # Custom function node
```

**What to understand:**
- Each node type has different parameters
- Parameters are stored as `NodeParameter` objects
- Factory methods make it easy to create nodes
- Each node has an ID, position on canvas, and metadata

**Example:**
```python
# Create a normalize node
node = Node.create_preprocessor("normalize")
# It comes with parameters like:
# - method: "minmax" (or "zscore", "robust")
```

---

### File: `src/analysis_gui/pipeline/graph.py`
**Purpose:** Connects nodes together into a pipeline
**What it does:**
- Manages a collection of nodes
- Manages connections (edges) between nodes
- Validates the pipeline (no cycles)
- Finds execution order

**Key class:**
- `PipelineGraph` - The entire pipeline as a graph

**Key methods:**
- `add_node()` / `remove_node()` - Manage nodes
- `add_edge()` / `remove_edge()` - Connect nodes
- `get_predecessors()` / `get_successors()` - Find connected nodes
- `get_topological_order()` - Figure out execution order
- `is_valid()` - Check for errors (cycles)
- `to_dict()` / `from_dict()` - Save/load pipelines

**What to understand:**
- This is a **Directed Acyclic Graph (DAG)**
- Data flows from one node to the next
- Pipeline validates: no loops allowed
- Order matters: must execute in correct sequence

**Example:**
```
Load CSV → Normalize → Clustering → Visualization

Graph has:
- 4 nodes
- 3 edges: (Load→Normalize), (Normalize→Clustering), (Clustering→Visualization)
- Topological order: [Load, Normalize, Clustering, Visualization]
```

---

### File: `src/analysis_gui/pipeline/code_generator.py`
**Purpose:** Converts the visual pipeline into executable Python code
**What it does:**
- Takes a pipeline graph
- Generates Python code line-by-line
- Creates imports, variable assignments, function calls

**Key class:**
- `CodeGenerator` - Takes a PipelineGraph and generates Python

**Key methods:**
- `generate()` - Main method, returns Python code as string
- `_generate_imports()` - Creates import statements
- `_generate_node_code()` - Code for each node type
- `_generate_data_loader()` - Specific: CSV loading code
- `_generate_preprocessor()` - Specific: preprocessing code
- etc.

**What to understand:**
- For each node type, there's a method that generates its code
- Imports are determined by which nodes are used
- Output is ready-to-run Python

**Example output:**
```python
import pandas as pd
from sklearn.preprocessing import StandardScaler

# Load CSV data
output_0 = pd.read_csv('data.csv')

# Preprocessing: Normalize
output_1 = (output_0 - output_0.min()) / (output_0.max() - output_0.min())

# Analysis: Clustering
kmeans = KMeans(n_clusters=3, random_state=42)
output_2 = kmeans.fit_predict(output_1)
```

---

## LAYER 4: INTEGRATION FEATURES

### File: `src/analysis_gui/repository/__init__.py`
**Purpose:** Manage user's custom code
**What it does:**
- Stores references to user repositories
- Manages metadata about repositories
- Placeholder for scanning repositories for functions

**Key classes:**
- `Repository` - Represents one user code repository
- `RepositoryManager` - Manages all repositories

**What to understand:**
- Users can upload code they've written
- Pipeline can reference functions from those repositories
- Currently saves/loads metadata, ready for expansion

---

### File: `src/analysis_gui/models/__init__.py`
**Purpose:** Integration with AI APIs
**What it does:**
- Provides clients for Claude and GPT
- Manages configuration
- Handles API calls

**Key classes:**
- `ClaudeClient` - Talks to Claude API
- `GPTClient` - Talks to GPT API
- `ModelIntegration` - Manages both

**What to understand:**
- When you add a "Call Claude" node, this handles it
- Requires API keys (environment variables)
- Easy to add more providers

---

### File: `src/analysis_gui/preprocessing/__init__.py` (empty)
**Purpose:** Placeholder for built-in preprocessing functions
**What to understand:**
- Future: put reusable preprocessing logic here
- Currently empty, extensible

---

### File: `src/analysis_gui/neural/__init__.py` & `analyzer.py`
**Purpose:** Neural network-specific analysis (extensible)
**What to understand:**
- Placeholder for neural network features
- Could add model loading, layer visualization, etc.
- Keeps code organized

---

## LAYER 5: UTILITIES

### File: `src/analysis_gui/utils/data_loader.py`
**Purpose:** Utility functions for loading data
**What it does:**
- Placeholder methods for CSV, NumPy, JSON loading
- Could be expanded for more formats

---

## LAYER 6: TESTING

### File: `tests/test_pipeline_graph.py`
**Purpose:** Tests for the pipeline system
**What it does:**
- Tests node creation
- Tests edge creation/removal
- Tests graph validation
- Tests serialization

**Key test classes:**
- `TestPipelineGraph` - Tests for graph logic

**How to run:**
```bash
pytest tests/
```

---

## HOW IT ALL WORKS TOGETHER

### Flow: User builds a pipeline

```
1. USER LAUNCHES APP
   └─> main.py → run_gui() → MainWindow opens

2. USER DRAGS A NODE
   └─> NodePalette → Canvas (drag-drop event)
   └─> main_window.py on_node_dropped()
   └─> Creates Node via factory method
   └─> Adds to PipelineGraph
   └─> Draws on Canvas

3. USER CLICKS GENERATED CODE
   └─> main_window.py generate_and_show_code()
   └─> Creates CodeGenerator(pipeline)
   └─> CodeGenerator.generate()
   └─> Loops through each node in topological order
   └─> Generates Python code
   └─> Displays in code view

4. USER SAVES PIPELINE
   └─> main_window.py save_pipeline()
   └─> Calls pipeline.to_dict()
   └─> Saves JSON file with all nodes/edges

5. USER OPENS PIPELINE
   └─> main_window.py open_pipeline()
   └─> Reads JSON file
   └─> Creates PipelineGraph from dict
   └─> Redraws canvas with all nodes
```

---

## KEY CONCEPTS EXPLAINED

### Nodes
- A "block" in your pipeline
- Has type (DATA_LOADER, PREPROCESSOR, etc.)
- Has parameters (settings)
- Has position on canvas

### Edges
- Connection between two nodes
- Shows data flowing from one node to next
- Represented as: (source_node_id, target_node_id)

### Graph
- Collection of all nodes and edges
- Validates that there are no cycles
- Can calculate execution order

### Code Generation
- For each node, generate the Python code
- Put outputs in variables: output_0, output_1, etc.
- Chain them together in execution order

---

## TO UNDERSTAND THE PROJECT

**Start with these questions:**

1. **How do I launch it?**
   - Answer: `analysis-gui` (from main.py)

2. **Where do I click?**
   - Answer: main_window.py (the UI)

3. **What happens when I drag a node?**
   - Answer: node.py creates it, graph.py stores it, canvas/__init__.py draws it

4. **How does it know which Python code to generate?**
   - Answer: code_generator.py has a method for each node type

5. **How do I save my work?**
   - Answer: graph.py has to_dict(), main_window.py saves it as JSON

---

## NEXT: Read each file in this order

1. **main.py** - Entry point (10 lines to understand)
2. **ui/main_window.py** - UI layout and interactions
3. **pipeline/node.py** - What a node is
4. **pipeline/graph.py** - How nodes connect
5. **pipeline/code_generator.py** - How code is generated
6. **ui/canvas/__init__.py** - Visual representation
7. **models/__init__.py** - AI integration

---

**Ready to dive in?** Each file is well-commented. Start with main.py! 🚀
