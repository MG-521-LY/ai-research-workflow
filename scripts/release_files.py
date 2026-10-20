"""Enumerate publishable working-tree files while respecting Git ignore rules."""
from pathlib import Path
import subprocess


def public_files(root):
    """Include tracked files (even force-added secrets) and unignored new files."""
    root = Path(root).resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Release checks require Git and a Git checkout; clone the repository first") from None
    paths = set(result.stdout.decode("utf-8").split("\0")) - {""}
    return [root / name for name in sorted(paths)]
