"""Main window for the Analysis GUI."""

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QPlainTextEdit,
    QPushButton,
    QMenuBar,
    QMenu,
    QMessageBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
import json

from ..pipeline import Node, NodeType, PipelineGraph, CodeGenerator
from ..ui.canvas import PipelineCanvas
from ..ui.widgets import NodePalette, PropertyInspector


class MainWindow(QMainWindow):
    """Main application window for the visual pipeline builder."""

    def __init__(self):
        super().__init__()
        self.pipeline = PipelineGraph()
        self.selected_node = None
        self.init_ui()

    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("Analysis GUI - Visual Pipeline Builder")
        self.setGeometry(100, 100, 1400, 800)

        # Create main widget
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # Create layouts
        main_layout = QHBoxLayout()

        # Left panel: Node palette
        self.node_palette = NodePalette()
        self.node_palette.list_widget.itemDoubleClicked.connect(
            self.on_node_palette_item_clicked
        )

        # Center: Canvas
        self.canvas = PipelineCanvas()
        self.canvas.node_dropped.connect(self.on_node_dropped)
        self.canvas.node_selected.connect(self.on_node_selected)

        # Right panel: Property inspector and code view
        right_layout = QVBoxLayout()

        self.property_inspector = PropertyInspector()
        right_layout.addWidget(self.property_inspector, 1)

        # Code view
        code_label = QPushButton("Generated Code")
        code_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        code_label.clicked.connect(self.generate_and_show_code)
        right_layout.addWidget(code_label)

        self.code_view = QPlainTextEdit()
        self.code_view.setReadOnly(False)
        self.code_view.setFont(QFont("Courier", 9))
        right_layout.addWidget(self.code_view, 2)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)

        # Add to main layout
        main_layout.addWidget(self.node_palette, 1)
        main_layout.addWidget(self.canvas, 2)
        main_layout.addWidget(right_widget, 1)

        main_widget.setLayout(main_layout)

        # Create menu bar
        self.create_menu_bar()

    def create_menu_bar(self):
        """Create the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        new_action = file_menu.addAction("New Pipeline")
        new_action.triggered.connect(self.new_pipeline)

        open_action = file_menu.addAction("Open Pipeline")
        open_action.triggered.connect(self.open_pipeline)

        save_action = file_menu.addAction("Save Pipeline")
        save_action.triggered.connect(self.save_pipeline)

        file_menu.addSeparator()
        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        # Pipeline menu
        pipeline_menu = menubar.addMenu("Pipeline")

        generate_action = pipeline_menu.addAction("Generate Code")
        generate_action.triggered.connect(self.generate_and_show_code)

        validate_action = pipeline_menu.addAction("Validate")
        validate_action.triggered.connect(self.validate_pipeline)

        # Help menu
        help_menu = menubar.addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self.show_about)

    def on_node_palette_item_clicked(self, item):
        """Handle node palette item double-click by placing the node."""
        node_type = item.data(Qt.ItemDataRole.UserRole)
        # A double-click places the node directly rather than starting a drag:
        # the palette's QListWidget exposes no text/plain mime data, and a drag
        # begun after the mouse button is already released can never produce a
        # drop the canvas would accept.
        x, y = self._next_node_position()
        self.on_node_dropped(node_type, x, y)

    def _next_node_position(self):
        """Cascade newly placed nodes so they do not stack on one another."""
        index = len(self.pipeline.nodes)
        return 60.0 + (index % 5) * 180.0, 60.0 + (index // 5 % 6) * 110.0

    def on_node_dropped(self, node_type: str, x: float, y: float):
        """Handle node drop on canvas."""
        node = self.create_node_from_type(node_type)
        node.position = (x, y)

        self.pipeline.add_node(node)
        self.canvas.add_node(node.id, node.label, node.node_type.value, x, y)

    def on_node_selected(self, node_id: str):
        """Handle node selection on canvas."""
        self.selected_node = self.pipeline.get_node(node_id)
        self.property_inspector.set_node(self.selected_node)

    def create_node_from_type(self, node_type: str) -> Node:
        """Create a node from a palette node-kind key."""
        try:
            return Node.create_from_kind(node_type)
        except ValueError:
            return Node(
                id="", node_type=NodeType.ANALYZER, label="Unknown", description=""
            )

    def generate_and_show_code(self):
        """Generate and display the pipeline code."""
        try:
            generator = CodeGenerator(self.pipeline)
            code = generator.generate()
            self.code_view.setPlainText(code)
        except ValueError as e:
            QMessageBox.warning(self, "Generation Error", str(e))

    def validate_pipeline(self):
        """Validate the current pipeline."""
        is_valid, message = self.pipeline.is_valid()
        if is_valid:
            QMessageBox.information(self, "Pipeline Validation", "✓ Pipeline is valid!")
        else:
            QMessageBox.warning(self, "Pipeline Validation", f"✗ {message}")

    def new_pipeline(self):
        """Create a new pipeline."""
        self.pipeline = PipelineGraph()
        self.canvas.scene.clear()
        self.canvas.nodes.clear()
        self.code_view.clear()
        self.property_inspector.property_list.clear()

    def save_pipeline(self):
        """Save the current pipeline to a file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Pipeline", "", "Pipeline Files (*.pipeline);;All Files (*)"
        )

        if file_path:
            try:
                data = self.pipeline.to_dict()
                with open(file_path, "w") as f:
                    json.dump(data, f, indent=2)
                QMessageBox.information(
                    self, "Success", f"Pipeline saved to {file_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save: {e}")

    def load_pipeline_file(self, file_path: str):
        """Replace the current pipeline with the one in the given file.

        Raises whatever reading the file raises; callers decide how to report
        it. Use :meth:`open_pipeline_file` for the usual dialog-based handling.
        """
        graph = PipelineGraph.from_file(file_path)

        self.new_pipeline()
        self.pipeline = graph

        # Redraw canvas
        for node_id, node in self.pipeline.nodes.items():
            self.canvas.add_node(
                node_id, node.label, node.node_type.value, *node.position
            )

        for edge in self.pipeline.edges:
            self.canvas.add_edge(edge.source, edge.target)

    def open_pipeline_file(self, file_path: str) -> bool:
        """Load a pipeline file, reporting failure in a dialog.

        Returns:
            True if the pipeline was loaded.
        """
        try:
            self.load_pipeline_file(file_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load: {e}")
            return False
        return True

    def open_pipeline(self):
        """Load a pipeline from a file chosen in a dialog."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Pipeline", "", "Pipeline Files (*.pipeline);;All Files (*)"
        )

        if file_path and self.open_pipeline_file(file_path):
            QMessageBox.information(
                self, "Success", f"Pipeline loaded from {file_path}"
            )

    def show_about(self):
        """Show the about dialog."""
        QMessageBox.about(
            self,
            "About Analysis GUI",
            "Analysis GUI v0.1.0\n\n"
            "A visual pipeline builder for data analysis.\n\n"
            "Build complex data analysis workflows by connecting nodes.\n"
            "Generate executable Python code automatically.",
        )
