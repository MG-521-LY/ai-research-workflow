"""Refresh release file hashes, preserving original upstream/snapshot attribution."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from release_files import public_files

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Explicitly update the source manifest")
    args = parser.parse_args()
    path = ROOT / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    old_rows = {row["path"]: row for row in manifest["files"]}
    rows = []
    for file in public_files(ROOT):
        name = file.relative_to(ROOT).as_posix()
        if name == "source_manifest.json":
            continue
        if not file.is_file() or file.is_symlink():
            raise ValueError(f"Not a regular release file: {name}")
        content = file.read_bytes()
        previous = old_rows.get(name, {})
        row = {**previous, "path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        if previous and row["sha256"] != previous["sha256"]:
            row.setdefault("original_snapshot_sha256", previous["sha256"])
        rows.append(row)
    changed = rows != manifest["files"] or len(rows) != manifest["file_count_excluding_manifest"]
    if changed and args.write:
        manifest["files"] = rows
        manifest["file_count_excluding_manifest"] = len(rows)
        manifest["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": not changed or args.write, "changed": changed,
                      "written": changed and args.write, "file_count": len(rows)}))
    return 0 if not changed or args.write else 1


if __name__ == "__main__":
    raise SystemExit(main())
