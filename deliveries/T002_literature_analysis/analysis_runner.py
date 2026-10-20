#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
T002 文献分析层严谨核验与执行工具 (T002 Literature Analysis Engine & Rigorous Validator)
任务编号: T002-LITERATURE-ANALYSIS (Revision: R3)
"""

import os
import sys
import json
import re
import hashlib
import unicodedata
import argparse

# 动态引入兼容的 PDFium 库（如果本地环境未全局安装）
EXTRA_SITE_PACKAGES = [
    r"C:\Users\lamost\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages",
    r"C:\Users\lamost\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\Lib\site-packages"
]
for p in EXTRA_SITE_PACKAGES:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# 目录与文件路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DELIVERY_DIR = SCRIPT_DIR
SNAPSHOT_DIR = os.path.join(DELIVERY_DIR, "papers_snapshot")
PARSED_DIR = os.path.join(DELIVERY_DIR, "parsed_text")
MANIFEST_PATH = os.path.join(DELIVERY_DIR, "papers_manifest.json")
EVIDENCE_PATH = os.path.join(DELIVERY_DIR, "evidence_records.json")
CARDS_PATH = os.path.join(DELIVERY_DIR, "research_cards.json")
COMPARISON_PATH = os.path.join(DELIVERY_DIR, "cross_paper_comparison.json")
QA_PATH = os.path.join(DELIVERY_DIR, "qa_citations_record.json")

# 严谨退出码
EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 1
EXIT_INTEGRITY_ERROR = 2
EXIT_PARSING_ERROR = 3
EXIT_SCHEMA_ERROR = 4
EXIT_EVIDENCE_ERROR = 5
EXIT_QA_ERROR = 6

# 基准期望值与事实定义
EXPECTED_PAPERS = {
    "PAPER_92645125": {
        "raw_id": "92645125514cca64",
        "expected_pages": 8,
        "expected_sha256": "4169c6b2489453c2a366d05c4719b5850a02127f8294308f4dc383a7f374a8d8",
        "expected_bytes": 2051417,
        "sample_role": "workflow_test"
    },
    "PAPER_cdaf1e55": {
        "raw_id": "cdaf1e556efa7e8c",
        "expected_pages": 7,
        "expected_sha256": "305ce9d41a7f8104575f6e9f2ed58107624b522ed70c595fcd7f36cb0d356ccd",
        "expected_bytes": 242157,
        "sample_role": "workflow_test"
    }
}

def norm(s):
    """去除空白与Unicode规范化，与审查组严格一致"""
    if not s:
        return ""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def compute_text_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def extract_pdf_pages_pdfium(pdf_path):
    """使用 pypdfium2 直接从原始 PDF 文件中提取真实物理页文本"""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
        pages_text = []
        for idx, page in enumerate(doc):
            text = page.get_textpage().get_text_range()
            pages_text.append(text)
        doc.close()
        return pages_text
    except Exception as e:
        print(f"[ERROR] 调用 pypdfium2 提取异常: {e}", file=sys.stderr)
        return None

def rebuild_parsed_output_if_needed(manifest):
    """
    如果解析产物缺失，自动从快照 PDF 重新提取并生成 parsed_text/，
    确保系统具备完全可重跑的解析自愈重建能力。
    """
    os.makedirs(PARSED_DIR, exist_ok=True)
    for item in manifest:
        pid = item['paper_id']
        json_path = os.path.join(PARSED_DIR, f"{pid}_parsed.json")
        md_path = os.path.join(PARSED_DIR, f"{pid}_parsed.md")
        pdf_path = item['snapshot_pdf']

        if not os.path.exists(json_path) or not os.path.exists(md_path):
            print(f"[INFO] 正在从快照 PDF 自动重建解析产物: {pid}...")
            pages_raw = extract_pdf_pages_pdfium(pdf_path)
            if not pages_raw:
                print(f"[ERROR] 无法从 PDF 提取内容: {pdf_path}", file=sys.stderr)
                return False

            pages_list = []
            for idx, text in enumerate(pages_raw):
                cleaned_text = re.sub(r'\((?:Partial|Complete):[\s\S]*?\)$', '', text).strip()
                pages_list.append({
                    "page": idx + 1,
                    "char_count": len(cleaned_text),
                    "text": cleaned_text
                })

            parsed_json = {
                "paper_id": pid,
                "raw_id": item['raw_id'],
                "title": item['title'],
                "total_pages": len(pages_list),
                "pages": pages_list
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(parsed_json, f, ensure_ascii=False, indent=2)

            md_content = f"# {item['title']}\n\n" + "\n---\n\n".join(
                [f"## Page {p['page']} ({p['char_count']} chars)\n\n{p['text']}\n" for p in pages_list]
            )
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            print(f"  [OK] 成功重建 {pid} 逐页解析产物 ({len(pages_list)} 页)")
    return True

def verify_snapshots():
    """1. 核验文献快照存在性、大小与完整 SHA256 哈希"""
    print("[1/5] 核验文献快照完整性与哈希一致性...")
    if not os.path.exists(MANIFEST_PATH):
        print(f"[ERROR] 清单文件缺失: {MANIFEST_PATH}", file=sys.stderr)
        return False, None

    try:
        with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取清单 JSON 失败: {e}", file=sys.stderr)
        return False, None

    if not isinstance(manifest, list) or len(manifest) != len(EXPECTED_PAPERS):
        print(f"[ERROR] 清单条目数不符: 期望 {len(EXPECTED_PAPERS)}, 实际 {len(manifest) if isinstance(manifest, list) else 0}", file=sys.stderr)
        return False, None

    for item in manifest:
        pid = item.get("paper_id")
        if pid not in EXPECTED_PAPERS:
            print(f"[ERROR] 未知论文编号: {pid}", file=sys.stderr)
            return False, None

        spec = EXPECTED_PAPERS[pid]
        pdf_path = item.get("snapshot_pdf")
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"[ERROR] 快照文件缺失: {pdf_path}", file=sys.stderr)
            return False, None

        actual_bytes = os.path.getsize(pdf_path)
        if actual_bytes != spec["expected_bytes"]:
            print(f"[ERROR] 文件大小不匹配: {pid} (实际 {actual_bytes} != 期望 {spec['expected_bytes']})", file=sys.stderr)
            return False, None

        actual_sha = compute_sha256(pdf_path)
        if actual_sha != spec["expected_sha256"]:
            print(f"[ERROR] 哈希校验不匹配: {pid} (实际 {actual_sha} != 期望 {spec['expected_sha256']})", file=sys.stderr)
            return False, None

        if item.get("sample_role") != spec["sample_role"]:
            print(f"[ERROR] 论文角色未按规范标记为 {spec['sample_role']}: {pid}", file=sys.stderr)
            return False, None

        print(f"  [PASS] {pid}: 文件存在，字节数与哈希完全吻合，角色标记正确")

    return True, manifest

def verify_parsed_pages(manifest):
    """
    2. 核验 8+7 真实页数完整解析、无截断、无空页；
    【R3核心改进】按 NFKC 与空白归一化严格比较文本本身是否与底层 PDF 完全一致，
    直接否决同长度假文（如全是‘伪’字）的伪造解析！
    """
    print("[2/5] 核验逐页文本解析覆盖率、物理页文本真实性与无截断...")
    
    # 确保缺失时自动重建
    if not rebuild_parsed_output_if_needed(manifest):
        return False, None

    source_text_cache = {}

    for pid, spec in EXPECTED_PAPERS.items():
        json_path = os.path.join(PARSED_DIR, f"{pid}_parsed.json")
        md_path = os.path.join(PARSED_DIR, f"{pid}_parsed.md")

        if not os.path.exists(json_path) or not os.path.exists(md_path):
            print(f"[ERROR] 解析产物文件缺失: {json_path} 或 {md_path}", file=sys.stderr)
            return False, None

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"[ERROR] 读取解析 JSON 失败: {json_path}: {e}", file=sys.stderr)
            return False, None

        pages = data.get("pages", [])
        expected_count = spec["expected_pages"]
        
        # 严格检查真实物理页数，若缺页直接否决
        if len(pages) != expected_count:
            print(f"[ERROR] 解析页数不匹配: {pid} (实际 {len(pages)} 页 != 期望 {expected_count} 页)", file=sys.stderr)
            return False, None

        item_manifest = next(m for m in manifest if m['paper_id'] == pid)
        raw_pdf_pages = extract_pdf_pages_pdfium(item_manifest['snapshot_pdf'])
        if raw_pdf_pages is None:
            print(f"[ERROR] 无法读取来源快照 PDF: {item_manifest['snapshot_pdf']}", file=sys.stderr)
            return False, None

        source_text_cache[pid] = raw_pdf_pages

        for idx, p in enumerate(pages):
            expected_page_num = idx + 1
            page_num = p.get("page")
            if page_num != expected_page_num:
                print(f"[ERROR] 物理页序异常: {pid} 第 {idx} 项标为第 {page_num} 页", file=sys.stderr)
                return False, None
            
            char_count = p.get("char_count", 0)
            text = p.get("text", "")
            if char_count < 100 or len(text.strip()) < 100:
                print(f"[ERROR] 物理页文本过短或空页: {pid} 第 {expected_page_num} 页只有 {char_count} 字符", file=sys.stderr)
                return False, None
            if "Partial: pages" in text:
                print(f"[ERROR] 解析文本中包含工具截断提示: {pid} 第 {expected_page_num} 页", file=sys.stderr)
                return False, None

            # R3 严谨逐页文本比对（使用 norm 归一化后必须 100% 相同，击穿同字数伪造假文）
            norm_pdf = norm(raw_pdf_pages[idx])
            norm_delivered = norm(text)
            if norm_pdf != norm_delivered:
                print(f"[ERROR] 物理第 {expected_page_num} 页解析文本与 PDF 原文文本不一致（检测到篡改或假文）！", file=sys.stderr)
                return False, None

        print(f"  [PASS] {pid}: 真实解析覆盖全部 {len(pages)} 页 (逐页文本与底层PDF 100% 逐字吻合)")

    return True, source_text_cache

def verify_evidence_and_cards(source_text_cache):
    """
    3. 核验证据有效性、出处定位真实性、原表数据准确性；
    【R3核心改进】
    - 将 verbatim_quote 按省略号拆为真实片段，每个片段必须真实出现在声称的物理页原文中（精准击穿伪造引文）！
    - 对 claim 进行精确数值核验（击穿 999999 份问卷等修改）！
    - 对 table_data 中的每个数值与标准误进行严格比对（击穿 9.99 标准误）！
    """
    print("[3/5] 核验事实证据有效性、出处定位与研究卡片结构...")
    if not os.path.exists(EVIDENCE_PATH):
        print(f"[ERROR] 证据文件缺失: {EVIDENCE_PATH}", file=sys.stderr)
        return False

    try:
        with open(EVIDENCE_PATH, 'r', encoding='utf-8') as f:
            evd_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取证据 JSON 失败: {e}", file=sys.stderr)
        return False

    records = evd_data.get("evidence_records", [])
    if not isinstance(records, list) or len(records) < 4:
        print(f"[ERROR] 证据条目数量不足: 期望至少 4 条, 实际 {len(records) if isinstance(records, list) else 0} 条", file=sys.stderr)
        return False

    # 预定义本批证据的硬性事实真值基准（ Ground Truth ）
    FACT_SPECS = {
        "EVD_92645125_001": {
            "physical_page": 3,
            "printed_page": 69,
            "must_contain_in_claim": ["1781", "2694", "66.11%", "81.3%", "45.0%"],
            "forbidden_in_claim": ["999999", "电机", "虚构", "编造"],
            "quote_fragments": [
                "在2023年12月27日至2024年1月10日期间进行在线问卷调查",
                "面向全国76所高校发放问卷",
                "回收问卷2694份，有效问卷1781份，有效率66.11%",
                "使用过GenAI工具的学生占比81.3%",
                "坚持使用GenAI工具的学生占比45.0%",
                "未坚持使用的学生占比55.0%"
            ]
        },
        "EVD_92645125_002": {
            "physical_page": 4,
            "printed_page": 70,
            "must_contain_in_claim": ["0.40", "0.38", "0.47", "0.45", "0.06", "0.05"],
            "forbidden_in_claim": ["9.99", "0.106", "电机"],
            "quote_fragments": [
                "表格中系数为标准化回归系数，括号中为系数的标准误",
                "回归结果表明，相对于未使用过GenAI工具和未坚持使用的学生，使用过和坚持使用GenAI工具对学生批判性思维和自主学习能力具有显著的正向影响，假设1至假设4初步得到验证"
            ],
            "table_exact_values": {
                "model1_coef": 0.40, "model1_se": 0.06, "model1_p": "*** p<0.001",
                "model2_coef": 0.38, "model2_se": 0.06, "model2_p": "** p<0.01",
                "model3_coef": 0.47, "model3_se": 0.05, "model3_p": "*** p<0.001",
                "model4_coef": 0.45, "model4_se": 0.05, "model4_p": "*** p<0.001"
            }
        },
        "EVD_cdaf1e55_001": {
            "physical_page": 2,
            "printed_page": 67,
            "must_contain_in_claim": ["136", "24", "18", "476", "6"],
            "forbidden_in_claim": ["电机", "虚构"],
            "quote_fragments": [
                "本研究的对象为香港科技大学(广州)的一年级本科生",
                "在该校的136名本科生中，有24名学生自愿参与了本研究",
                "最终，18位参与者的数据有效，产生了476段对话用于分析",
                "为三角验证编码结果，本研究邀请了六名学生进行跟踪访谈"
            ]
        },
        "EVD_cdaf1e55_002": {
            "physical_page": 3,
            "printed_page": 68,
            "must_contain_in_claim": ["319", "17", "104", "78", "42", "13", "设定学习子目标"],
            "forbidden_in_claim": ["设定学习计划", "设定学习环境", "电机"],
            "quote_fragments": [
                "参与者使用ChatGPT进行自我调节学习的频率最高(319次)",
                "在整体的自我调节学习的对话中，学习规划类的互动对话出现的频率明显较低(17次，3人)",
                "设定学习子目标、理解教育技术的角色、建立CBLEs的内部表征及回顾最佳任务处理策略"
            ],
            "table_exact_values": {
                "total_srl": 319,
                "planning_goal_count": 17, "planning_goal_people": 3,
                "strategy_search_count": 104, "strategy_search_people": 18,
                "strategy_extension_count": 78, "strategy_extension_people": 12,
                "strategy_integrate_count": 17, "strategy_integrate_people": 5,
                "strategy_resource_count": 7, "strategy_resource_people": 3,
                "monitoring_judgment_count": 42,
                "monitoring_feeling_count": 28,
                "monitoring_content_eval_count": 13,
                "monitoring_sufficiency_eval_count": 5
            }
        }
    }

    evidence_map = {}
    for r in records:
        eid = r.get("evidence_id")
        pid = r.get("paper_id")
        claim = r.get("claim", "")
        quote = r.get("verbatim_quote", "")

        if not eid or eid not in FACT_SPECS:
            print(f"[ERROR] 存在无效或未知的证据 ID: {eid}", file=sys.stderr)
            return False
        if eid in evidence_map:
            print(f"[ERROR] 证据 ID 重复: {eid}", file=sys.stderr)
            return False
        evidence_map[eid] = r

        spec = FACT_SPECS[eid]
        loc = r.get("location", {})
        phys_page = loc.get("physical_pdf_page")
        print_page = loc.get("printed_page")

        if phys_page != spec["physical_page"] or print_page != spec["printed_page"]:
            print(f"[ERROR] 证据 {eid} 出处位置不符: 实际标为 P.{phys_page}/印刷P.{print_page}, 期望 P.{spec['physical_page']}/印刷P.{spec['printed_page']}", file=sys.stderr)
            return False

        # 1. 核验 claim 中的关键数字与事实真值
        norm_claim = norm(claim)
        for req in spec["must_contain_in_claim"]:
            if norm(req) not in norm_claim:
                print(f"[ERROR] 证据 {eid} 的 claim 缺少核心事实或数据 '{req}'！", file=sys.stderr)
                return False
        for fbd in spec["forbidden_in_claim"]:
            if norm(fbd) in norm_claim:
                print(f"[ERROR] 证据 {eid} 的 claim 包含非法或虚构内容 '{fbd}'！", file=sys.stderr)
                return False

        # 2. 核验 verbatim_quote 的真实性（拆解为片段，检查是否真实出现在对应物理页的原文中）
        page_raw_text = source_text_cache[pid][phys_page - 1]
        norm_source_page = norm(page_raw_text)

        # 检查每个原文片段是否存在于该页
        quote_fragments = re.split(r'\.{3}|…+', quote)
        quote_fragments = [frag.strip() for frag in quote_fragments if frag.strip()]
        if not quote_fragments:
            print(f"[ERROR] 证据 {eid} 的 verbatim_quote 为空！", file=sys.stderr)
            return False

        for frag in quote_fragments:
            norm_frag = norm(frag)
            if len(norm_frag) > 5 and norm_frag not in norm_source_page:
                print(f"[ERROR] 证据 {eid} 的引文片段 '{frag[:30]}...' 未在物理第 {phys_page} 页原文中找到（检测到伪造引文）！", file=sys.stderr)
                return False

        # 3. 校验表格数值与标准误
        if eid == "EVD_92645125_002":
            table_data = r.get("table_data", {})
            rows = table_data.get("rows", [])
            if len(rows) != 2:
                print(f"[ERROR] {eid} 表1行数不完整", file=sys.stderr)
                return False
            m1 = rows[0].get("model1_crit_thinking", {})
            m2 = rows[0].get("model2_self_learning", {})
            m3 = rows[1].get("model3_crit_thinking", {})
            m4 = rows[1].get("model4_self_learning", {})
            tb_spec = spec["table_exact_values"]
            if (m1.get("coef") != tb_spec["model1_coef"] or m1.get("se") != tb_spec["model1_se"] or m1.get("p_level") != tb_spec["model1_p"] or
                m2.get("coef") != tb_spec["model2_coef"] or m2.get("se") != tb_spec["model2_se"] or m2.get("p_level") != tb_spec["model2_p"] or
                m3.get("coef") != tb_spec["model3_coef"] or m3.get("se") != tb_spec["model3_se"] or m3.get("p_level") != tb_spec["model3_p"] or
                m4.get("coef") != tb_spec["model4_coef"] or m4.get("se") != tb_spec["model4_se"] or m4.get("p_level") != tb_spec["model4_p"]):
                print(f"[ERROR] {eid} 回归系数、标准误或显著性p值与原表真实值不符 (包含错误数值或显著性篡改，如 p<0.90)！", file=sys.stderr)
                return False
        elif eid == "EVD_cdaf1e55_002":
            table_data = r.get("table_data", {})
            freqs = table_data.get("srl_frequencies", {})
            tb_spec = spec["table_exact_values"]
            if (freqs.get("total_srl") != tb_spec["total_srl"] or
                freqs.get("planning_goal_setting", {}).get("count") != tb_spec["planning_goal_count"] or
                freqs.get("planning_goal_setting", {}).get("people") != tb_spec["planning_goal_people"] or
                freqs.get("strategy_goal_directed_search", {}).get("count") != tb_spec["strategy_search_count"] or
                freqs.get("strategy_goal_directed_search", {}).get("people") != tb_spec["strategy_search_people"] or
                freqs.get("strategy_knowledge_extension", {}).get("count") != tb_spec["strategy_extension_count"] or
                freqs.get("strategy_knowledge_extension", {}).get("people") != tb_spec["strategy_extension_people"] or
                freqs.get("strategy_integrate_materials", {}).get("count") != tb_spec["strategy_integrate_count"] or
                freqs.get("strategy_integrate_materials", {}).get("people") != tb_spec["strategy_integrate_people"] or
                freqs.get("strategy_resource_acquisition", {}).get("count") != tb_spec["strategy_resource_count"] or
                freqs.get("strategy_resource_acquisition", {}).get("people") != tb_spec["strategy_resource_people"] or
                freqs.get("monitoring_learning_judgment", {}).get("count") != tb_spec["monitoring_judgment_count"] or
                freqs.get("monitoring_feeling_of_knowing", {}).get("count") != tb_spec["monitoring_feeling_count"] or
                freqs.get("monitoring_content_evaluation", {}).get("count") != tb_spec["monitoring_content_eval_count"] or
                freqs.get("monitoring_sufficiency_evaluation", {}).get("count") != tb_spec["monitoring_sufficiency_eval_count"]):
                print(f"[ERROR] {eid} 表2 SRL行为频次或人数字段与原表真实值不符 (检测到篡改或未核对字段)！", file=sys.stderr)
                return False

        print(f"  [PASS] 证据 {eid}: 物理P.{phys_page} (印刷P.{print_page}) 片段与原表数值全部通过逐字/逐数真实核对")

    # 检查研究卡片
    if not os.path.exists(CARDS_PATH):
        print(f"[ERROR] 卡片文件缺失: {CARDS_PATH}", file=sys.stderr)
        return False

    try:
        with open(CARDS_PATH, 'r', encoding='utf-8') as f:
            cards_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取卡片 JSON 失败: {e}", file=sys.stderr)
        return False

    cards = cards_data.get("cards", [])
    if not isinstance(cards, list) or len(cards) != 2:
        print(f"[ERROR] 卡片数量不足: 期望 2 张, 实际 {len(cards) if isinstance(cards, list) else 0}", file=sys.stderr)
        return False

    for c in cards:
        pid = c.get("paper_id")
        if pid not in EXPECTED_PAPERS:
            print(f"[ERROR] 卡片论文编号未知: {pid}", file=sys.stderr)
            return False
        for req_field in ["research_question", "methodology", "authors_claimed_contributions", 
                          "premises_and_conditions", "main_results", "limitations_and_future_work", "code_and_reproducibility"]:
            if req_field not in c:
                print(f"[ERROR] 卡片 {pid} 缺失必要字段: {req_field}", file=sys.stderr)
                return False
        print(f"  [PASS] 研究卡片 {pid}: 结构完备，字段完整")

    return evidence_map

def verify_comparison_table(evidence_map):
    """4. 核验跨论文对比表结构与证据依据有效性"""
    print("[4/5] 核验跨论文对比表与证据关联性...")
    if not os.path.exists(COMPARISON_PATH):
        print(f"[ERROR] 对比表文件缺失: {COMPARISON_PATH}", file=sys.stderr)
        return False

    try:
        with open(COMPARISON_PATH, 'r', encoding='utf-8') as f:
            cmp_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取对比表 JSON 失败: {e}", file=sys.stderr)
        return False

    dims = cmp_data.get("aligned_dimensions", [])
    if not isinstance(dims, list) or len(dims) < 5:
        print(f"[ERROR] 对比维度不足或为空: 期望至少 5 个维度, 实际 {len(dims) if isinstance(dims, list) else 0}", file=sys.stderr)
        return False

    for d in dims:
        dim_name = d.get("dimension")
        evd_links = d.get("evidence_alignment", [])
        for eid in evd_links:
            if eid not in evidence_map:
                print(f"[ERROR] 对比维度 '{dim_name}' 引用了不存在的证据: {eid}", file=sys.stderr)
                return False
        print(f"  [PASS] 对比维度 '{dim_name}': 关联证据存在，比较判定明确")

    return True

def verify_and_test_qa(evidence_map):
    """
    5. 实际测试问答基准集，核验问题支持性判定与证据引用真实性；
    【R3核心改进】
    - 不仅检查证据 ID 存在，而且深度核验回答中的语义事实与命题！
    - 若答案夹带伪造电机结论（如 999 效率、motor 等）即使夹带了关键词 '76'，依然坚决否决拦截！
    - 对 Q01~Q04 各题必须包含该题所要求的完整命题事实集合，否则判定为虚假回答退出 6！
    """
    print("[5/5] 执行固定基准问答集结构、引用、事实关系核对与静态语义核查...")
    if not os.path.exists(QA_PATH):
        print(f"[ERROR] 问答记录文件缺失: {QA_PATH}", file=sys.stderr)
        return False

    try:
        with open(QA_PATH, 'r', encoding='utf-8') as f:
            qa_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取问答 JSON 失败: {e}", file=sys.stderr)
        return False

    questions = qa_data.get("questions", [])
    if not isinstance(questions, list) or len(questions) < 4:
        print(f"[ERROR] 问答测试集过短或为空: 实际 {len(questions) if isinstance(questions, list) else 0} 题", file=sys.stderr)
        return False

    # 每道受支持问答的核心语义事实约束（Semantic Fact Constraints）
    QA_SEMANTIC_SPECS = {
        "Q01_SINGLE_SAMPLE": {
            "required_facts": ["1781", "2694", "76", "66.11%", "81.3%", "45.0%"],
            "forbidden_words": ["motor", "电机", "999", "fabricated", "虚构", "面条"],
            "required_evidence": ["EVD_92645125_001"],
            "relational_check": lambda ans: (
                ("回收" in ans and "有效" in ans and ans.find("2694") < ans.find("1781")) or
                ("回收问卷2694" in ans and "有效问卷1781" in ans) or
                ("回收2694" in ans and "有效1781" in ans)
            )
        },
        "Q02_SINGLE_REGRESSION": {
            "required_facts": ["0.40", "0.38", "0.47", "0.45", "0.06", "0.05"],
            "forbidden_words": ["0.106", "0.108", "9.99", "电机"],
            "required_evidence": ["EVD_92645125_002"]
        },
        "Q03_SINGLE_QUAL_DATA": {
            "required_facts": ["香港科技大学", "136", "24", "18", "476", "6"],
            "forbidden_words": ["电机", "虚构"],
            "required_evidence": ["EVD_cdaf1e55_001"]
        },
        "Q04_CROSS_COMPARISON": {
            "required_facts": ["封闭型问题", "319", "17", "设定学习子目标", "假说"],
            "forbidden_words": ["电机", "已证实认知偏差", "设定学习计划"],
            "required_evidence": ["EVD_92645125_002", "EVD_cdaf1e55_002"]
        }
    }

    has_supported = False
    has_unsupported = False

    for q in questions:
        qid = q.get("question_id")
        qtext = q.get("question_text", "")
        is_sup = q.get("is_supported")
        cited_evd = q.get("cited_evidence", [])
        sys_ans = q.get("system_answer", "")

        # 检查引用的证据是否真实存在
        for eid in cited_evd:
            if eid not in evidence_map:
                print(f"[ERROR] 问答 [{qid}] 引用了不存在的证据 ID: {eid}！", file=sys.stderr)
                return False

        # 支持性逻辑一致性检查
        if is_sup:
            has_supported = True
            if not cited_evd:
                print(f"[ERROR] 问答 [{qid}] 标记为支持，但未关联任何证据引用！", file=sys.stderr)
                return False
            if len(sys_ans.strip()) < 20:
                print(f"[ERROR] 问答 [{qid}] 回答内容过短或为空", file=sys.stderr)
                return False

            if qid in QA_SEMANTIC_SPECS:
                q_spec = QA_SEMANTIC_SPECS[qid]
                # 检查关联证据是否完全吻合
                if set(cited_evd) != set(q_spec["required_evidence"]):
                    print(f"[ERROR] 问答 [{qid}] 关联的证据不符合事实规范: 期望 {q_spec['required_evidence']}, 实际 {cited_evd}", file=sys.stderr)
                    return False

                norm_ans = norm(sys_ans)
                # 检查违禁词（防关键词欺骗，如仅仅追加 '76' 的水下电机假答案）
                for fbd in q_spec["forbidden_words"]:
                    if norm(fbd) in norm_ans:
                        print(f"[ERROR] 问答 [{qid}] 回答包含违禁或伪造内容 '{fbd}' (检测到夹带关键词的虚假回答)！", file=sys.stderr)
                        return False

                # 检查必需的事实集合
                for req in q_spec["required_facts"]:
                    if norm(req) not in norm_ans:
                        print(f"[ERROR] 问答 [{qid}] 回答未体现必要核心事实 '{req}'！", file=sys.stderr)
                        return False

                # 关系与限定条件检查（防止交换数值导致事实颠倒）
                rel_check = q_spec.get("relational_check")
                if rel_check and not rel_check(sys_ans):
                    print(f"[ERROR] 问答 [{qid}] 事实关系颠倒或逻辑不成立（检测到数值互换或语意破坏）！", file=sys.stderr)
                    return False

        else:
            has_unsupported = True
            if "信息不足" not in sys_ans and "原文未报告" not in sys_ans and "超出文献范围" not in sys_ans:
                print(f"[ERROR] 问答 [{qid}] 标为不支持，但回答未包含明确的信息不足/原文未报告拦截说明！", file=sys.stderr)
                return False

        # 【收尾二核心要求】对全部 6 道静态题严格核验人工/Agent回查记录有效性与答案指纹绑定
        audit = q.get("semantic_audit")
        if not audit or not isinstance(audit, dict):
            print(f"[ERROR] 问答 [{qid}] 缺少 semantic_audit 回查记录，不能声明已通过语义核对！", file=sys.stderr)
            return False

        audit_status = audit.get("audit_status")
        if audit_status != "verified":
            print(f"[ERROR] 问答 [{qid}] 语义审核状态为 '{audit_status}' (未完成人工/Agent回查或处于 pending)，不能声明通过！", file=sys.stderr)
            return False

        expected_hash = audit.get("answer_sha256")
        if not expected_hash:
            print(f"[ERROR] 问答 [{qid}] 缺少答案指纹哈希 (answer_sha256)，审核状态无效！", file=sys.stderr)
            return False

        actual_hash = compute_text_sha256(sys_ans)
        if expected_hash != actual_hash:
            print(f"[ERROR] 问答 [{qid}] 答案内容已改动，原回查哈希失效，需重新进行人工/Agent语义核对！", file=sys.stderr)
            return False

        print(f"  [PASS] 问答 [{qid}]: {'支持性回答' if is_sup else '信息不足拦截'} 结构、引用与事实关系通过 (已核验人工/Agent回查记录有效)")

    if not has_supported or not has_unsupported:
        print("[ERROR] 问答集必须同时包含受支持问题核验与信息不足拦截测试用例！", file=sys.stderr)
        return False

    return True

def main():
    parser = argparse.ArgumentParser(description="T002 Rigorous Validator")
    parser.add_argument("--verify-only", action="store_true", help="仅执行完整性校验")
    parser.add_argument("--check-qa", action="store_true", help="重点核对问答与引用支持性")
    args = parser.parse_args()

    print("==================================================================")
    print("  T002 文献分析层严谨核验器 (T002-LITERATURE-ANALYSIS R3)")
    print("==================================================================")

    # 1. 快照校验
    snapshot_ok, manifest = verify_snapshots()
    if not snapshot_ok:
        sys.exit(EXIT_INTEGRITY_ERROR)

    # 2. 逐页解析覆盖校验 (8+7页逐页文本真实性与无截断校验)
    pages_ok, source_text_cache = verify_parsed_pages(manifest)
    if not pages_ok:
        sys.exit(EXIT_PARSING_ERROR)

    # 3. 证据与卡片有效性
    evidence_map = verify_evidence_and_cards(source_text_cache)
    if not evidence_map:
        sys.exit(EXIT_EVIDENCE_ERROR)

    # 4. 对比表校验
    if not verify_comparison_table(evidence_map):
        sys.exit(EXIT_SCHEMA_ERROR)

    # 5. 问答集与引用支持性实际检查
    if not verify_and_test_qa(evidence_map):
        sys.exit(EXIT_QA_ERROR)

    print("\n[SUCCESS] T002 全部物理页解析、核心事实证据、卡片对比及问答引用严谨核验通过！")
    sys.exit(EXIT_SUCCESS)

if __name__ == "__main__":
    main()
