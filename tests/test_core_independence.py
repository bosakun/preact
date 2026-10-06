"""Prevent domain/vendor implementations from leaking into the shared runtime."""

import ast
import importlib.util
import sys
from pathlib import Path


def test_core_imports_only_domain_independent_contract_storage_and_numeric_libraries():
    root = Path(next(iter(importlib.util.find_spec("preact.core").submodule_search_locations)))
    allowed = sys.stdlib_module_names | {"pydantic", "sqlalchemy", "numpy", "scipy"}
    violations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 1:
                    violations.append(f"{path.name}:{node.lineno} relative import leaves Core")
                imports = [node.module or ""] if not node.level else []
            else:
                continue
            for module in imports:
                if module.startswith("preact.core.") or module == "preact.core":
                    continue
                if module.split(".")[0] not in allowed:
                    violations.append(f"{path.name}:{node.lineno} {module}")
    assert not violations, "Core dependency boundary violated: " + ", ".join(violations)
