"""User code repository management.

Registered repositories live under ``~/.analysis_gui/repositories``.  Discovery
scans those plus the current workspace (``src/``, or a config list) using
:mod:`ast` only — importing a library is not required to learn nodes from it.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .learn import describe_discovered_kinds
from .matching import RANKER_API_INTENT, RANKER_NAME, ApiIntentIndex
from .scan import (
    DiscoveredFunction,
    count_chunks_by_kind,
    default_library_roots,
    scan_python_tree,
)
from .similar import run_similar


@dataclass
class Repository:
    """Represents a user code repository."""

    id: str
    name: str
    path: str
    description: str = ""
    functions: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert repository to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "functions": self.functions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Repository":
        """Create repository from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            path=data["path"],
            description=data.get("description", ""),
            functions=data.get("functions", {}),
        )


class RepositoryManager:
    """Manages user code repositories."""

    def __init__(self, storage_path: str = "~/.analysis_gui/repositories"):
        """
        Initialize the repository manager.

        Args:
            storage_path: Path to store repository metadata
        """
        self.storage_path = os.path.expanduser(storage_path)
        self.repositories: Dict[str, Repository] = {}
        self.load_repositories()

    def add_repository(self, repo: Repository) -> bool:
        """
        Add a repository.

        Args:
            repo: Repository to add

        Returns:
            True if added, False if already exists
        """
        if repo.id in self.repositories:
            return False

        self.repositories[repo.id] = repo
        self.save_repositories()
        return True

    def remove_repository(self, repo_id: str) -> bool:
        """
        Remove a repository.

        Args:
            repo_id: ID of repository to remove

        Returns:
            True if removed, False if not found
        """
        if repo_id not in self.repositories:
            return False

        del self.repositories[repo_id]
        self.save_repositories()
        return True

    def get_repository(self, repo_id: str) -> Optional[Repository]:
        """Get a repository by ID."""
        return self.repositories.get(repo_id)

    def list_repositories(self) -> list:
        """List all repositories."""
        return list(self.repositories.values())

    def registered_paths(self) -> List[str]:
        """Filesystem paths of registered repositories that still exist."""
        return [
            repo.path
            for repo in self.repositories.values()
            if repo.path and os.path.isdir(os.path.expanduser(repo.path))
        ]

    def scan_repository(
        self, repo_path: str, repository_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Scan a repository for Python functions (AST only).

        Args:
            repo_path: Path to the repository
            repository_id: Optional registered-repo id stored on each record

        Returns:
            Dictionary of functions found, keyed by qualified name
        """
        functions: Dict[str, Dict[str, Any]] = {}
        for record in scan_python_tree(
            os.path.expanduser(repo_path), repository_id=repository_id
        ):
            functions[record.qualified_name] = record.to_dict()
        return functions

    def scan_and_store(self, repo_id: str) -> Dict[str, Dict[str, Any]]:
        """Rescan a registered repository and persist the function index."""
        repo = self.get_repository(repo_id)
        if repo is None:
            return {}
        repo.functions = self.scan_repository(repo.path, repository_id=repo.id)
        self.save_repositories()
        return repo.functions

    def save_repositories(self):
        """Save repositories to disk."""
        os.makedirs(self.storage_path, exist_ok=True)
        metadata_file = os.path.join(self.storage_path, "repositories.json")
        data = {repo_id: repo.to_dict() for repo_id, repo in self.repositories.items()}

        with open(metadata_file, "w") as f:
            json.dump(data, f, indent=2)

    def load_repositories(self):
        """Load repositories from disk."""
        metadata_file = os.path.join(self.storage_path, "repositories.json")

        if not os.path.exists(metadata_file):
            return

        try:
            with open(metadata_file, "r") as f:
                data = json.load(f)

            for repo_data in data.values():
                repo = Repository.from_dict(repo_data)
                self.repositories[repo.id] = repo
        except Exception as e:
            print(f"Error loading repositories: {e}", file=sys.stderr)


def discover_libraries(
    *,
    roots: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
    manager: Optional[RepositoryManager] = None,
) -> Dict[str, Any]:
    """Scan configured library roots and return functions plus candidate kinds.

    When ``roots`` is given those directories are the search set.  Otherwise
    the default is ``<workspace>/src`` (or the workspace itself), optional
    paths from ``~/.analysis_gui/library_roots.json``, and every registered
    repository on ``manager``.
    """
    manager = (
        manager
        if manager is not None
        else (None if roots is not None else RepositoryManager())
    )
    registered = []
    repo_ids_by_path: Dict[str, str] = {}
    if manager is not None:
        for repo in manager.list_repositories():
            path = os.path.abspath(os.path.expanduser(repo.path))
            registered.append(path)
            repo_ids_by_path[path] = repo.id

    resolved_roots = default_library_roots(
        workspace=workspace,
        extra_roots=roots,
        registered_paths=registered if roots is None else (),
        include_config=roots is None,
    )

    records: List[DiscoveredFunction] = []
    errors: List[Dict[str, str]] = []
    if roots:
        for raw in roots:
            path = os.path.abspath(os.path.expanduser(raw))
            if not os.path.isdir(path):
                errors.append(
                    {
                        "code": "missing_library_root",
                        "message": f"Library root does not exist: {path}",
                        "path": path,
                    }
                )
    for root in resolved_roots:
        if not os.path.isdir(root):
            errors.append(
                {
                    "code": "missing_library_root",
                    "message": f"Library root does not exist: {root}",
                    "path": root,
                }
            )
            continue
        repo_id = repo_ids_by_path.get(os.path.abspath(root))
        records.extend(scan_python_tree(root, repository_id=repo_id))

    kinds = describe_discovered_kinds(records)
    chunk_counts = count_chunks_by_kind(records)
    match_index = ApiIntentIndex.build(records) if records else None
    return {
        "roots": resolved_roots,
        "functions": [record.to_dict() for record in records],
        "chunks": [record.to_dict() for record in records],
        "kinds": kinds,
        "count": len(records),
        "chunk_counts": chunk_counts,
        "errors": errors,
        "records": records,
        "match_index": match_index,
    }


def find_similar(
    query: str = "",
    *,
    roots: Optional[Sequence[str]] = None,
    workspace: Optional[str] = None,
    manager: Optional[RepositoryManager] = None,
    limit: int = 20,
    ranker: str = RANKER_API_INTENT,
    from_span: Optional[str] = None,
    from_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Discover libraries and rank chunks for ``query`` or a seed chunk."""
    index = discover_libraries(roots=roots, workspace=workspace, manager=manager)
    search = run_similar(
        query,
        index["records"],
        limit=limit,
        ranker=ranker,
        match_index=index.get("match_index"),
        from_span=from_span,
        from_kind=from_kind,
    )
    label = query
    if from_span:
        label = from_span
    elif from_kind:
        label = from_kind
    return {
        "query": label,
        "roots": index["roots"],
        "hits": search["hits"],
        "count": len(search["hits"]),
        "indexed": index["count"],
        "chunk_counts": index["chunk_counts"],
        "reranked": search["reranked"],
        "ranker": search["ranker"],
        "candidates_examined": search["candidates_examined"],
        "used_fallback": search["used_fallback"],
        "alignments_scored": search.get("alignments_scored", 0),
        "errors": index["errors"],
        "from_span": from_span,
        "from_kind": from_kind,
    }


__all__ = [
    "Repository",
    "RepositoryManager",
    "DiscoveredFunction",
    "discover_libraries",
    "find_similar",
    "default_library_roots",
    "count_chunks_by_kind",
    "ApiIntentIndex",
    "RANKER_NAME",
]
