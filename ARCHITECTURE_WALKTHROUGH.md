# Historical walkthrough

This file walks an older PyQt-first layout. For current architecture and
the headless CLI contract, start at **[README.md](README.md)** and
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

# Analysis GUI - File-by-File Architecture Walkthrough

## Overview: What We're Building

A **visual pipeline builder** where users:
1. **Drag nodes** onto a canvas (Load CSV → Normalize → Clustering → Visualize)
2. **Connect them** to form a workflow
3. **Click a button** to generate executable Python code
4. **Save/Load** pipelines as JSON files

Think: Snap (scratch blocks) + data analysis tools = Analysis GUI

---

## Architecture Layers

```
Layer 4: Application Entry Point
           └─ main.py

Layer 3: User Interface (PyQt6)
           ├─ ui/main_window.py
           ├─ ui/canvas/__init__.py
           └─ ui/widgets/__init__.py

Layer 2: Pipeline Logic
           ├─ pipeline/node.py (nodes)
           ├─ pipeline/graph.py (connections)
           └─ pipeline/code_generator.py (Python generation)

Layer 1: Integration & Support
           ├─ models/__init__.py (Claude/GPT)
           ├─ repository/__init__.py (user code)
           └─ utils/ (helpers)
```

---

## FILE 1: `pipeline/node.py` - The Building Blocks

### **What is it?**
Defines all the different types of blocks/nodes users can use. Like LEGO pieces with different shapes.

### **Key Classes**

```python
NodeType(Enum)
├── DATA_LOADER      # Load CSV files
├── PREPROCESSOR     # Transform data
├── ANALYZER         # Analyze data
├── VISUALIZER       # Plot results
├── MODEL_CALL       # Call Claude/GPT
├── CUSTOM_CODE      # User code
└── OUTPUT           # Result node

NodeParameter
├── name: str        # Parameter name
├── param_type: str  # "string", "number", "file", "dropdown"
├── default_value    # Default setting
├── description: str # Explanation
└── options: List    # For dropdowns

Node (Main Class)
├── id: str          # Unique identifier
├── node_type: NodeType
├── label: str       # "Load CSV", "Normalize", etc.
├── parameters: Dict # Configuration options
├── position: tuple  # (x, y) on canvas
└── metadata: Dict   # Extra info
```

### **Factory Methods** (Create different node types)

```python
Node.create_data_loader("csv")
  └─ Creates a CSV loader node with file_path and delimiter parameters

Node.create_preprocessor("normalize")
  ├─ Creates normalize node (minmax, zscore, robust methods)
  ├─ handle_missing (mean, median, drop, forward_fill)
  ├─ feature_select
  └─ split (train/test)

Node.create_analyzer("clustering")
  ├─ clustering (kmeans, dbscan, hierarchical)
  ├─ correlation
  └─ regression

Node.create_model_call("claude")
  └─ Creates Claude/GPT node with prompt parameter

Node.create_custom_code()
  └─ Creates node for user's own functions
```

### **Key Methods**

```python
to_dict()    # Serialize to JSON for saving
from_dict()  # Deserialize from JSON for loading
```

### **Example: Create a Normalize Node**
```python
node = Node.create_preprocessor("normalize")
# node.label = "Normalize"
# node.parameters = {"method": NodeParameter(...)}
# node.node_type = NodeType.PREPROCESSOR
```

---

## FILE 2: `pipeline/graph.py` - The Connection System

### **What is it?**
Manages how nodes connect to each other. Creates the pipeline flow (data flows from one node to the next).

### **Key Class: PipelineGraph**

```python
PipelineGraph
├── nodes: Dict[id → Node]        # All nodes in pipeline
└── edges: List[(source, target)] # Connections between nodes
```

### **Key Methods**

