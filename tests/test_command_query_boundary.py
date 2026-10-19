"""Architectural boundary tests for client-scoped resource queries."""

from __future__ import annotations

import ast
from pathlib import Path


def test_resource_queries_do_not_access_orm_tables_directly() -> None:
    path = Path(__file__).parents[1] / "src" / "execution_plane" / "commands" / "queries.py"
    tree = ast.parse(path.read_text(), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules = {alias.name for alias in node.names}
            assert "sqlalchemy" not in imported_modules, path
            assert "sqlmodel" not in imported_modules, path
        if isinstance(node, ast.ImportFrom):
            assert node.module != "sqlalchemy", path
            assert node.module != "sqlmodel", path
            assert not (node.module or "").startswith("execution_plane.models."), path
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "select", path
