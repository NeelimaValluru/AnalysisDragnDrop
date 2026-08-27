# Analysis GUI - Project Summary

## What We've Built

A **visual, drag-and-drop pipeline builder** for data analysis - similar to Snap programming blocks but for data science workflows.

## Installation & Usage

### Install
```bash
cd /Users/neelimavalluru/Desktop/AnalysisGUI
pip install -e ".[dev]"
```

### Run
```bash
analysis-gui
```

### Test
```bash
pytest tests/
```

### Format Code
```bash
black src/ tests/
```

## Example Generated Code

When you build a pipeline like:
```
Load CSV → Normalize → Clustering → Visualization
```

It generates:
```python
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt

# Load CSV data
output_0 = pd.read_csv('data.csv', delimiter=',')
print(f'Loaded data shape: {output_0.shape}')

# Preprocessing: Normalize
output_1 = output_0.copy()
output_1 = (output_0 - output_0.min()) / (output_0.max() - output_0.min())

# Analysis: Clustering
from sklearn.cluster import KMeans
kmeans = KMeans(n_clusters=3, random_state=42)
output_2 = kmeans.fit_predict(output_1)

# Visualization: Visualization
import matplotlib.pyplot as plt
plt.figure(figsize=(10, 6))
plt.plot(output_2)
plt.title('Visualization plot generated')
plt.show()
output_3 = 'Visualization plot generated'

if __name__ == '__main__':
    print('Pipeline executed successfully')
```

## Technology Stack

- **GUI**: PyQt6 (modern, professional desktop application)
- **Data**: Pandas, NumPy
- **ML**: Scikit-learn, TensorFlow
- **Visualization**: Matplotlib
- **Testing**: Pytest
- **Code Quality**: Black, Flake8, MyPy

## Package Distribution

The project is set up as a professional Python package:
- Modern `pyproject.toml` configuration
- Installable via pip
- Console script entry point (`analysis-gui`)
- Development dependencies included
- Ready for PyPI distribution

## Customization Points

### Add a New Preprocessor
Edit `src/analysis_gui/pipeline/node.py` - `create_preprocessor()` method

### Add Analysis Type
Edit `src/analysis_gui/pipeline/node.py` - `create_analyzer()` method

### Add Data Format
Edit `src/analysis_gui/pipeline/node.py` - `create_data_loader()` method

### Customize Code Generation
Edit `src/analysis_gui/pipeline/code_generator.py` - `_generate_*` methods

### Add UI Elements
Edit `src/analysis_gui/ui/` - main_window.py, canvas/__init__.py, or widgets/__init__.py

## Ready to Deploy

The package is production-ready for:
- Installation via `pip install -e .`
- Distribution on PyPI
- Containerization with Docker
- Integration into larger systems

## Summary

You now have a **professional, extensible visual pipeline builder** for data analysis. It's fully functional for:
- Building complex data workflows visually
- Generating executable Python code
- Integrating custom code and AI models
- Sharing pipelines with others

The architecture is designed to be **highly extensible** - add new node types, data formats, analysis methods, and integrations as needed.

Perfect for:
✅ Data scientists learning to code
✅ Business analysts building workflows
✅ Teams sharing analysis pipelines
✅ Teaching data analysis concepts
✅ Rapid prototyping of analysis workflows
