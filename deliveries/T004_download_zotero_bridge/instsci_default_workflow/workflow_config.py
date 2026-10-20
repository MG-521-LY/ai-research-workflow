#!/usr/bin/env python3
"""Configuration constants for InstSci Default Workflow."""

from __future__ import annotations

import os
from pathlib import Path

# Paths
MODULE_DIR = Path(__file__).resolve().parent
T004_DIR = MODULE_DIR.parent
PROJECT_ROOT = T004_DIR.parent.parent

# Upstream topic-library script
TOPIC_SCRIPT = PROJECT_ROOT / "InstSCI-topic-library" / "skills" / "instsci-topic-library" / "scripts" / "topic_library.py"

# Zotero defaults
ZOTERO_CONNECTOR_URL = os.environ.get("ZOTERO_CONNECTOR_URL", "http://127.0.0.1:23119/connector/import")
ZOTERO_CONNECTOR_PING = os.environ.get("ZOTERO_CONNECTOR_PING", "http://127.0.0.1:23119/connector/ping")
ZOTERO_DEFAULT_DB = Path(os.environ.get("ZOTERO_DB", str(Path.home() / "Zotero" / "zotero.sqlite")))
ZOTERO_DEFAULT_STORAGE = Path(os.environ.get("ZOTERO_STORAGE", str(Path.home() / "Zotero" / "storage")))

# Default working directories
DEFAULT_WORKSPACE = MODULE_DIR / "workspace"
DEFAULT_TASK_DIR = DEFAULT_WORKSPACE / "active_topic_task"
DEFAULT_BRIDGE_DIR = DEFAULT_WORKSPACE / "bridge_run"
DEFAULT_MANIFEST_PATH = MODULE_DIR / "unified_analysis_manifest.json"

# Schema constants
UNIFIED_MANIFEST_SCHEMA = "instsci.unified_analysis_manifest.v1"
SCHEMA_VERSION = "1.0.0"
