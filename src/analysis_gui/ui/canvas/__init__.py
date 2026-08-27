"""Canvas and drag-drop functionality for the pipeline builder."""

from PyQt6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QGraphicsLineItem,
)
from PyQt6.QtCore import Qt, QPointF, QMimeData, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QPen
from typing import Dict, Optional, Tuple


class PipelineNodeGraphic(QGraphicsRectItem):
    """Visual representation of a pipeline node."""

    def __init__(
        self, node_id: str, label: str, node_type: str, x: float = 0, y: float = 0
    ):
        """Initialize node graphic."""
        super().__init__(0, 0, 150, 80)
        self.node_id = node_id
        self.label = label
        self.node_type = node_type

        # Styling
        self.setBrush(self._get_color_for_type(node_type))
        self.setPen(QPen(QColor(0, 0, 0), 2))
        self.setPos(x, y)

        # Add text
        text_item = QGraphicsTextItem(self)
        text_item.setPlainText(label)
        text_item.setFont(QFont("Arial", 9))
        text_item.setPos(5, 30)

        # Make draggable
        self.setAcceptHoverEvents(True)
        self.setFlag(self.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable, True)

    def _get_color_for_type(self, node_type: str) -> QColor:
        """Get color based on node type."""
        colors = {
            "data_loader": QColor(100, 150, 255),  # Blue
            "preprocessor": QColor(100, 255, 150),  # Green
            "analyzer": QColor(255, 200, 100),  # Orange
            "visualizer": QColor(255, 150, 150),  # Red
            "model_call": QColor(200, 100, 255),  # Purple
            "custom_code": QColor(255, 255, 100),  # Yellow
        }
        return colors.get(node_type, QColor(200, 200, 200))


class PipelineCanvas(QGraphicsView):
    """Canvas for building pipelines visually."""

    node_dropped = pyqtSignal(str, float, float)  # node_type, x, y
    node_selected = pyqtSignal(str)  # node_id

    def __init__(self, parent=None):
        """Initialize the canvas."""
        super().__init__(parent)
        self.scene = QGraphicsScene()
        self.setScene(self.scene)

        self.nodes: Dict[str, PipelineNodeGraphic] = {}
        self.edges = []

        # Set up canvas
        self.setRenderHint(self.RenderHint.Antialiasing)
        self.setSceneRect(0, 0, 2000, 1500)

        # Connect signals
        self.scene.selectionChanged.connect(self._on_selection_changed)

    def add_node(self, node_id: str, label: str, node_type: str, x: float, y: float):
        """Add a node to the canvas."""
        node_graphic = PipelineNodeGraphic(node_id, label, node_type, x, y)
        self.scene.addItem(node_graphic)
        self.nodes[node_id] = node_graphic

    def remove_node(self, node_id: str):
        """Remove a node from the canvas."""
        if node_id in self.nodes:
            item = self.nodes[node_id]
            self.scene.removeItem(item)
            del self.nodes[node_id]

    def add_edge(self, source_id: str, target_id: str):
        """Add a connection between two nodes."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return

        source = self.nodes[source_id]
        target = self.nodes[target_id]

        line = QGraphicsLineItem(
            source.rect().center().x() + source.pos().x(),
            source.rect().center().y() + source.pos().y(),
            target.rect().center().x() + target.pos().x(),
            target.rect().center().y() + target.pos().y(),
        )
        line.setPen(QPen(QColor(0, 0, 0), 2))
        self.scene.addItem(line)
        self.edges.append((source_id, target_id, line))

    def dragEnterEvent(self, event):
        """Handle drag enter event."""
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        """Handle drag move event."""
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle drop event."""
        if event.mimeData().hasText():
            node_type = event.mimeData().text()
            pos = self.mapToScene(event.pos())
            self.node_dropped.emit(node_type, pos.x(), pos.y())
            event.acceptProposedAction()

    def _on_selection_changed(self):
        """Handle selection changes."""
        selected_items = self.scene.selectedItems()
        if selected_items:
            for item in selected_items:
                if isinstance(item, PipelineNodeGraphic):
                    self.node_selected.emit(item.node_id)
