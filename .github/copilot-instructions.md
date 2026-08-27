# Analysis GUI - Visual Pipeline Builder

## Project Overview
A visual, drag-and-drop pipeline builder for data analysis. Users construct data analysis workflows by connecting nodes (data loading, preprocessing, analysis, visualization). The system generates executable Python code, integrates with AI models (Claude, GPT, open weights), and allows users to upload custom code repositories.

## Architecture

### Core Concepts
1. **Node System** - Draggable pipeline components (CSV loader, preprocessing, analysis, custom code)
2. **Pipeline Graph** - Connected nodes that represent data flow
3. **Code Generation** - Converts visual pipeline to executable Python code
4. **Repository Integration** - Users can upload and pull custom functions
5. **Model Integration** - Call Claude/GPT/open weights models for analysis steps
6. **Save/Load** - Persist pipelines as JSON configurations

### Module Structure
- **ui/** - GUI components (canvas, node palette, property inspector)
  - `main_window.py` - Main application with drag-drop interface
  - `canvas/` - PipelineCanvas with visual node rendering
  - `widgets/` - NodePalette and PropertyInspector reusable components
- **pipeline/** - Pipeline graph, nodes, code generation
  - `node.py` - Node definitions and factory methods for all node types
  - `graph.py` - PipelineGraph (DAG) with validation
  - `code_generator.py` - Generates executable Python from pipeline
- **repository/** - User code repository management
- **models/** - AI model integration (Claude, GPT, open weights)
- **preprocessing/** - Built-in preprocessing functions
- **utils/** - Helper functions
