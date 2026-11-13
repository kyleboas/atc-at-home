"""Static check that controller.py only does what a controller needs. CI runs it before scoring.

    python3 tools/ci_guard.py [controller.py]

Allowed imports: a few pure standard-library modules and the atc package's procedures, world
and command set. Not allowed: other modules (os, sys, random, time, socket, ...), eval/exec,
file access, introspection (getattr, globals, dunder attributes), and changing anything in the
simulator's modules (assigning to W.x, P.x, C.x). This is a seat belt, not a sandbox: CI also
runs the code without network, as an unprivileged user, and a maintainer reads every diff.
"""
import ast
import os
import sys

MODULES = {"math", "heapq", "bisect", "collections", "itertools", "functools", "dataclasses", "enum",
           "typing", "statistics", "operator", "atc", "atc.procs", "atc.world", "atc.commands", "__future__"}
BANNED_CALLS = {"eval", "exec", "compile", "open", "__import__", "getattr", "setattr", "delattr", "globals",
                "locals", "vars", "breakpoint", "input", "memoryview", "help", "exit", "quit", "object"}
BANNED_NAMES = {"__builtins__", "__loader__", "__spec__", "__file__", "builtins", "sys", "os"}
# the controller works from the runways in use (W.ARR_RUNWAYS, W.DEP_RUNWAYS, ...), as a real
# controller does from the ATIS: it may not look the configuration up by name, so it has to handle
# configurations it has never seen (the holdout has some)
BANNED_ATTRS = {"CONFIG_KEY", "CONFIGS", "CONFIG", "set_config"}
BANNED_STRINGS = {"config", "config_key"}
MUTATORS = {"append", "extend", "insert", "pop", "remove", "clear", "update", "setdefault", "popitem",
            "add", "discard", "sort", "reverse", "__setitem__", "__delitem__"}


def check(src, filename="controller.py"):
    tree = ast.parse(src, filename)
    modules = set()   # names bound to imported modules / objects from the atc package
    errs = []

    def bad(node, msg):
        errs.append(f"{filename}:{getattr(node, 'lineno', '?')}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in MODULES:
                    bad(node, f"import {a.name} is not allowed")
                modules.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "") not in MODULES:
                bad(node, f"from {node.module} import is not allowed")
            for a in node.names:
                if a.name == "*":
                    bad(node, "import * is not allowed")
                if (node.module or "").startswith("atc"):
                    modules.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            bad(node, f"dunder attribute .{node.attr} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            bad(node, f".{node.attr}: use the runways in use, not the configuration's name")
        elif isinstance(node, ast.Constant) and node.value in BANNED_STRINGS:
            bad(node, f"'{node.value}': use the runways in use, not the configuration's name")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            bad(node, f"name {node.id} is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            bad(node, f"{node.func.id}() is not allowed")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, (ast.Attribute, ast.Subscript)) and _root(sub) in modules:
                        bad(node, f"changing {_root(sub)}.* (simulator data) is not allowed")
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)) and sub.id in modules:
                        bad(node, f"rebinding {sub.id} is not allowed")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS
              and _root(node.func.value) in modules):
            bad(node, f"{_root(node.func.value)}...{node.func.attr}() changes simulator data")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and any(n in modules for n in node.names):
            bad(node, "global/nonlocal on a simulator module is not allowed")
    return list(dict.fromkeys(errs))


def _root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "controller.py"
    errors = check(open(path).read(), path)
    for e in errors:
        print("::error::" + e if "GITHUB_ACTIONS" in os.environ else e)
    if errors:
        print(f"\n{len(errors)} problem(s). See tools/ci_guard.py for what a controller may use.")
    sys.exit(1 if errors else 0)
