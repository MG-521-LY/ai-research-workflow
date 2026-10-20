"""Install the topic addon for Codex and DSH; back up and add a topic router to InstSci."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "instsci-topic-library"
BACKUPS = ROOT / "runtime" / "installation-backups"
MARKER = "<!-- instsci-topic-library routing -->"
ROUTER = """

<!-- instsci-topic-library routing -->
## 从研究主题收集指定篇数

用户从自然语言研究主题开始，要求阅读摘要筛选、按成功篇数补位或中途调整目标时，先加载已安装的 `instsci-topic-library` 技能，使用其候选评审、批次预约及原始清单复核。全文获取和出版社证据要求继续遵循本技能。给定 DOI 的直接获取仍按本技能执行。
<!-- /instsci-topic-library routing -->
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(selected_hosts=("codex", "dsh")):
    # Validate the invoking environment before changing installed skills.
    import instsci
    import pymupdf
    python = Path(sys.executable).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = BACKUPS / stamp
    files = [p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    results = []
    # Preflight all destinations before changing either host.
    hosts = {host: Path.home() / ("." + host) / "skills" for host in selected_hosts}
    for host, skills_root in hosts.items():
        dest = skills_root / SOURCE.name
        base = skills_root / "instsci" / "SKILL.md"
        if dest.is_symlink() or any(p.is_symlink() for p in dest.rglob("*")):
            raise ValueError("Refusing a symlinked addon installation")
        if dest.exists() and not (dest / ".topic-install.json").is_file():
            raise ValueError(f"Unowned destination already exists: {dest}")
        if not base.is_file() or "name: instsci" not in base.read_text(encoding="utf-8"):
            raise ValueError(f"Install the base InstSci skill first: {base}")
    launcher = Path.home() / ".local" / "bin" / "instsci-topic.cmd"
    if launcher.exists() and str(ROOT).lower() not in launcher.read_text(encoding="ascii").lower():
        raise ValueError("An unrelated instsci-topic launcher already exists")
    for host, skills_root in hosts.items():
        dest = skills_root / SOURCE.name
        base = skills_root / "instsci" / "SKILL.md"
        backup = backup_root / host
        backup.mkdir(parents=True)
        shutil.copy2(base, backup / "instsci-SKILL.md")
        if dest.exists():
            # Copy individual files rather than move/delete a user's skill tree.
            for old in dest.rglob("*"):
                if old.is_file():
                    out = backup / SOURCE.name / old.relative_to(dest)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, out)
        hashes = {}
        for source in files:
            relative = source.relative_to(SOURCE)
            out = dest / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, out)
            assert digest(source) == digest(out)
            hashes[relative.as_posix()] = digest(source)
        (dest / ".topic-install.json").write_text(json.dumps({"source": str(SOURCE), "files": hashes}, indent=2), encoding="utf-8")
        before = base.read_text(encoding="utf-8")
        if MARKER not in before:
            base.write_text(before.rstrip() + ROUTER, encoding="utf-8")
        results.append({"host": host, "skill": str(dest), "files_verified": len(files), "backup": str(backup)})
    launcher.parent.mkdir(parents=True, exist_ok=True)
    script = SOURCE / "scripts" / "topic_library.py"
    if not python.is_file():
        raise ValueError("InstSci Python environment not found")
    launcher.write_text(f'@echo off\nset PYTHONUTF8=1\n"{python}" "{script}" %*\n', encoding="ascii")
    report = {"status": "installed", "hosts": results, "launcher": str(launcher)}
    (ROOT / "runtime" / "installation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", nargs="+", choices=("codex", "dsh"), default=["codex", "dsh"])
    args = parser.parse_args()
    print(json.dumps(install(tuple(dict.fromkeys(args.hosts))), ensure_ascii=False))
