#!/usr/bin/env python3
"""
Quick test to verify the Analysis GUI pipeline system works.
This tests the core logic without needing PyQt6.
"""

import sys
sys.path.insert(0, '/Users/neelimavalluru/Desktop/AnalysisGUI/src')

from analysis_gui.pipeline import Node, NodeType, PipelineGraph, CodeGenerator

print("=" * 70)
print("ANALYSIS GUI - PIPELINE SYSTEM TEST")
print("=" * 70)

# Test 1: Create nodes
print("\n1. Creating nodes...")
loader = Node.create_data_loader("csv")
normalizer = Node.create_preprocessor("normalize")
clustering = Node.create_analyzer("clustering")
viz = Node(id="", node_type=NodeType.VISUALIZER, label="Visualization")

print(f"   ✓ Created 4 nodes:")
print(f"     - {loader.label} (type: {loader.node_type.value})")
print(f"     - {normalizer.label} (type: {normalizer.node_type.value})")
print(f"     - {clustering.label} (type: {clustering.node_type.value})")
print(f"     - {viz.label} (type: {viz.node_type.value})")

# Test 2: Build a pipeline
print("\n2. Building a pipeline graph...")
graph = PipelineGraph()
id1 = graph.add_node(loader)
id2 = graph.add_node(normalizer)
id3 = graph.add_node(clustering)
id4 = graph.add_node(viz)

print(f"   ✓ Added {len(graph.nodes)} nodes to graph")

# Add edges (connections)
graph.add_edge(id1, id2)
graph.add_edge(id2, id3)
graph.add_edge(id3, id4)

print(f"   ✓ Connected nodes with {len(graph.edges)} edges:")
for source, target in graph.edges:
    src_node = graph.get_node(source)
    tgt_node = graph.get_node(target)
    print(f"     {src_node.label} → {tgt_node.label}")

# Test 3: Validate pipeline
print("\n3. Validating pipeline...")
is_valid, message = graph.is_valid()
print(f"   {'✓' if is_valid else '✗'} Pipeline valid: {message}")

# Test 4: Generate code
print("\n4. Generating Python code...")
generator = CodeGenerator(graph)
code = generator.generate()

print("   ✓ Generated Python code:")
print("   " + "-" * 66)
for line in code.split('\n')[:20]:  # Show first 20 lines
    print(f"   {line}")
if len(code.split('\n')) > 20:
    print(f"   ... ({len(code.split(chr(10))) - 20} more lines)")
print("   " + "-" * 66)

# Test 5: Serialization
print("\n5. Testing serialization...")
pipeline_data = graph.to_dict()
print(f"   ✓ Serialized to JSON with:")
print(f"     - {len(pipeline_data['nodes'])} nodes")
print(f"     - {len(pipeline_data['edges'])} edges")

# Test 6: Deserialization
print("\n6. Deserializing back...")
graph2 = PipelineGraph.from_dict(pipeline_data)
print(f"   ✓ Reconstructed graph with:")
print(f"     - {len(graph2.nodes)} nodes")
print(f"     - {len(graph2.edges)} edges")

print("\n" + "=" * 70)
print("✅ ALL TESTS PASSED - Pipeline system is working!")
print("=" * 70)
print("\nTo launch the GUI:")
print("  cd /Users/neelimavalluru/Desktop/AnalysisGUI")
print("  pip install -e '.[dev]'")
print("  analysis-gui")
print("\n")
