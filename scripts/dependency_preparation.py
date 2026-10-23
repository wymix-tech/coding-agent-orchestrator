"""Bound dependency preparation to project environments before implementation."""
import os
import re
from pathlib import Path

PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?(?:\.exe)?")
PIP = re.compile(r"pip(?:3(?:\.\d+)?)?(?:\.exe)?")


def dependency_attempt(tokens):
    name = Path(tokens[0]).name.lower() if tokens else ""
    return bool(((PYTHON.fullmatch(name) or PIP.fullmatch(name)) and "install" in tokens) or
                tokens[:3] == ["uv", "pip", "install"] or
                name == "npm" and "install" in tokens or
                PYTHON.fullmatch(name) and tokens[1:3] == ["-m", "venv"] or tokens[:2] == ["uv", "venv"])


def project_environment(repo, executable):
    path = Path(os.path.abspath(executable))
    for name in (".venv", "venv"):
        root = repo.resolve() / name
        if (root.resolve() == root and (root / "pyvenv.cfg").is_file() and
                path.parent in {root / "bin", root / "Scripts"} and path.parent.resolve() == path.parent
                and re.fullmatch(r"(?:python(?:3(?:\.\d+)?)?|pip(?:3(?:\.\d+)?)?)(?:\.exe)?", path.name)
                and path.is_file()):
            return True
    return False


def dependency_preparation(repo, tokens, cwd):
    """No implicit active venv, global install, target override or project switch."""
    if not tokens:
        return False
    forbidden = ("--break-system-packages", "--user", "--system", "--target", "--prefix", "--root",
                 "--global", "--directory", "--project", "--location", "--config-file")
    if any(t == "-C" or (t.startswith(("-t", "-g", "-p")) and not t.startswith("--")) or
           any(t == flag or t.startswith(flag + "=") for flag in forbidden) for t in tokens[1:]):
        return False
    name = Path(tokens[0]).name.lower()
    if name == "npm" and tokens[1:2] == ["install"]:
        return cwd.resolve().is_relative_to(repo.resolve())
    if tokens[:3] == ["uv", "pip", "install"]:
        # An explicit project interpreter avoids an inherited external VIRTUAL_ENV.
        if tokens.count("--python") != 1 or any(t.startswith("--python=") for t in tokens):
            return False
        index = tokens.index("--python")
        if index + 1 >= len(tokens):
            return False
        return project_environment(repo, cwd / tokens[index + 1])
    if (PYTHON.fullmatch(name) and tokens[1:3] == ["-m", "venv"]) or tokens[:2] == ["uv", "venv"]:
        args = tokens[3:] if PYTHON.fullmatch(name) else tokens[2:]
        if not args and name == "uv":
            args = [".venv"]
        if len(args) != 1 or args[0].startswith("-"):
            return False
        target = Path(os.path.abspath(cwd / args[0]))
        return target in {repo.resolve() / ".venv", repo.resolve() / "venv"} and target.resolve() == target
    executable = cwd / tokens[0]
    if not project_environment(repo, executable) or "--python" in tokens[1:]:
        return False
    return bool((PYTHON.fullmatch(name) and tokens[1:4] == ["-m", "pip", "install"]) or
                (PIP.fullmatch(name) and tokens[1:2] == ["install"]))
