"""Entry point for the Analysis GUI desktop application.

Usage::

    analysis-gui [PIPELINE]

``PIPELINE`` is an optional ``.pipeline`` file to open on startup.  With no
arguments the builder opens with an empty pipeline, as it always has.

Arguments are parsed, and any named file is checked, *before* PyQt6 is
imported.  ``--help``, ``--version`` and "no such file" therefore work on a
machine with no display and no Qt installed -- which is exactly where an editor
extension probing the installation tends to run them.

Exit codes:
  0  the window was opened and later closed normally
  1  the named file could not be read or is not a valid pipeline document,
     or the GUI itself is unavailable (PyQt6 missing)
  2  usage error (argparse)
"""

import argparse
import os
import sys
from typing import Optional, Sequence, TextIO

from . import __version__
from .pipeline import SCHEMA_VERSION, PipelineGraph

EXIT_OK = 0
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the desktop entry point."""
    parser = argparse.ArgumentParser(
        prog="analysis-gui",
        description="Launch the AnalysisGUI visual pipeline builder.",
        epilog=(
            "With no arguments the builder opens with an empty pipeline. "
            "For headless work (code generation, validation) use analysis-gui-cli."
        ),
    )
    parser.add_argument(
        "pipeline",
        nargs="?",
        default=None,
        metavar="PIPELINE",
        help="Path to a .pipeline file to open on startup",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"analysis-gui {__version__} (schema v{SCHEMA_VERSION})",
    )
    return parser


def check_pipeline_file(path: str) -> Optional[str]:
    """Check that a file can be opened as a pipeline before Qt starts.

    Returns ``None`` if the file loads, otherwise a human-readable reason it
    does not.  This deliberately loads the document rather than just stat-ing
    it: reporting "that is not a pipeline file" on a terminal the user is
    already looking at beats launching a window that immediately apologises.

    A pipeline with no nodes loads fine.  Emptiness makes a pipeline
    unrunnable, not unopenable, and refusing to open one would leave a user who
    saved an empty file with no way back into it.
    """
    try:
        PipelineGraph.from_file(path)
    except FileNotFoundError:
        return f"No such pipeline file: {path}"
    except IsADirectoryError:
        return f"Not a file: {path}"
    except OSError as exc:
        return f"Could not read {path}: {exc}"
    except ValueError as exc:
        return f"{path} is not a valid .pipeline file: {exc}"
    return None


def run_gui(
    pipeline_path: Optional[str] = None, stderr: Optional[TextIO] = None
) -> int:
    """Launch the Analysis GUI application, optionally opening a pipeline.

    Args:
        pipeline_path: A ``.pipeline`` file to load into the new window.
        stderr: Stream for error output; defaults to :data:`sys.stderr`.

    Returns:
        A process exit code.
    """
    stderr = stderr if stderr is not None else sys.stderr

    # Imported here, not at module scope, so that argument handling above never
    # needs Qt. Everything up to this point runs on a headless machine.
    try:
        from PyQt6.QtWidgets import QApplication

        from .ui.main_window import MainWindow
    except ImportError as exc:
        print(
            f"error: the desktop GUI is unavailable: {exc}\n"
            "hint: install the GUI dependencies with 'pip install PyQt6', or "
            "use 'analysis-gui-cli' for headless work.",
            file=stderr,
        )
        return EXIT_FAILURE

    # Qt gets the program name only: the pipeline argument is ours, and Qt has
    # no business trying to interpret it.
    app = QApplication(sys.argv[:1])
    window = MainWindow()

    if pipeline_path is not None:
        # Failure here means the file changed between the check above and now.
        # Qt is running, so report it the way every other in-window failure is
        # reported and leave the user with an empty builder.
        window.open_pipeline_file(pipeline_path)

    window.show()
    return app.exec()


def main(argv: Optional[Sequence[str]] = None, stderr: Optional[TextIO] = None) -> int:
    """Console script entry point. Returns a process exit code."""
    stderr = stderr if stderr is not None else sys.stderr

    parser = build_parser()
    args = parser.parse_args(argv)

    pipeline_path = None
    if args.pipeline is not None:
        pipeline_path = os.path.expanduser(args.pipeline)
        problem = check_pipeline_file(pipeline_path)
        if problem is not None:
            print(f"error: {problem}", file=stderr)
            return EXIT_FAILURE

    return run_gui(pipeline_path, stderr=stderr)


if __name__ == "__main__":
    sys.exit(main())
