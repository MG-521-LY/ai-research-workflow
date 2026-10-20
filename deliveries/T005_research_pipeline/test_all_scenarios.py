import json
import tempfile
import shutil
import traceback
import urllib.request
import urllib.error
from pathlib import Path
import importlib.util

SOURCE = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py")
spec = importlib.util.spec_from_file_location("runner", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

base_manifest = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\representative_instsci_manifest.json")
config_file = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\gemini_proxy_backend.yaml")
rep_run_dir = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\instsci_representative_5_run")

print("=== SCENARIO 1: SAME INPUT RESUME ===")
pipeline1 = m.ResearchPipeline(manifest_path=base_manifest, question=None, output_dir=rep_run_dir, resume=True, backend="paperqa_native", config_path=config_file)
ret1 = pipeline1.run()
assert ret1 == 0, f"Same input resume failed with exit code {ret1}"
print("PASS: Same input resume hits 100% cache instantly with exit code 0.")

print("=== SCENARIO 2: QUESTION / TOPIC CHANGE INVALIDATION ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    shutil.copytree(rep_run_dir, tmp_path / "run_q_diff", dirs_exist_ok=True)
    pipeline2 = m.ResearchPipeline(
        manifest_path=base_manifest,
        question="一个完全不同的深海机器人能源管理与推进调度控制新问题？",
        output_dir=tmp_path / "run_q_diff",
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    assert not pipeline2.is_stage_done("retrieval_and_qa"), "retrieval_and_qa MUST be invalidated on question change"
    assert not pipeline2.is_stage_done("hypotheses_generation"), "hypotheses MUST be invalidated on question change"
    assert not pipeline2.is_stage_done("synthesis_report"), "synthesis_report MUST be invalidated on question change"
    print("PASS: Question change correctly invalidates retrieval, hypotheses, and report!")

print("=== SCENARIO 3: AUDIT CORRECTION FILE CHANGE INVALIDATION ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    shutil.copytree(rep_run_dir, tmp_path / "run_audit_diff", dirs_exist_ok=True)
    pipeline3 = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=tmp_path / "run_audit_diff",
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    audit_file = tmp_path / "run_audit_diff" / "evidence_cross_check.json"
    audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
    audit_data["target_faulty_claim"] = "测试人工发现的新事实冲突与概念混淆"
    audit_file.write_text(json.dumps(audit_data), encoding="utf-8")
    
    assert not pipeline3.is_stage_done("cross_paper_matrix"), "Matrix MUST be invalidated when audit changes"
    assert not pipeline3.is_stage_done("hypotheses_generation"), "Hypotheses MUST be invalidated when audit changes"
    assert not pipeline3.is_stage_done("external_search_critique"), "Critique MUST be invalidated when audit changes"
    assert not pipeline3.is_stage_done("verification_plan"), "Plan MUST be invalidated when audit changes"
    assert not pipeline3.is_stage_done("synthesis_report"), "Report MUST be invalidated when audit changes"
    print("PASS: Audit correction file change strictly invalidates all 5 downstream stages!")

print("=== SCENARIO 4: CORRUPTED / MISSING STAGE_INPUT_HASH ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    shutil.copytree(rep_run_dir, tmp_path / "run_corrupt_hash", dirs_exist_ok=True)
    st_file = tmp_path / "run_corrupt_hash" / "pipeline_state.json"
    st_data = json.loads(st_file.read_text(encoding="utf-8"))
    st_data["stages"]["hypotheses_generation"]["stage_input_hash"] = None
    st_file.write_text(json.dumps(st_data), encoding="utf-8")
    
    pipeline4 = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=tmp_path / "run_corrupt_hash",
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    assert not pipeline4.is_stage_done("hypotheses_generation"), "Missing hash MUST reject stage as done"
    print("PASS: Missing stage_input_hash strictly rejects stage completion!")

print("=== SCENARIO 5: FAILED STATUS LAYERED SEMANTICS ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    pipeline5 = m.ResearchPipeline(manifest_path=base_manifest, question="问题", output_dir=tmp_path, backend="paperqa_native")
    for i in range(9):
        pipeline5.state["stages"][str(i)] = {"status": "completed"}
    pipeline5.update_run_result(status="failed", errors=["unresolved evidence conflict"])
    res5 = json.loads((tmp_path / "run_result.json").read_text(encoding="utf-8"))
    assert res5["status"] == "failed"
    assert res5["execution_status"] == "FAILED"
    assert res5["evidence_status"] == "UNVERIFIED_DUE_TO_EXECUTION_FAILURE"
    assert res5["research_maturity"] == "INCOMPLETE_DRAFT"
    print("PASS: Failed status strictly cascades to execution FAILED and unverified evidence!")

print("=== SCENARIO 6: REFUSAL & PARTIAL STATUS HONEST REPORT & EXIT CODE ===")
held_run_dir = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\held_out_supp_2_run")
held_manifest = Path(r"E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\held_out_supp_manifest.json")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    # 复制已有前置工件与分块，保留真实 step1 与下游报告渲染器
    shutil.copytree(held_run_dir, tmp_path / "run_refusal_test", dirs_exist_ok=True)
    pipeline6 = m.ResearchPipeline(
        manifest_path=held_manifest,
        question="一个完全缺乏证据支持的跨界问题",
        output_dir=tmp_path / "run_refusal_test",
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    
    # 在受控模型边界注入真实拒答/失败响应，下游重型步骤用完成替身隔离，验证未修改的生产 run() 核心驱动行为
    def mock_refusal_qa(norm_m, mapping):
        qa_mock = {
            "is_refusal": True,
            "model_answer": "I cannot answer. The provided context does not contain sufficient evidence.",
            "raw_answer": "I cannot answer.",
            "retrieved_evidence": [],
            "contexts": []
        }
        pipeline6.save_state("retrieval_and_qa", "failed", {"is_refusal": True})
        return qa_mock
    
    def fast_stage(name, val):
        def _f(*args, **kwargs):
            pipeline6.save_state(name, "completed", {})
            return val
        return _f
    
    pipeline6.step2_retrieval_and_qa = mock_refusal_qa
    pipeline6.step8_evidence_cross_check = fast_stage("evidence_cross_check", {
        "has_discrepancy": False,
        "harmonized_replacement_text": "",
        "fact_vs_inference_distinction": [],
        "unverified_or_insufficient_claims": ["拒答状态无待核验证据"]
    })
    pipeline6.step4_cross_paper_matrix = fast_stage("cross_paper_matrix", {})
    pipeline6.step5_hypotheses_generation = fast_stage("hypotheses_generation", [])
    pipeline6.step6_external_search_critique = fast_stage("external_search_critique", {})
    pipeline6.step7_verification_plan = fast_stage("verification_plan", {"experiment_title": "Test Plan"})
    # 清理旧完成态以确保驱动执行下游真实报告渲染
    pipeline6.state["stages"]["synthesis_report"]["status"] = "pending"
    
    # 调用未修改的生产驱动 run() 并验证真实返回码
    real_exit_code = pipeline6.run()
    assert real_exit_code == 1, f"Production run() MUST return exit code 1 on failed QA stage, got {real_exit_code}"
    
    res6 = json.loads((tmp_path / "run_refusal_test" / "run_result.json").read_text(encoding="utf-8"))
    assert res6["execution_status"] == "FAILED"
    assert res6["status"] == "failed", f"Expected status failed on refusal run, got {res6.get('status')}"
    
    rep_txt = (tmp_path / "run_refusal_test" / "RESEARCH_SYNTHESIS_REPORT.md").read_text(encoding="utf-8")
    assert "FAILED" in rep_txt, "Report MUST contain FAILED when QA failed"
    assert "UNVERIFIED_DUE_TO_EXECUTION_FAILURE" in rep_txt, "Report MUST state UNVERIFIED_DUE_TO_EXECUTION_FAILURE"
    assert "INCOMPLETE_DRAFT" in rep_txt, "Report MUST state INCOMPLETE_DRAFT"
    assert "COMPLETED" not in rep_txt, "Report MUST NOT claim COMPLETED when QA failed"
    assert "LS-QP" not in rep_txt, "Report MUST NOT inject old sample LS-QP"
    assert "SS-QP" not in rep_txt, "Report MUST NOT inject old sample SS-QP"
    assert "sha_cdda" not in rep_txt, "Report MUST NOT inject old sample sha_cdda"
    print("PASS: Production run() with injected refusal strictly returns exit code 1, report & contract state FAILED!")

print("=== SCENARIO 7: INPUT PRECHECK FOR MISSING PDFS (EARLY REJECTION) ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    bad_manifest_path = tmp_path / "all_missing_manifest.json"
    bad_data = {
        "topic": "完全缺失PDF早停测试",
        "question": "测试空语料是否会在昂贵调用前终止",
        "items": [
            {"paper_id": "p_fake_1", "title": "Fake 1", "pdf_path": str(tmp_path / "no_such_1.pdf")},
            {"paper_id": "p_fake_2", "title": "Fake 2", "pdf_path": str(tmp_path / "no_such_2.pdf")}
        ]
    }
    bad_manifest_path.write_text(json.dumps(bad_data), encoding="utf-8")
    p_bad = m.ResearchPipeline(
        manifest_path=bad_manifest_path,
        question=bad_data["question"],
        output_dir=tmp_path / "bad_out",
        backend="paperqa_native"
    )
    ret_bad = p_bad.run()
    assert ret_bad == 1, f"Expected exit code 1 on missing PDFs, got {ret_bad}"
    
    res_bad = json.loads((tmp_path / "bad_out" / "run_result.json").read_text(encoding="utf-8"))
    assert res_bad["execution_status"] == "FAILED"
    assert res_bad["stages_status"]["manifest_and_corpus"] == "failed"
    assert not (tmp_path / "bad_out" / "candidate_hypotheses.json").exists()
    assert not (tmp_path / "bad_out" / "external_search_critique.json").exists()
    assert not (tmp_path / "bad_out" / "RESEARCH_SYNTHESIS_REPORT.md").exists()
    print("PASS: Missing PDFs rejected early with exit code 1 and zero expensive model calls!")

print("=== SCENARIO 8: PARTIAL INPUT GAPS PROPAGATION & COMPLEMENT INGESTION ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    # 步骤 A: 构造部分缺失清单 (1篇有效 + 1篇缺失)
    held_manifest_raw = json.loads(held_manifest.read_text(encoding="utf-8"))
    valid_item = held_manifest_raw["items"][0]
    missing_pdf_file = tmp_path / "absent_probe.pdf"
    
    partial_manifest_file = tmp_path / "partial_manifest.json"
    partial_data = {
        "question": "测试部分文献缺失时契约与报告是否如实记录来源缺口，并在补齐后正确重算",
        "items": [
            valid_item,
            {
                "paper_id": "probe_missing_002",
                "title": "Probe Missing Paper",
                "pdf_path": str(missing_pdf_file)
            }
        ]
    }
    partial_manifest_file.write_text(json.dumps(partial_data), encoding="utf-8")
    
    out_dir = tmp_path / "partial_out"
    pipeline8 = m.ResearchPipeline(
        manifest_path=partial_manifest_file,
        question=partial_data["question"],
        output_dir=out_dir,
        backend="paperqa_native",
        config_path=config_file
    )
    
    # 用隔离边界替代下游模型调用，保留真实 step1、run()、update_run_result 与报告渲染器
    def make_mock_stage(target_pipeline, name, val):
        def _f(*args, **kwargs):
            target_pipeline.save_state(name, "completed", {})
            return val
        return _f
    
    pipeline8.step2_retrieval_and_qa = make_mock_stage(pipeline8, "retrieval_and_qa", {
        "model_answer": "MOCK ANSWER FOR PARTIAL TEST",
        "raw_answer": "MOCK ANSWER FOR PARTIAL TEST",
        "is_refusal": False,
        "contexts": [],
        "retrieved_evidence": []
    })
    pipeline8.step3_single_paper_cards = make_mock_stage(pipeline8, "single_paper_cards", {})
    pipeline8.step8_evidence_cross_check = make_mock_stage(pipeline8, "evidence_cross_check", {})
    pipeline8.step4_cross_paper_matrix = make_mock_stage(pipeline8, "cross_paper_matrix", {})
    pipeline8.step5_hypotheses_generation = make_mock_stage(pipeline8, "hypotheses_generation", [])
    pipeline8.step6_external_search_critique = make_mock_stage(pipeline8, "external_search_critique", {})
    pipeline8.step7_verification_plan = make_mock_stage(pipeline8, "verification_plan", {"experiment_title": "Partial test plan"})
    
    code8 = pipeline8.run()
    assert code8 == 1, f"Partial input pipeline run MUST return exit code 1, got {code8}"
    
    rr8 = json.loads((out_dir / "run_result.json").read_text(encoding="utf-8"))
    assert rr8["execution_status"] == "PARTIAL", f"Expected PARTIAL execution_status, got {rr8.get('execution_status')}"
    assert rr8["evidence_status"] == "VERIFIED_WITH_PARTIAL_SOURCE_GAPS"
    assert rr8["corpus_integrity"]["has_source_conflicts"] is True
    assert rr8["corpus_integrity"]["source_conflicts_count"] == 1
    assert "probe_missing_002" in json.dumps(rr8), "Missing paper_id MUST be recorded in contract"
    
    rep8_txt = (out_dir / "RESEARCH_SYNTHESIS_REPORT.md").read_text(encoding="utf-8")
    assert "probe_missing_002" in rep8_txt, "Missing paper_id MUST be recorded in report"
    assert "来源缺口/冲突排除文献数" in rep8_txt, "Report MUST have source conflict audit"
    print("PASS: Partial input successfully isolated, recorded in contract and report, and exited with 1.")
    
    # 步骤 B: 补齐缺失文献 PDF，验证缓存失效并全量完成
    # 将现有的第二篇有效 PDF 复制到 missing_pdf_file 路径
    second_valid_pdf = Path(held_manifest_raw["items"][1]["pdf_path"])
    shutil.copyfile(second_valid_pdf, missing_pdf_file)
    assert missing_pdf_file.exists()
    
    # 再次创建并运行 pipeline8，验证输入哈希变化使 manifest_and_corpus 失效重算
    pipeline8_resumed = m.ResearchPipeline(
        manifest_path=partial_manifest_file,
        question=partial_data["question"],
        output_dir=out_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    assert not pipeline8_resumed.is_stage_done("manifest_and_corpus"), "Stage 1 MUST be invalidated when missing PDF is supplied!"
    
    pipeline8_resumed.step2_retrieval_and_qa = make_mock_stage(pipeline8_resumed, "retrieval_and_qa", {
        "model_answer": "MOCK ANSWER FOR RECOVERED TEST",
        "raw_answer": "MOCK ANSWER FOR RECOVERED TEST",
        "is_refusal": False,
        "contexts": [],
        "retrieved_evidence": []
    })
    pipeline8_resumed.step3_single_paper_cards = make_mock_stage(pipeline8_resumed, "single_paper_cards", {})
    pipeline8_resumed.step8_evidence_cross_check = make_mock_stage(pipeline8_resumed, "evidence_cross_check", {})
    pipeline8_resumed.step4_cross_paper_matrix = make_mock_stage(pipeline8_resumed, "cross_paper_matrix", {})
    pipeline8_resumed.step5_hypotheses_generation = make_mock_stage(pipeline8_resumed, "hypotheses_generation", [])
    pipeline8_resumed.step6_external_search_critique = make_mock_stage(pipeline8_resumed, "external_search_critique", {})
    pipeline8_resumed.step7_verification_plan = make_mock_stage(pipeline8_resumed, "verification_plan", {"experiment_title": "Recovered plan"})
    
    code8_resumed = pipeline8_resumed.run()
    assert code8_resumed == 0, f"Recovered pipeline run MUST return exit code 0, got {code8_resumed}"
    rr8_resumed = json.loads((out_dir / "run_result.json").read_text(encoding="utf-8"))
    assert rr8_resumed["execution_status"] == "COMPLETED"
    assert rr8_resumed["corpus_integrity"]["has_source_conflicts"] is False
    assert rr8_resumed["corpus_integrity"]["valid_papers_loaded"] == 2
    print("PASS: Ingestion of missing file correctly invalidated cache, completed fully, and returned 0!")

print("=== SCENARIO 9: ALL EXTERNAL SEARCH NETWORK FAILURES & RECOVERY ON RESTORATION ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    run_dir = tmp_path / "run_sc9"
    shutil.copytree(rep_run_dir, run_dir, dirs_exist_ok=True)

    # Invalidate step 6 and downstream to force real execution of external search
    st_file = run_dir / "pipeline_state.json"
    st_data = json.loads(st_file.read_text(encoding="utf-8"))
    st_data["stages"]["external_search_critique"]["status"] = "pending"
    st_data["stages"]["verification_plan"]["status"] = "pending"
    st_data["stages"]["synthesis_report"]["status"] = "pending"
    st_file.write_text(json.dumps(st_data), encoding="utf-8")

    orig_urlopen = urllib.request.urlopen
    def mock_fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("Simulated Network Outage")

    urllib.request.urlopen = mock_fail_urlopen
    def mock_llm(messages, **kwargs):
        prompt_str = str(messages)
        if "三阶段递进式" in prompt_str or "phase_1" in prompt_str:
            return {"content": json.dumps({
                "experiment_title": "Mock Plan",
                "plan_design_philosophy": "Mock philosophy",
                "phase_1_software_simulation_and_benchmarking": {"status": "ready_to_implement", "concrete_tasks": []},
                "phase_2_ablation_and_sensitivity_study": {"status": "ready_to_implement"},
                "phase_3_unconfirmed_physical_and_hardware_in_the_loop": {"status": "resources_unconfirmed_future_work"},
                "benchmarks_and_baselines": [],
                "evaluation_metrics": [],
                "falsification_criteria": [],
                "major_risks_and_mitigation": []
            })}
        return {"content": "Mock Red Team Critique under network failure"}

    try:
        p9_fail = m.ResearchPipeline(
            manifest_path=base_manifest,
            question=None,
            output_dir=run_dir,
            resume=True,
            backend="paperqa_native",
            config_path=config_file
        )
        p9_fail.call_llm = mock_llm
        ret9_fail = p9_fail.run()
        assert ret9_fail == 1, f"Expected 1 on all external network failures, got {ret9_fail}"

        rr_fail = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
        assert rr_fail["execution_status"] == "PARTIAL", f"Expected PARTIAL, got {rr_fail.get('execution_status')}"
        assert rr_fail["evidence_status"] == "UNVERIFIED_DUE_TO_EXTERNAL_SEARCH_FAILURE"
        assert rr_fail["stages_status"]["external_search_critique"] == "partial"

        crit_data = json.loads((run_dir / "external_search_critique.json").read_text(encoding="utf-8"))
        assert crit_data["transport_status"] == "all_failed"
        assert crit_data["successful_transports"] == 0
        assert crit_data["failed_transports"] >= 3
        assert "已完成已知基线方法核对" not in crit_data["unresolved_gap_reason"]
        assert "网络传输失败" in crit_data["unresolved_gap_reason"]

        rep_fail = (run_dir / "RESEARCH_SYNTHESIS_REPORT.md").read_text(encoding="utf-8")
        assert "已完成已知基线方法核对" not in rep_fail
        assert "外部学术与代码库检索全部网络中断/失败" in rep_fail
        print("PASS: Part A (Network Failure) correctly sets status partial, honest gap reason, exit code 1!")
    finally:
        urllib.request.urlopen = orig_urlopen

    # Part B: Network Restoration and Stage Reuse
    def mock_success_urlopen(url_or_req, *args, **kwargs):
        class MockResp:
            def __init__(self, data):
                self.data = data
            def read(self):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        req_url = url_or_req.full_url if hasattr(url_or_req, "full_url") else str(url_or_req)
        if "arxiv" in req_url:
            xml = b'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1</id><title>Mock Thrust Allocation Preprint</title><summary>A mock paper on allocation.</summary><published>2021-01-01T00:00:00Z</published></entry></feed>'
            return MockResp(xml)
        elif "crossref" in req_url:
            js = json.dumps({"message": {"items": [{"title": ["Mock CrossRef Paper"], "URL": "https://doi.org/10.1000/182", "created": {"date-time": "2021-01-01T00:00:00Z"}}]}}).encode("utf-8")
            return MockResp(js)
        elif "github" in req_url:
            js = json.dumps({"items": [{"full_name": "mock_org/mock_allocation_repo", "html_url": "https://github.com/mock_org/mock_allocation_repo", "description": "Mock allocation repo", "stargazers_count": 42, "default_branch": "main"}]}).encode("utf-8")
            return MockResp(js)
        return MockResp(b"{}")

    urllib.request.urlopen = mock_success_urlopen
    try:
        p9_restore = m.ResearchPipeline(
            manifest_path=base_manifest,
            question=None,
            output_dir=run_dir,
            resume=True,
            backend="paperqa_native",
            config_path=config_file
        )
        assert p9_restore.is_stage_done("manifest_and_corpus") is True
        assert p9_restore.is_stage_done("retrieval_and_qa") is True
        assert p9_restore.is_stage_done("single_paper_cards") is True
        assert p9_restore.is_stage_done("evidence_cross_check") is True
        assert p9_restore.is_stage_done("cross_paper_matrix") is True
        assert p9_restore.is_stage_done("hypotheses_generation") is True
        assert p9_restore.is_stage_done("external_search_critique") is False

        p9_restore.call_llm = mock_llm
        ret9_restore = p9_restore.run()
        assert ret9_restore == 0, f"Expected 0 on restored network, got {ret9_restore}"

        rr_res = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
        assert rr_res["execution_status"] == "COMPLETED"
        assert rr_res["status"] == "completed"
        assert rr_res["stages_status"]["external_search_critique"] == "completed"

        crit_res = json.loads((run_dir / "external_search_critique.json").read_text(encoding="utf-8"))
        assert crit_res["transport_status"] == "all_success"
        assert crit_res["successful_transports"] >= 3
        assert crit_res["failed_transports"] == 0

        rep_res = (run_dir / "RESEARCH_SYNTHESIS_REPORT.md").read_text(encoding="utf-8")
        assert "COMPLETED" in rep_res
        assert "VERIFIED_WITH_EXPLICIT_BOUNDARIES" in rep_res
        print("PASS: Part B (Network Restoration & Stage Reuse) verified completely with exit code 0!")
    finally:
        urllib.request.urlopen = orig_urlopen


print("\n=== SCENARIO 10: REAL DRIVER REGRESSION: VALID QA ANSWER TRANSITION TO REFUSAL PURGES STALE AUDIT & EXITS 1 ===")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    run_dir = tmp_path / "run_sc10"
    shutil.copytree(rep_run_dir, run_dir, dirs_exist_ok=True)
    
    # 1. 验证基线状态：快照具备已完成的审计与 >0 的有效引用覆盖
    audit_file = run_dir / "evidence_cross_check.json"
    audit_pre = json.loads(audit_file.read_text(encoding="utf-8"))
    assert audit_pre.get("resolved_refs_count", 0) > 0, "Baseline audit should have citations"
    assert audit_pre.get("coverage_percentage", 0.0) == 100.0
    
    # 2. 模拟真实不一致输入（即 04:30 Codex 反例）：
    # QA 产物为真实拒答，但 pipeline_state 中 status仍为 completed、is_refusal=False
    qa_file = run_dir / "retrieval_qa_result.json"
    qa_data = json.loads(qa_file.read_text(encoding="utf-8"))
    qa_data["model_answer"] = "I cannot answer."
    qa_data["raw_answer"] = "I cannot answer."
    qa_data["is_refusal"] = True
    qa_file.write_text(json.dumps(qa_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    st_file = run_dir / "pipeline_state.json"
    st_data = json.loads(st_file.read_text(encoding="utf-8"))
    st_data["stages"]["retrieval_and_qa"]["status"] = "completed"
    st_data["stages"]["retrieval_and_qa"]["is_refusal"] = False
    st_file.write_text(json.dumps(st_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # 3. 构造流水线并仅在外部模型边界注入隔离，不手工篡改中间阶段状态，走完整 run() 默认驱动！
    pipeline10 = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    
    def mock_boundary_llm(messages, **kwargs):
        prompt_str = str(messages)
        if "三阶段递进式" in prompt_str or "phase_1" in prompt_str:
            return {"content": json.dumps({
                "experiment_title": "Mock Plan on Refusal",
                "plan_design_philosophy": "Mock philosophy",
                "phase_1_software_simulation_and_benchmarking": {"status": "ready_to_implement", "concrete_tasks": []},
                "phase_2_ablation_and_sensitivity_study": {"status": "ready_to_implement"},
                "phase_3_unconfirmed_physical_and_hardware_in_the_loop": {"status": "resources_unconfirmed_future_work"},
                "benchmarks_and_baselines": [],
                "evaluation_metrics": [],
                "falsification_criteria": [],
                "major_risks_and_mitigation": []
            })}
        return {"content": json.dumps({
            "has_discrepancy": False,
            "target_faulty_claim": "",
            "harmonized_replacement_text": "",
            "fact_vs_inference_distinction": [],
            "unverified_or_insufficient_claims": []
        })}
    pipeline10.call_llm = mock_boundary_llm
    
    # 4. 执行真实默认驱动 run()
    exit_code10 = pipeline10.run()
    assert exit_code10 == 1, f"Default driver on refusal run MUST return exit code 1, got {exit_code10}"
    
    # 5. 断言生产机制已自动将 QA 阶段更正为 failed 且 is_refusal=True
    qa_stage_final = pipeline10.state["stages"]["retrieval_and_qa"]
    assert qa_stage_final["status"] == "failed", f"Expected QA status failed, got {qa_stage_final.get('status')}"
    assert qa_stage_final["is_refusal"] is True, "QA stage must have is_refusal=True"
    
    # 6. 断言当前审计已彻底清零且无残留
    audit_post = json.loads((run_dir / "evidence_cross_check.json").read_text(encoding="utf-8"))
    assert audit_post["total_identified_refs"] == 0, f"Expected 0 refs, got {audit_post.get('total_identified_refs')}"
    assert audit_post["resolved_refs_count"] == 0
    assert audit_post["coverage_percentage"] == 0.0
    assert len(audit_post.get("fact_vs_inference_distinction", [])) == 0
    
    # 7. 断言契约状态全链一致
    rr10 = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
    assert rr10["status"] == "failed", f"Expected status failed, got {rr10.get('status')}"
    assert rr10["execution_status"] == "FAILED"
    assert rr10["evidence_status"] == "UNVERIFIED_DUE_TO_EXECUTION_FAILURE"
    assert rr10["research_maturity"] == "INCOMPLETE_DRAFT"
    assert rr10["execution_finished"] is True
    assert rr10["stages_status"]["retrieval_and_qa"] == "failed"
    
    # 8. 断言报告严格净化：不得残留 100.0% 或 14/14，明确记录拒答与 0 (not_applicable) 引用
    rep_txt10 = (run_dir / "RESEARCH_SYNTHESIS_REPORT.md").read_text(encoding="utf-8")
    assert "问答证据不足与拒答事实记录" in rep_txt10
    assert "0 (not_applicable)" in rep_txt10
    assert "100.0%" not in rep_txt10, "Report MUST NOT retain stale 100.0% coverage"
    assert "14/14" not in rep_txt10, "Report MUST NOT retain stale 14/14 citations"
    assert "FAILED" in rep_txt10
    assert "UNVERIFIED_DUE_TO_EXECUTION_FAILURE" in rep_txt10
    
    # 9. 构造器重读幂等性校验：再次实例化构造器，确认不会将契约重置回 running
    p_check = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    rr10_check = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
    assert rr10_check["status"] == "failed"
    assert rr10_check["execution_status"] == "FAILED"
    
    print("PASS: Scenario 10 real driver regression completely passed with status failed, exit code 1, clean report, and constructor idempotency!")

print("=== ALL 10 SCENARIOS FULLY PASSED ===")