```python
add_node(node) → node_id
  └─ Adds a node to the graph, returns its ID

remove_node(node_id) → bool
  └─ Removes a node and all connected edges

add_edge(source_id, target_id) → bool
  └─ Connects two nodes (source → target)
  └─ Prevents duplicate edges

remove_edge(source_id, target_id) → bool
  └─ Removes a connection

get_predecessors(node_id) → List[node_ids]
  └─ Which nodes feed INTO this node?

get_successors(node_id) → List[node_ids]
  └─ Which nodes does this node feed INTO?

get_topological_order() → List[node_ids]
  └─ Execution order (ensures data flows correctly)
  └─ Used for code generation

is_valid() → (bool, error_msg)
  └─ Checks for cycles (circular dependencies)
  └─ Ensures pipeline can be executed

to_dict() / from_dict()
  └─ Save/load pipelines as JSON
```

### **Example: Build a Pipeline**
```python
graph = PipelineGraph()

# Create nodes
loader = Node.create_data_loader("csv")
normalizer = Node.create_preprocessor("normalize")
clusterer = Node.create_analyzer("clustering")

# Add to graph
id1 = graph.add_node(loader)      # Returns UUID
id2 = graph.add_node(normalizer)
id3 = graph.add_node(clusterer)

# Connect them
graph.add_edge(id1, id2)  # CSV → Normalize
graph.add_edge(id2, id3)  # Normalize → Clustering

# Get execution order
order = graph.get_topological_order()
# Returns [id1, id2, id3]
```

### **Validation**
```python
is_valid, msg = graph.is_valid()

# Valid: Linear pipeline
# Invalid: Cycles (A→B→A), empty pipeline, etc.
```

---

## FILE 3: `pipeline/code_generator.py` - Python Code Generation

### **What is it?**
Converts the visual pipeline into executable Python code.

### **Key Class: CodeGenerator**

```python
CodeGenerator(graph: PipelineGraph)
  └─ Takes a validated graph
  └─ Generates Python code
```

### **Generation Process**

```python
generate() → Python code string
  ├─ Validate pipeline
  ├─ Generate imports (smart - only imports used nodes need)
  │   └─ "import pandas" only if you have CSV loader
  ├─ Get topological order
  ├─ For each node:
  │   ├─ Call _generate_data_loader()
  │   ├─ Call _generate_preprocessor()
  │   ├─ Call _generate_analyzer()
  │   ├─ Call _generate_model_call()
  │   └─ Etc.
  └─ Combine all code + execution guard
```

### **Example Output**

**Visual Pipeline:**
```
Load CSV → Normalize → Clustering
```

**Generated Code:**
```python
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans

# Load CSV data
output_0 = pd.read_csv('data.csv', delimiter=',')

# Preprocessing: Normalize
output_1 = output_0.copy()
output_1 = (output_0 - output_0.min()) / (output_0.max() - output_0.min())

# Analysis: Clustering
kmeans = KMeans(n_clusters=3, random_state=42)
output_2 = kmeans.fit_predict(output_1)
```

---

## FILE 4: `ui/main_window.py` - The Main Application

### **What is it?**
The main PyQt6 window. What users see and interact with.

### **Layout**
```
┌─────────────────────────────────────────────────────────┐
│ File  Pipeline  Help                                    │
├──────────┬──────────────────────┬──────────────────────┤
│ NodePal. │                      │  Properties          │
│ ├ Load   │    CANVAS            │  ├─ ID              │
│ ├ Norm   │   (dragged nodes)    │  ├─ Type            │
│ ├ Clust  │                      │  └─ Parameters      │
│ ├ Viz    │                      │──────────────────────│
│ ├ Claude │                      │  Generated Code:    │
│ └ Custom │                      │  ┌─────────────────┐│
│          │                      │  │ import pandas   ││
└──────────┴──────────────────────┴──────────────────────┘
```

### **Key Methods**

```python
init_ui()                      # Set up the interface
create_menu_bar()              # File, Pipeline, Help menus

# Menu actions
new_pipeline()                 # Clear and start over
save_pipeline()                # Save to .pipeline JSON file
open_pipeline()                # Load from .pipeline JSON file
generate_and_show_code()       # Generate & display Python
validate_pipeline()            # Check for errors

# Event handlers
on_node_palette_item_clicked() # User drags from left panel
on_node_dropped()              # User drops node on canvas
on_node_selected()             # User clicks node on canvas
```

