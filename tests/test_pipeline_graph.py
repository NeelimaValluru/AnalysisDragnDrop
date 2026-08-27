"""Tests for the pipeline graph."""

import pytest
from analysis_gui.pipeline import Node, NodeType, PipelineGraph


class TestPipelineGraph:
    """Tests for PipelineGraph."""
    
    def test_add_node(self):
        """Test adding a node to the graph."""
        graph = PipelineGraph()
        node = Node.create_data_loader()
        node_id = graph.add_node(node)
        
        assert node_id in graph.nodes
        assert graph.nodes[node_id].label == "Load CSV"
    
    def test_remove_node(self):
        """Test removing a node from the graph."""
        graph = PipelineGraph()
        node = Node.create_data_loader()
        node_id = graph.add_node(node)
        
        assert graph.remove_node(node_id)
        assert node_id not in graph.nodes
    
    def test_add_edge(self):
        """Test adding an edge between nodes."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        
        assert graph.add_edge(id1, id2)
        assert (id1, id2) in graph.edges
    
    def test_remove_edge(self):
        """Test removing an edge."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        graph.add_edge(id1, id2)
        
        assert graph.remove_edge(id1, id2)
        assert (id1, id2) not in graph.edges
    
    def test_get_predecessors(self):
        """Test getting predecessor nodes."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        graph.add_edge(id1, id2)
        
        predecessors = graph.get_predecessors(id2)
        assert id1 in predecessors
    
    def test_get_successors(self):
        """Test getting successor nodes."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        graph.add_edge(id1, id2)
        
        successors = graph.get_successors(id1)
        assert id2 in successors
    
    def test_topological_order(self):
        """Test getting nodes in topological order."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        node3 = Node.create_analyzer("correlation")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        id3 = graph.add_node(node3)
        
        graph.add_edge(id1, id2)
        graph.add_edge(id2, id3)
        
        order = graph.get_topological_order()
        assert order.index(id1) < order.index(id2)
        assert order.index(id2) < order.index(id3)
    
    def test_validation_empty_graph(self):
        """Test validation of empty graph."""
        graph = PipelineGraph()
        is_valid, _ = graph.is_valid()
        assert not is_valid
    
    def test_validation_valid_graph(self):
        """Test validation of valid graph."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        graph.add_node(node1)
        graph.add_node(node2)
        
        is_valid, _ = graph.is_valid()
        assert is_valid
    
    def test_serialization(self):
        """Test serialization and deserialization."""
        graph = PipelineGraph()
        node1 = Node.create_data_loader()
        node2 = Node.create_preprocessor("normalize")
        
        id1 = graph.add_node(node1)
        id2 = graph.add_node(node2)
        graph.add_edge(id1, id2)
        
        # Serialize
        data = graph.to_dict()
        
        # Deserialize
        graph2 = PipelineGraph.from_dict(data)
        
        assert len(graph2.nodes) == 2
        assert len(graph2.edges) == 1
