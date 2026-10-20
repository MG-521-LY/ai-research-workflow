"""
test_unified_caching_and_refusal.py

覆盖用户与 05:00 审查明确要求的 7 大缓存与拒答核心回归场景：
1. 同输入有效 cache：秒级复用，模型边界调用 = 0，阶段保持 completed。
2. 同输入拒答且 metadata 失配（如 state 中为 completed / is_refusal=False）：
   诚实复用拒答事实，自动纠偏阶段为 failed 且 is_refusal=True，模型边界调用 = 0。
3. 换 question：缓存严格失效，必须越过旧缓存进入模型边界，记录实际模型调用 >= 1。
4. 换 PDF / 关键配置：stage_input_hash 变更，缓存严格失效，记录实际模型调用 >= 1。
5. no-resume (resume=False)：强制新运行，完全越过已有缓存，记录实际模型调用 >= 1。
6. 失败后显式重试恢复：当先前阶段失败或重试时，调用模型边界获得有效回答，阶段由 failed 恢复为 completed。
7. 有效回答含局部缺口措辞：长篇学术回答中出现 'could not find 某算法实现' 或 '部分信息不足'，
   严谨识别为诚实的局部学术缺口说明，绝不误判为整答拒绝/失败，阶段保持 completed。

全量测试均在断网与受控模型边界下执行，记录实际模型调用次数，杜绝仅断言 is_stage_done 或手算状态。
"""

import sys
import os
import json
import shutil
import tempfile
import socket
from pathlib import Path

# 拦截真实外部网络连接，确保测试 100% 离线安全
orig_getaddrinfo = socket.getaddrinfo
def mock_getaddrinfo(*args, **kwargs):
    raise socket.gaierror("Network strictly blocked in isolated regression")
socket.getaddrinfo = mock_getaddrinfo