---

## FILE 5: `ui/canvas/__init__.py` - The Visual Canvas

### **What is it?**
The canvas where nodes appear and connections are drawn.

### **Visual Node**
```
┌─────────────────┐
│  Normalize      │  ← Colored by type (green for preprocessor)
│  (draggable)    │  ← Can be selected
└─────────────────┘
```

### **Key Classes**

```python
PipelineNodeGraphic(QGraphicsRectItem)
  ├─ Visual rectangle for a node
  ├─ Colored by node type
  ├─ Draggable
  └─ Selectable

PipelineCanvas(QGraphicsView)
  ├─ Main drawing area
  ├─ Manages all node graphics
  ├─ Handles drag-and-drop
  └─ Draws connections (lines between nodes)
```

### **Colors**

```python
DATA_LOADER   → Blue
PREPROCESSOR  → Green
ANALYZER      → Orange
VISUALIZER    → Red
MODEL_CALL    → Purple
CUSTOM_CODE   → Yellow
```

---

## FILE 6: `ui/widgets/__init__.py` - Sidebar Panels

### **NodePalette (Left Sidebar)**
```
┌─────────────────┐
│ Pipeline Nodes  │
├─────────────────┤
│ ○ Load CSV      │ ← Drag these
│ ○ Normalize     │
│ ○ Handle Missing│
│ ○ Split         │
│ ○ Correlation   │
│ ○ Clustering    │
│ ○ Regression    │
│ ○ Visualization │
│ ○ Call Claude   │
│ ○ Call GPT      │
│ ○ Custom Code   │
└─────────────────┘
```

### **PropertyInspector (Right Sidebar Top)**
```
┌──────────────────────────┐
│ Node Properties          │
├──────────────────────────┤
│ ID: node-abc123          │
│ Type: preprocessor       │
│ Label: Normalize         │
│ ─────────────────────    │
│ Parameters:              │
│ • method: minmax         │
└──────────────────────────┘
```

---

## FILE 7: `models/__init__.py` - AI Model Integration

### **What is it?**
Calls Claude and GPT APIs for analysis nodes.

### **Example: Using in Pipeline**
```python
# User creates "Call Claude" node
# Sets prompt: "Summarize this data"
# Generated code:
from anthropic import Anthropic
client = Anthropic()
response = client.messages.create(
    model='claude-3-sonnet-20240229',
    max_tokens=1024,
    messages=[{'role': 'user', 'content': 'Summarize this data'}]
)
result = response.content[0].text
```

---

## FILE 8: `repository/__init__.py` - User Code Management

### **What is it?**
Stores and manages user's custom code repositories.

### **Example**
```python
# User has a folder with their analysis functions
# ~/.analysis_gui/repositories/
# ├─ my-repo/
# │  ├─ analysis.py (has function: my_analysis())
# │  └─ helpers.py

# User adds this repository to Analysis GUI
# Can then use "Custom Code" nodes calling my_analysis()
```

---

## Data Flow Example

### **User's Perspective**

```
1. Open Analysis GUI
   ↓
2. Drag "Load CSV" to canvas
   ↓
3. Drag "Normalize" to canvas
   ↓
4. Connect them (Load CSV → Normalize)
   ↓
5. Click "Generated Code"
   ↓
6. See Python code in right panel
   ↓
7. Copy code, save as .py, run it!
```

### **Behind the Scenes**

```
Canvas.dropEvent()
  ↓
MainWindow.on_node_dropped()
  ├─ Creates Node
  ├─ Adds to self.pipeline (PipelineGraph)
  └─ Updates canvas graphics
  ↓
User clicks "Generated Code"
  ↓
MainWindow.generate_and_show_code()
  ├─ Calls CodeGenerator(self.pipeline).generate()
  ├─ CodeGenerator validates pipeline
  ├─ Gets topological order
  ├─ Generates code for each node
  ├─ Combines imports + code
  └─ Returns Python string
  ↓
Code displayed in self.code_view
```

---

**Now you understand the entire architecture!** 🎉
