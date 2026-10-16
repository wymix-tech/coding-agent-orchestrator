"""Explicit, previewable migration of CAO project instructions."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid


BEGIN = "<!-- CAO:BEGIN v2 -->"
END = "<!-- CAO:END -->"
# Exact normalized standalone templates shipped at main 18e07a7. Custom edits do
# not match, so they cannot be mistaken for replaceable generated instructions.
LEGACY_HASHES = {
    "AGENTS.md": "fcc28c90c972329c4afcf98fd92590b0ad1067b6d60e6bba2e12c7871b43b595",
    "CLAUDE.md": "8d79890100bf23a35f17eaf58eab31d820cddfbd80e8ec4b340eff6c6e5e22ae",
}


def legacy_template(text: str, name: str) -> bool:
    match = re.search(r"```\n(.+?)\n```", text, re.S)
    if not match:
        return False
    command = match.group(1).strip()
    normalized = text.replace(command, "{{CAO_COMMAND}}")
    normalized = re.sub(r"--host (?:claude-code|codex|pi)", "--host <host>", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest() == LEGACY_HASHES.get(name)


def update_instructions(repo: Path, rendered: dict[str, str], apply: bool = False) -> dict:
    results = []
    for name, incoming in rendered.items():
        path = repo / name
        if path.is_symlink():
            results.append({"file": name, "status": "preserved_symlink"})
            continue
        if path.exists() and not path.is_file():
            results.append({"file": name, "status": "preserved_nonfile"})
            continue
        try:
            original = path.read_bytes() if path.exists() else None
        except OSError:
            results.append({"file": name, "status": "preserved_unreadable"})
            continue
        try:
            text = original.decode("utf-8") if original is not None else ""
        except UnicodeDecodeError:
            results.append({"file": name, "status": "preserved_custom"})
            continue
        if original is None or legacy_template(text, name):
            desired = incoming
        elif text.count(BEGIN) == 1 and text.count(END) == 1 and text.index(BEGIN) < text.index(END):
            start, end = text.index(BEGIN), text.index(END) + len(END)
            desired = text[:start] + incoming.rstrip("\n") + text[end:]
        else:
            results.append({"file": name, "status": "preserved_custom",
                            "instruction": "Manually replace only the old CAO section using the installed template; retain project custom rules."})
            continue
        data = desired.encode("utf-8")
        if data == original:
            results.append({"file": name, "status": "unchanged"})
            continue
        item = {"file": name, "status": "updated" if apply else "preview", "content": desired}
        if apply:
            # Recheck before mutation. Never follow project instruction symlinks.
            if path.is_symlink() or (path.read_bytes() if path.exists() else None) != original:
                raise RuntimeError("PROJECT_INSTRUCTIONS_CHANGED: rerun the preview")
            if original is not None:
                backup = repo / ".orchestrator/backups/instructions" / uuid.uuid4().hex / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(original)
                item["backup"] = str(backup)
            fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=repo)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                if path.exists():
                    os.chmod(temporary, path.stat().st_mode)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        results.append(item)
    return {"ok": True, "applied": apply, "files": results,
            "next": "Restart the host to load updated project instructions." if apply else "Review content, then rerun with --apply. Custom files are never replaced automatically."}