import importlib.util
runner_path = Path(__file__).parent / "research_runner.py"
spec = importlib.util.spec_from_file_location("runner", runner_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

base_manifest = runner_path.parent / "configs" / "held_out_supp_manifest.json"
config_file = runner_path.parent / "configs" / "gemini_proxy_backend.yaml"
base_run_dir = runner_path.parent / "runs" / "held_out_supp_2_run"

print("======================================================================")
print("开始执行统一缓存契约与拒答适用性 7 大场景回归测试...")
print("======================================================================")

# ----------------------------------------------------------------------
# 场景 1: 同输入有效 cache (零模型调用，保持 completed)
# ----------------------------------------------------------------------
print("\n[SCENARIO 1] 同输入有效 cache 复用...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s1"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    # 构建同输入有效完成态
    qa_path = run_dir / "retrieval_qa_result.json"
    qa_data = json.loads(qa_path.read_text(encoding="utf-8"))
    qa_data["model_answer"] = "This is a valid comprehensive answer on thrust allocation."
    qa_data["raw_answer"] = "This is a valid comprehensive answer on thrust allocation."
    qa_data["is_refusal"] = False
    qa_path.write_text(json.dumps(qa_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    # 将 state 标记为对应 input_hash 的 completed
    expected_hash = pipeline.compute_stage_input_hash("retrieval_and_qa")
    pipeline.state["stages"]["retrieval_and_qa"] = {
        "status": "completed",
        "stage_input_hash": expected_hash,
        "is_refusal": False
    }
    pipeline.dump_state()
    
    model_calls = []
    import paperqa
    async def mock_aquery(*args, **kwargs):
        model_calls.append("aquery")
        return None
    paperqa.Docs.aquery = mock_aquery
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) == 0, f"Expected 0 model calls on valid cache hit, got {len(model_calls)}"
    assert qa_res["is_refusal"] is False
    assert pipeline.state["stages"]["retrieval_and_qa"]["status"] == "completed"
    print("  PASS: 场景 1 验证通过：同输入有效缓存零模型调用并保持 completed！")

# ----------------------------------------------------------------------
# 场景 2: 同输入拒答且 metadata 失配 (自动纠偏为 failed/is_refusal=True，零模型调用)
# ----------------------------------------------------------------------
print("\n[SCENARIO 2] 同输入拒答事实且 metadata 失配自动纠偏...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s2"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    qa_path = run_dir / "retrieval_qa_result.json"
    qa_data = json.loads(qa_path.read_text(encoding="utf-8"))
    qa_data["model_answer"] = "I cannot answer."
    qa_data["raw_answer"] = "I cannot answer."
    qa_data["is_refusal"] = True
    qa_path.write_text(json.dumps(qa_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    expected_hash = pipeline.compute_stage_input_hash("retrieval_and_qa")
    # 模拟 metadata 失配：原本产物是拒答，但历史 metadata 错误标为 completed / is_refusal=False
    pipeline.state["stages"]["retrieval_and_qa"] = {
        "status": "completed",
        "stage_input_hash": expected_hash,
        "is_refusal": False
    }
    pipeline.dump_state()
    
    model_calls = []
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) == 0, "Refusal cache hit must not call model"
    assert qa_res["is_refusal"] is True
    # 断言机制已自动同步为 failed 且 is_refusal=True
    assert pipeline.state["stages"]["retrieval_and_qa"]["status"] == "failed"
    assert pipeline.state["stages"]["retrieval_and_qa"]["is_refusal"] is True
    print("  PASS: 场景 2 验证通过：拒答事实被诚实复用且 metadata 自动纠偏为 failed！")

# ----------------------------------------------------------------------
# 场景 3: 换 question (严格失效，越过旧拒答，记录模型调用)
# ----------------------------------------------------------------------
print("\n[SCENARIO 3] 更换研究核心问题 (严格失效并触发模型边界)...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s3"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    new_q = "NEW QUESTION: How should feasible allocation be evaluated after a single thruster failure?"
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=new_q,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    
    model_calls = []
    import paperqa
    async def mock_aquery3(*args, **kwargs):
        model_calls.append("new_q_aquery")
        class MockSession:
            answer = "Detailed answer for new question."
            raw_answer = "Detailed answer for new question."
            contexts = []
            references = ""
        return MockSession()
    paperqa.Docs.aquery = mock_aquery3
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) >= 1, "Model boundary MUST be called when question changed!"
    assert qa_res["question"] == new_q
    assert qa_res["model_answer"] == "Detailed answer for new question."
    print("  PASS: 场景 3 验证通过：换问题严格越过旧拒答，成功进入模型边界！")

# ----------------------------------------------------------------------
# 场景 4: 换 PDF / 关键配置 (stage_input_hash 失效触发模型边界)
# ----------------------------------------------------------------------
print("\n[SCENARIO 4] 关键输入要素变更 (stage_input_hash 失效触发模型调用)...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s4"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    # 人工篡改缓存哈希模拟配置变更
    pipeline.state["stages"]["retrieval_and_qa"]["stage_input_hash"] = "stale_hash_from_older_model_config"
    pipeline.dump_state()
    
    model_calls = []
    async def mock_aquery4(*args, **kwargs):
        model_calls.append("config_changed_aquery")
        class MockSession:
            answer = "Answer under updated configuration."
            raw_answer = "Answer under updated configuration."
            contexts = []
            references = ""
        return MockSession()
    paperqa.Docs.aquery = mock_aquery4
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) >= 1, "Model boundary MUST be called when stage_input_hash mismatched!"
    assert qa_res["model_answer"] == "Answer under updated configuration."
    print("  PASS: 场景 4 验证通过：配置变更导致哈希失配，正确触发模型重跑！")

# ----------------------------------------------------------------------
# 场景 5: no-resume (resume=False 强制新运行并越过已有缓存)
# ----------------------------------------------------------------------
print("\n[SCENARIO 5] no-resume (resume=False 强制新运行并越过已有缓存)...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s5"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=False,
        backend="paperqa_native",
        config_path=config_file
    )
    
    model_calls = []
    async def mock_aquery5(*args, **kwargs):
        model_calls.append("no_resume_aquery")
        class MockSession:
            answer = "Answer on forced fresh run."
            raw_answer = "Answer on forced fresh run."
            contexts = []
            references = ""
        return MockSession()
    paperqa.Docs.aquery = mock_aquery5
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) >= 1, "resume=False MUST invoke model boundary!"
    assert qa_res["model_answer"] == "Answer on forced fresh run."
    print("  PASS: 场景 5 验证通过：resume=False 强制越过旧拒答并调用模型！")

# ----------------------------------------------------------------------
# 场景 6: 失败后显式重试恢复 (重试成功将状态由 failed 恢复为 completed)
# ----------------------------------------------------------------------
print("\n[SCENARIO 6] 失败后显式重试恢复...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s6"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    # 模拟前次运行处于 failed 状态
    st_file = run_dir / "pipeline_state.json"
    st = json.loads(st_file.read_text(encoding="utf-8"))
    st["stages"]["retrieval_and_qa"]["status"] = "failed"
    st["stages"]["retrieval_and_qa"]["is_refusal"] = True
    st_file.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # 清理掉旧拒答文件以触发显式重试
    (run_dir / "retrieval_qa_result.json").unlink(missing_ok=True)
    
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=True,
        backend="paperqa_native",
        config_path=config_file
    )
    
    model_calls = []
    async def mock_aquery6(*args, **kwargs):
        model_calls.append("retry_recovery_aquery")
        class MockSession:
            answer = "Recovered valid answer after retry."
            raw_answer = "Recovered valid answer after retry."
            contexts = []
            references = ""
        return MockSession()
    paperqa.Docs.aquery = mock_aquery6
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert len(model_calls) >= 1, "Retry MUST call model boundary"
    assert qa_res["is_refusal"] is False
    assert pipeline.state["stages"]["retrieval_and_qa"]["status"] == "completed"
    print("  PASS: 场景 6 验证通过：失败后显式重试成功恢复为 completed 状态！")

# ----------------------------------------------------------------------
# 场景 7: 有效学术回答含局部缺口措辞 (严谨识别，绝不误判为整答拒答)
# ----------------------------------------------------------------------
print("\n[SCENARIO 7] 有效学术回答含局部缺口措辞 (不误判为整答拒绝)...")
with tempfile.TemporaryDirectory() as tmp_dir:
    run_dir = Path(tmp_dir) / "run_s7"
    shutil.copytree(base_run_dir, run_dir, dirs_exist_ok=True)
    
    academic_text = (
        "本研究针对水面多艇协同向深海潜水器迁移中的推力分配进行了深入建模分析。"
        "在第 3 节中，我们建立了基于李雅普诺夫几何控制的鲁棒自适应框架；"
        "需要说明的是，针对深海高静水压扰动的实测数据，当前文献库中 could not find 某特定算法的开源完整代码，"
        "部分极端海况下信息不足，但这不影响主体几何可达集与 QP 重构的理论证明与算法完备性。"
    )
    
    qa_probe = {
        "question": "测试核心问题",
        "raw_answer": academic_text,
        "model_answer": academic_text,
        "is_refusal": False
    }
    
    is_ref = m.is_genuine_refusal(qa_probe)
    assert not is_ref, "有效学术长文中的局部缺口/信息不足陈述绝不能判定为整答拒答！"
    
    # 模拟模型生成该学术长文，验证状态被标记为 completed
    pipeline = m.ResearchPipeline(
        manifest_path=base_manifest,
        question=None,
        output_dir=run_dir,
        resume=False,
        backend="paperqa_native",
        config_path=config_file
    )
    
    async def mock_aquery7(*args, **kwargs):
        class MockSession:
            answer = academic_text
            raw_answer = academic_text
            contexts = []
            references = ""
        return MockSession()
    paperqa.Docs.aquery = mock_aquery7
    
    norm, mapping = pipeline.step1_prepare_corpus()
    qa_res = pipeline.step2_retrieval_and_qa(norm, mapping)
    
    assert qa_res["is_refusal"] is False, "is_refusal must be False"
    assert pipeline.state["stages"]["retrieval_and_qa"]["status"] == "completed"
    print("  PASS: 场景 7 验证通过：局部缺口诚实说明未被误判，阶段正确记为 completed！")

print("\n======================================================================")
print("=== ALL 7 CACHING & REFUSAL CONTRACT SCENARIOS FULLY PASSED ===")
print("======================================================================")
