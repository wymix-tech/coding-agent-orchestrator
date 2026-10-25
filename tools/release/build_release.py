"""Build the runtime-only CAO skill archive used by GitHub Releases."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "release-manifest.txt"


def patterns() -> tuple[list[str], list[str]]:
    includes: list[str] = []
    excludes: list[str] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            excludes.append(line[1:].strip())
        else:
            includes.append(line)
    return includes, excludes


def expand(patterns: list[str], *, required: bool = True) -> list[Path]:
    files = []
    for pattern in patterns:
        matches = sorted(ROOT.glob(pattern))
        if not matches:
            # A missing include is a manifest error; a missing exclusion simply
            # means the build-time file is already gone.
            if required:
                raise SystemExit(f"release manifest entry has no matches: {pattern}")
            continue
        for match in matches:
            if match.is_file():
                files.append(match)
            elif match.is_dir():
                files.extend(path for path in sorted(match.rglob("*")) if path.is_file())
    return files


def included_files() -> list[Path]:
    includes, excludes = patterns()
    return sorted(set(expand(includes)) - set(expand(excludes, required=False)))


def validate(files: list[Path]) -> None:
    if not files:
        raise SystemExit("release manifest selected no files")
    forbidden = (".git", "node_modules", "tests", "docs", "examples")
    for path in files:
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in forbidden:
            raise SystemExit(f"forbidden development path in release: {relative}")
    required = {"SKILL.md", "LICENSE", "cao", "plugin.json", "pyproject.toml"}
    selected = {path.relative_to(ROOT).as_posix() for path in files}
    missing = sorted(required - selected)
    if missing:
        raise SystemExit(f"required runtime files missing: {', '.join(missing)}")


def archive_version(version: str) -> str:
    """Keep conventional tags readable; disambiguate normalized or long tags."""
    if not version:
        raise ValueError("release tag must not be empty")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", version):
        return version
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip(".-_")[:60] or "tag"
    return readable + "-" + hashlib.sha256(version.encode("utf-8")).hexdigest()[:16]


def build(output: Path, version: str) -> tuple[Path, Path]:
    files = included_files()
    validate(files)
    output.mkdir(parents=True, exist_ok=True)
    name = f"coding-agent-orchestrator-{archive_version(version)}-runtime"
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / name
        for source in files:
            destination = staging / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        archive = output / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(staging, arcname=name)
        zip_path = output / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for source in sorted(staging.rglob("*")):
                if source.is_file():
                    handle.write(source, source.relative_to(staging.parent))
    return archive, zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default="dev")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = included_files()
    validate(files)
    if args.check:
        print(f"release manifest valid: {len(files)} files")
        return 0
    archives = build(args.output, args.version)
    for archive in archives:
        print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
