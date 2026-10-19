"""Architectural boundary tests for ExecutionTargetService consumers."""

from __future__ import annotations

import ast
from pathlib import Path

_TARGET_STORE_IDENTIFIERS = {"ExecutionTargetStore", "target_store", "_target_store"}
_TARGET_STORE_FACTORY_METHODS = {"from_engine", "from_database_url", "from_session", "from_transaction"}
_TARGET_STORE_MODULE = "execution_plane.execution_target.execution_target_store"


def _base_identifier(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _imported_store_aliases(tree: ast.Module) -> set[str]:
    aliases = {"ExecutionTargetStore"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != _TARGET_STORE_MODULE:
            continue
        aliases.update(alias.asname or alias.name for alias in node.names if alias.name == "ExecutionTargetStore")
    return aliases


def test_production_code_uses_execution_target_service_not_execution_target_store() -> None:
    source_root = Path(__file__).parents[2] / "src" / "execution_plane"
    internal_target_files = {
        source_root / "execution_target" / "execution_target_store.py",
        source_root / "execution_target" / "execution_target_service.py",
    }
    composition_roots = {
        source_root / "bootstrap.py",
        source_root / "worker.py",
        source_root / "cluster" / "binding_reconciler.py",
        source_root / "commands" / "targets.py",
        source_root / "commands" / "queries.py",
    }
    production_files = [path for path in source_root.rglob("*.py") if path not in internal_target_files]

    assert production_files
    for path in production_files:
        tree = ast.parse(path.read_text(), filename=str(path))
        aliases = _imported_store_aliases(tree)
        is_composition_root = path in composition_roots
        for node in ast.walk(tree):
            if not is_composition_root:
                if isinstance(node, ast.Name):
                    assert node.id not in _TARGET_STORE_IDENTIFIERS, path
                    assert node.id not in aliases, path
                if isinstance(node, ast.Attribute):
                    assert node.attr not in _TARGET_STORE_IDENTIFIERS, path
                continue
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            identifier = _base_identifier(node.func.value)
            if identifier in _TARGET_STORE_IDENTIFIERS or identifier in aliases:
                assert node.func.attr in _TARGET_STORE_FACTORY_METHODS, path
