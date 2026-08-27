"""
Analysis GUI — pipeline graphs, Python codegen, VS Code canvas.

The headless engine lives in :mod:`analysis_gui.pipeline` and
:mod:`analysis_gui.cli`.  The PyQt GUI is optional and imported lazily.
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

__all__ = ["run_gui"]


def __getattr__(name):
    """Resolve :func:`run_gui` on first use.

    Importing it eagerly would put ``analysis_gui.main`` in ``sys.modules``
    before ``python -m analysis_gui.main`` runs it, which makes runpy warn. The
    GUI entry point is also the rarer use of this package: the engine and the
    headless CLI are imported far more often.
    """
    if name == "run_gui":
        from .main import run_gui

        return run_gui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
