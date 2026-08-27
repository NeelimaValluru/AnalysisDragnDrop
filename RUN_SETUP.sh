#!/bin/bash

# Analysis GUI - Complete Setup & Test Guide

echo "════════════════════════════════════════════════════════════"
echo "  Analysis GUI - Setup & Testing Guide"
echo "════════════════════════════════════════════════════════════"
echo ""

# Navigate to project
cd /Users/neelimavalluru/Desktop/AnalysisGUI

echo "📍 Project Location: $(pwd)"
echo ""

# Step 1: Show Python version
echo "════════════════════════════════════════════════════════════"
echo "STEP 1: Check Python"
echo "════════════════════════════════════════════════════════════"
python3 --version
echo ""

# Step 2: Install package
echo "════════════════════════════════════════════════════════════"
echo "STEP 2: Install Analysis GUI"
echo "════════════════════════════════════════════════════════════"
echo "Installing in development mode with dependencies..."
pip install -e ".[dev]" 2>&1 | tail -20
echo ""
echo "✓ Installation complete!"
echo ""

# Step 3: Verify imports
echo "════════════════════════════════════════════════════════════"
echo "STEP 3: Verify Core Modules Load"
echo "════════════════════════════════════════════════════════════"

python3 << 'EOF'
try:
    print("Testing imports...")
    from analysis_gui.pipeline import Node, NodeType, PipelineGraph, CodeGenerator
    print("✓ Pipeline modules imported successfully")
    
    from analysis_gui.models import ModelIntegration
    print("✓ Models module imported successfully")
    
    from analysis_gui.repository import RepositoryManager
    print("✓ Repository module imported successfully")
    
    print("\n✓ All core modules loaded successfully!")
except ImportError as e:
    print(f"✗ Import error: {e}")
    exit(1)
EOF

echo ""

# Step 4: Run quick test
echo "════════════════════════════════════════════════════════════"
echo "STEP 4: Quick Functionality Test"
echo "════════════════════════════════════════════════════════════"

python3 << 'EOF'
from analysis_gui.pipeline import Node, NodeType, PipelineGraph, CodeGenerator

print("1. Creating nodes...")
loader = Node.create_data_loader("csv")
normalizer = Node.create_preprocessor("normalize")
clusterer = Node.create_analyzer("clustering")
print(f"   ✓ Created 3 nodes")

print("\n2. Building pipeline graph...")
graph = PipelineGraph()
id1 = graph.add_node(loader)
id2 = graph.add_node(normalizer)
id3 = graph.add_node(clusterer)
print(f"   ✓ Added 3 nodes to graph")

print("\n3. Connecting nodes...")
graph.add_edge(id1, id2)
graph.add_edge(id2, id3)
print(f"   ✓ Connected: CSV → Normalize → Clustering")

print("\n4. Validating pipeline...")
is_valid, msg = graph.is_valid()
if is_valid:
    print(f"   ✓ Pipeline is valid: {msg}")
else:
    print(f"   ✗ Pipeline error: {msg}")

print("\n5. Generating Python code...")
try:
    generator = CodeGenerator(graph)
    code = generator.generate()
    lines = code.split('\n')
    print(f"   ✓ Generated {len(lines)} lines of code")
    print(f"\n   First 10 lines:")
    for line in lines[:10]:
        print(f"   {line}")
except Exception as e:
    print(f"   ✗ Code generation failed: {e}")

print("\n✓ Functionality test complete!")
EOF

echo ""

# Step 5: Run unit tests
echo "════════════════════════════════════════════════════════════"
echo "STEP 5: Run Unit Tests"
echo "════════════════════════════════════════════════════════════"
pytest tests/ -v 2>&1 | tail -30
echo ""

# Step 6: Show next steps
echo "════════════════════════════════════════════════════════════"
echo "NEXT STEPS"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "1. Launch the GUI:"
echo "   $ analysis-gui"
echo ""
echo "2. Read the documentation:"
echo "   - QUICKSTART.md           → 5-minute tutorial"
echo "   - README.md               → Full documentation"
echo "   - ARCHITECTURE_WALKTHROUGH.md → File-by-file guide"
echo ""
echo "3. Build a pipeline:"
echo "   - Drag 'Load CSV' node"
echo "   - Drag 'Normalize' node"
echo "   - Drag 'Clustering' node"
echo "   - Click 'Generated Code' to see Python"
echo ""
echo "4. Format and lint code:"
echo "   $ black src/ tests/"
echo "   $ flake8 src/ tests/"
echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ Setup Complete! You're ready to go!"
echo "════════════════════════════════════════════════════════════"
