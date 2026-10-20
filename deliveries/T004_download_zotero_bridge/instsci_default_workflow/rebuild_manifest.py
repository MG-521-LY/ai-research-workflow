
import sys, json
from pathlib import Path
sys.path.insert(0, r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge")
from instsci_default_workflow.manifest_builder import build_unified_manifest

MODULE_DIR = Path(r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow")
ten_man = json.loads((MODULE_DIR / "workspace/ten_core_analysis_manifest.json").read_text(encoding="utf-8"))
supp_man = json.loads((MODULE_DIR / "workspace/supp_task_analysis_manifest.json").read_text(encoding="utf-8"))
items = ten_man["items"] + supp_man["items"]

out_dest = MODULE_DIR / "unified_analysis_manifest.json"
manifest = build_unified_manifest(items, output_path=out_dest)
print("Finished building manifest! Total items:", manifest["total_items"])
