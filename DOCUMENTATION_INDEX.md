# Analysis GUI - Complete Documentation Index

Welcome to Analysis GUI! This is your guide to understanding and using the visual pipeline builder.

## 📚 Documentation Files

### Getting Started
- **[QUICKSTART.md](QUICKSTART.md)** - 5-minute getting started guide
  - Installation steps
  - Launching the application
  - Building your first pipeline
  - Common workflows

### Project Overview
- **[README.md](README.md)** - Comprehensive feature documentation
  - Feature descriptions
  - Installation instructions
  - Architecture overview
  - Configuration guide
  - Development guidelines

- **[PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)** - Summary
  - Technology stack
  - Installation & usage
  - Customization points

### Technical Details
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Deep dive into system design
  - Data flow diagrams
  - Module dependencies
  - Node type hierarchy
  - State management
  - Extensibility points

- **[IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)** - Install, test, and extend
  - Installation & usage
  - Testing & code quality
  - Extensibility

## 🚀 Quick Start

```bash
# 1. Install the package
cd /Users/neelimavalluru/Desktop/AnalysisGUI
pip install -e ".[dev]"

# 2. Launch the application
analysis-gui

# 3. Run tests
pytest tests/

# 4. Format code
black src/ tests/
```

## 📁 Project Structure

```
AnalysisGUI/
├── src/analysis_gui/          # Main application code
│   ├── pipeline/              # Core pipeline logic
│   ├── ui/                    # User interface
│   ├── repository/            # User code management
│   ├── models/                # AI model integration
│   └── ...
├── tests/                     # Unit tests
├── docs/                      # Documentation (extensible)
├── setup.py & pyproject.toml  # Package configuration
└── README.md, etc.            # Documentation files
```

## 🎯 Key Features

### Visual Pipeline Builder
- Drag-and-drop interface
- 11+ node types
- Real-time property inspection
- Canvas for visual workflow construction

### Automatic Code Generation
- Generates executable Python code
- Smart imports
- Parameter substitution
- Ready-to-run scripts

### Data Analysis Nodes
- **Data Loading**: CSV (extensible to other formats)
- **Preprocessing**: Normalize, handle missing values, feature selection, train/test split
- **Analysis**: Correlation, clustering, regression
- **Visualization**: Plot and display results
- **AI Models**: Claude, GPT integration
- **Custom Code**: Use your own functions

### Workflow Persistence
- Save pipelines as `.pipeline` files (JSON format)
- Load and reconstruct pipelines
- Share with team members

### Repository Integration
- Upload custom code repositories
- Integrate functions into pipelines
- Version control for custom code

## 🔧 Development

### Adding a New Node Type

1. **Define the node** in `src/analysis_gui/pipeline/node.py`:
   ```python
   @classmethod
   def create_my_node(cls):
       return cls(
           id="",
           node_type=NodeType.ANALYZER,
           label="My Node",
           # ... parameters
       )
   ```

2. **Add to UI** in `src/analysis_gui/ui/main_window.py`:
   ```python
   elif node_type == "my_node_type":
       return Node.create_my_node()
   ```

3. **Implement code generation** in `src/analysis_gui/pipeline/code_generator.py`:
   ```python
   def _generate_my_node(self, node, var_name, pred_var):
       # Generate Python code for this node
   ```

### Adding a New Preprocessor
1. Add to `create_preprocessor()` method
2. Add `_generate_preprocessor()` logic for the type

### Adding a New Data Format
1. Extend `create_data_loader()` method
2. Add format-specific code generation

## 📊 Architecture Overview

```
User Interface (PyQt6)
    ↓
Pipeline Canvas & Nodes
    ↓
Pipeline Graph (DAG)
    ↓
Code Generator
    ↓
Python Code Output
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=src tests/

# Run specific test file
pytest tests/test_pipeline_graph.py

# Run specific test
pytest tests/test_pipeline_graph.py::TestPipelineGraph::test_add_node
```

## 📝 Code Quality

```bash
# Format code
black src/ tests/

# Check style
flake8 src/ tests/

# Type checking
mypy src/

# All checks
black src/ tests/ && flake8 src/ tests/ && mypy src/
```

## 💾 Pipeline File Format

Pipelines are saved as JSON:

```json
{
  "nodes": {
    "node-123": {
      "id": "node-123",
      "node_type": "data_loader",
      "label": "Load CSV",
      "parameters": {...},
      "position": [100, 100],
      "metadata": {...}
    }
  },
  "edges": [["node-123", "node-456"]]
}
```

## 🔗 API Keys

For AI model integration, set environment variables:

```bash
export ANTHROPIC_API_KEY="your-claude-key"
export OPENAI_API_KEY="your-openai-key"
```

Or configure in code:

```python
from analysis_gui.models import ModelIntegration

integration = ModelIntegration()
integration.configure_claude(api_key="...")
integration.configure_gpt(api_key="...")
```

## 🎓 Example Workflows

### Basic Data Analysis
```
Load CSV → Normalize → Clustering → Visualization
```

### With AI Assistance
```
Load CSV → Preprocess → Analysis → Claude (interpret) → Visualization
```

### Custom Function
```
Load CSV → Custom Code (my_analysis) → Visualization
```

## 📦 Package Information

- **Name**: analysis-gui
- **Version**: 0.1.0
- **License**: MIT
- **Python**: 3.8+
- **Main Dependencies**: PyQt6, pandas, numpy, scikit-learn
- **Optional**: anthropic, openai

## 🚀 Distribution

The package is ready for:
- Local installation: `pip install -e .`
- PyPI publication
- Docker containerization
- Team sharing

## 📞 Help & Support

- Check **QUICKSTART.md** for common tasks
- See **ARCHITECTURE.md** for design questions
- Review code comments for implementation details
- Run tests to understand expected behavior

## 🤝 Contributing

To extend Analysis GUI:

1. Create a new branch
2. Add your feature
3. Write tests
4. Update documentation
5. Submit for review

## 📄 License

MIT License - see [LICENSE](LICENSE) file

---

**Happy pipeline building!** 🎉

Questions? Check the documentation files or explore the source code - everything is well-commented!
