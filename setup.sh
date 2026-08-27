#!/usr/bin/env bash

# Setup and run Analysis GUI

echo "═══════════════════════════════════════════════════════"
echo "  Analysis GUI - Visual Pipeline Builder Setup"
echo "═══════════════════════════════════════════════════════"
echo ""

# Check Python version
echo "✓ Checking Python installation..."
python3 --version

# Navigate to project directory
cd /Users/neelimavalluru/Desktop/AnalysisGUI

# Install package in development mode
echo ""
echo "✓ Installing Analysis GUI package..."
pip install -e ".[dev]"

if [ $? -ne 0 ]; then
    echo "❌ Installation failed. Please check your Python environment."
    exit 1
fi

echo ""
echo "✓ Installation successful!"
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Next Steps:"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  1. Run tests:"
echo "     pytest tests/"
echo ""
echo "  2. Launch the application:"
echo "     analysis-gui"
echo ""
echo "  3. Read the documentation:"
echo "     • QUICKSTART.md - 5 minute getting started guide"
echo "     • README.md - Full feature documentation"
echo "     • ARCHITECTURE.md - System design details"
echo ""
echo "  4. Build your first pipeline!"
echo "     • Drag nodes from the left panel"
echo "     • View generated code on the right"
echo "     • Save your pipeline for later use"
echo ""
echo "═══════════════════════════════════════════════════════"
echo ""
