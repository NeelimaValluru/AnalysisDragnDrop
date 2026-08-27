"""Reusable widgets for the UI."""

from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QLabel
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QDrag, QFont
from PyQt6.QtCore import pyqtSignal

from ...pipeline import NODE_KINDS


class NodePalette(QWidget):
    """Palette of available nodes to drag onto the canvas."""

    node_selected = pyqtSignal(str)  # node_type

    def __init__(self, parent=None):
        """Initialize the node palette."""
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()

        # Title
        title = QLabel("Pipeline Nodes")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # Node list
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(self.list_widget.DragDropMode.DragOnly)

        # Node kinds come from the registry in pipeline.node so the palette,
        # the factories and the CLI cannot drift apart.
        for spec in NODE_KINDS.values():
            if not spec.in_palette:
                continue
            item = QListWidgetItem(spec.palette_label)
            item.setData(Qt.ItemDataRole.UserRole, spec.kind)
            self.list_widget.addItem(item)

        layout.addWidget(self.list_widget)
        self.setLayout(layout)


class PropertyInspector(QWidget):
    """Inspector for viewing and editing node properties."""

    def __init__(self, parent=None):
        """Initialize the property inspector."""
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        """Initialize the UI."""
        layout = QVBoxLayout()

        # Title
        title = QLabel("Node Properties")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # Properties list
        self.property_list = QListWidget()
        layout.addWidget(self.property_list)

        self.setLayout(layout)

    def set_node(self, node):
        """Set the node to inspect."""
        self.property_list.clear()

        if node is None:
            return

        # Add node information
        info_items = [
            f"ID: {node.id}",
            f"Type: {node.node_type.value}",
            f"Label: {node.label}",
        ]

        for info in info_items:
            self.property_list.addItem(info)

        # Add parameters. Show the resolved value, flagging user overrides so
        # it is clear when a value differs from the node kind's default.
        if node.parameters:
            self.property_list.addItem("--- Parameters ---")
            for param_name, param in node.parameters.items():
                suffix = "" if param.value is None else "  (edited)"
                self.property_list.addItem(
                    f"{param_name}: {param.resolved_value}{suffix}"
                )
