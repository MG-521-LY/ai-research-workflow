
from pathlib import Path
import sys, importlib.util

SOURCE = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py")
spec = importlib.util.spec_from_file_location("runner", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

MF = Path(r"E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\ten_paper_real_ingest\papers_manifest.json")
OUT = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\thrust_allocation_10_integrated_gemini38")
CFG = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\gemini_proxy_backend.yaml")
Q = "水下机器人（ROV/AUV）在过驱动与推力饱和多重约束下的推力分配主流优化算法（加权伪逆、二次规划QP、模型预测控制MPC）、推进器故障容错重构机制及实时性工程实现路径是什么？"

pipeline = m.ResearchPipeline(
    manifest_path=MF,
    question=Q,
    output_dir=OUT,
    backend="paperqa_native",
    config_path=CFG,
    resume=True
)
norm_manifest, mapping_data = pipeline.step1_prepare_corpus()
qa_res = pipeline.step2_retrieval_and_qa(norm_manifest, mapping_data)
cards = pipeline.step3_single_paper_cards(norm_manifest, mapping_data)
matrix = pipeline.step4_cross_paper_matrix(norm_manifest, cards)
hypotheses = pipeline.step5_hypotheses_generation(cards, matrix)
critique = pipeline.step6_external_search_critique(hypotheses)
plan = pipeline.step7_verification_plan(hypotheses, critique)
audit = pipeline.step8_evidence_cross_check(norm_manifest, qa_res, mapping_data)
final_report = pipeline.step9_synthesis_report(norm_manifest, qa_res, cards, matrix, hypotheses, critique, plan, audit)

unverified = audit.get("unverified_or_insufficient_claims", []) if isinstance(audit, dict) else []
stage_statuses = [v.get("status") for v in pipeline.state.get("stages", {}).values()]
has_failed = any(s == "failed" for s in stage_statuses)
has_pending = any(s == "pending" for s in stage_statuses)
overall_status = "partial" if (has_failed or has_pending) else "completed"
pipeline.update_run_result(status=overall_status, report_path=final_report, errors=unverified)
print("SUCCESSFULLY_UPDATED_REPORT:", final_report)
print("OVERALL_STATUS:", overall_status)
