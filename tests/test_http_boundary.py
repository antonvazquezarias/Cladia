"""HTTP composition must retain routes and avoid server/peer-router imports."""
import ast
import json
from pathlib import Path
from cladia.server import app


def test_active_api_routes_preserved():
    snapshot = Path(__file__).parent / "data/http_routes.json"
    expected = {(path, method) for path, methods in json.loads(snapshot.read_text()) for method in methods}
    verbs = {"get", "post", "put", "delete", "patch", "options", "head"}
    actual = {(path, method.upper()) for path, methods in app.openapi()["paths"].items()
              for method in methods if method.lower() in verbs}
    assert expected == actual

def test_router_dependencies_are_one_way():
    root = Path(__file__).resolve().parents[1] / "cladia"
    for path in root.glob("api_*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module in (None, "cladia"):
                    names += [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            assert not any(name.split(".")[-1] == "server" or name.split(".")[-1].startswith("api_")
                           for name in names), f"{path}: server/peer-router dependency"
    for module in ("execution", "job_outputs", "job_progress", "item_inputs", "tool_validation",
                   "tool_jobs", "sequence_export", "tool_install", "tool_install_request", "conda"):
        tree = ast.parse((root / f"{module}.py").read_text())
        for node in ast.walk(tree):
            names = ([node.module or "", *[a.name for a in node.names]] if isinstance(node, ast.ImportFrom)
                     else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.split(".")[0] in {"fastapi", "starlette"} or
                           name.split(".")[-1].startswith("api_") or name.split(".")[-1] == "server"
                           for name in names), f"{module}: HTTP dependency"
