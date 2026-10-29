"""标书工具 Web 服务：上传招标文件 → 招标要点 / 审标报告 / 投标文件模板。

架构：
    Vue 前端 --HTTP--> FastAPI(本文件) --import--> bid_toolkit 核心能力

说明：
    - 核心解析能力全部来自 bid-toolkit（本地离线，数据不出库）
    - LLM 未配置时投标文件走模板降级模式，不影响骨架生成
    - 产物统一写入 OUTPUT_DIR，前端通过 /api/download 下载
    - 历史记录通过 index.json 持久化，会话丢失时从磁盘自动恢复
"""
import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ─── 挂载 bid-toolkit 代码路径 ───────────────────────────────
REPO = Path(r"c:\Users\SJKJ\Documents\trae_projects\test\bid-toolkit-analysis")
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

OUTPUT_DIR = REPO / "demo_output" / "web"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO / "templates" / "user_config.yaml"
COMPANY_DIR = REPO / "demo_output" / "company_profile"
COMPANY_FILES = ("company_info", "qualifications", "team", "performance")

INDEX_PATH = OUTPUT_DIR / "index.json"

app = FastAPI(title="标书工具 Web", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地演示用，生产环境应收紧
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, dict] = {}  # session_id -> {"src": Path, "stem": str}


# ─── 历史索引持久化 ──────────────────────────────────────────

def _load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_index(idx: dict) -> None:
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def _index_update(sid: str, **kw) -> None:
    idx = _load_index()
    entry = idx.get(sid, {})
    entry.update(kw)
    idx[sid] = entry
    _save_index(idx)


def _recover_stem(sid: str) -> str:
    """从既有产物恢复原始文件 stem（落盘名 {sid}_src{ext} 不含原始文件名）。"""
    report_f = OUTPUT_DIR / f"{sid}_审标报告.md"
    if report_f.exists():
        m = re.search(r"^文件：(.+)$", report_f.read_text(encoding="utf-8"), re.M)
        if m:
            return Path(m.group(1).strip()).stem
    points_f = OUTPUT_DIR / f"{sid}_要点.json"
    if points_f.exists():
        try:
            name = str(json.loads(points_f.read_text(encoding="utf-8")).get("文件名") or "")
            if name and f"{sid}_src" not in name:  # 排除落盘名
                return Path(name).stem
        except Exception:
            pass
    return sid


def _recover_review_stats(sid: str) -> tuple[int, int] | None:
    """从既有审标产物恢复 (risk_total, critical)，无则 None。"""
    review_json = OUTPUT_DIR / f"{sid}_审标.json"
    if review_json.exists():
        try:
            data = json.loads(review_json.read_text(encoding="utf-8"))
            items = data.get("items") or []
            if items:
                return len(items), sum(1 for it in items if str(it.get("类别", "")).startswith("🔴"))
        except Exception:
            pass
    report_f = OUTPUT_DIR / f"{sid}_审标报告.md"
    if report_f.exists():
        m = re.search(r"命中：(\d+) 处风险点", report_f.read_text(encoding="utf-8"))
        if m:
            text = report_f.read_text(encoding="utf-8")
            risk = int(m.group(1))
            crit = len(re.findall(r"- L\d+ \[🔴", text))
            return risk, crit
    return None


def _scan_existing() -> None:
    """启动时扫描历史产物目录，把无索引的存量文件回填到索引。"""
    idx = _load_index()
    changed = False
    for src in OUTPUT_DIR.glob("*_src.*"):
        sid = src.name.split("_", 1)[0]
        entry = idx.get(sid)
        if entry is None:
            stem = _recover_stem(sid)
            entry = {
                "filename": stem + src.suffix,
                "created_at": datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "status": "uploaded",
                "risk_total": None,
                "critical": None,
                "score": None,
                "duration_sec": None,
            }
            idx[sid] = entry
            SESSIONS[sid] = {"src": src, "stem": stem}
            changed = True
        # 补算统计（旧记录缺 risk/critical 时从产物恢复）
        if entry.get("risk_total") is None:
            recovered = _recover_review_stats(sid)
            if recovered:
                risk_total, critical = recovered
                entry.update(risk_total=risk_total, critical=critical,
                             score=max(0, 100 - risk_total),
                             status="done" if (OUTPUT_DIR / f"{sid}_投标文件初稿.md").exists() else "reviewed")
                changed = True
    if changed:
        _save_index(idx)


_scan_existing()


# ─── 会话恢复 ────────────────────────────────────────────────

def _get_session(sid: str) -> dict | None:
    """先查内存，miss 则从磁盘恢复（重启后历史记录仍可用）。"""
    if sid in SESSIONS:
        return SESSIONS[sid]
    for src in OUTPUT_DIR.glob(f"{sid}_src.*"):
        sess = {"src": src, "stem": _recover_stem(sid)}
        SESSIONS[sid] = sess
        return sess
    return None


# ─── 工具函数 ────────────────────────────────────────────────

def _load_parse_bid():
    import parse_bid  # noqa: PLC0415
    return parse_bid


def _get_llm_client():
    """按设置页配置动态构建 LLM 客户端；未配置 api_key 时返回 None（走本地模板模式）。

    每次请求重新读取配置，设置页保存后无需重启后端即可生效。
    """
    from llm_client import LLMClient

    if not CONFIG_PATH.exists():
        return None
    try:
        full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    llm_cfg = full.get("llm") or {}
    if not str(llm_cfg.get("api_key") or "").strip():
        return None
    client = LLMClient(config_path=str(CONFIG_PATH))
    return client if client.is_available() else None


class _ScanLLMAdapter:
    """审标 scanner 调 chat(prompt) 传字符串，适配为 LLMClient 的 messages 接口。"""

    def __init__(self, client):
        self._client = client

    def chat(self, prompt, **kw):
        return self._client.chat_simple("", prompt, **kw)


def docx_to_md(src: Path) -> Path:
    """docx 转 markdown（表格转 md 表），复用已验证的转换器。"""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    doc = Document(str(src))
    parts = []
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name or "").lower()
            if "heading 1" in style:
                parts.append(f"# {t}")
            elif "heading 2" in style:
                parts.append(f"## {t}")
            elif "heading 3" in style:
                parts.append(f"### {t}")
            elif "heading" in style:
                parts.append(f"#### {t}")
            else:
                parts.append(t)
        elif child.tag == qn("w:tbl"):
            tbl = Table(child, doc)
            rows = []
            for r in tbl.rows:
                cells = [" ".join(c.text.split()) or " " for c in r.cells]
                rows.append("| " + " | ".join(cells) + " |")
                if len(rows) == 1:
                    rows.append("|" + "---|" * len(cells))
            parts.append("\n".join(rows))
    md = src.with_suffix(".md")
    md.write_text("\n\n".join(parts), encoding="utf-8")
    return md


def normalize_to_md(src: Path, sess: dict | None = None) -> Path:
    """任意支持格式统一转成 md 供解析（docx 路径已验证比 PDF 直读干净）。

    PDF 含扫描页时自动走 RapidOCR 本地离线识别（详见 scripts/pdf_ocr.py）：
    - 纯数字版 PDF：返回原文件，走 pdfplumber 快路径（行为与改动前一致）
    - 含扫描页：逐页组装 md（文字页保表格 / 扫描页 OCR），OCR 统计写入 sess["ocr_info"]
    - OCR 链路任何异常：警告后返回原文件，不劣于改动前
    """
    ext = src.suffix.lower()
    if ext == ".docx":
        return docx_to_md(src)
    if ext == ".pdf":
        md_candidate = src.with_suffix(".md")
        try:
            sys.path.insert(0, str(REPO / "scripts"))
            from pdf_ocr import pdf_needs_ocr, pdf_to_md_via_ocr

            if pdf_needs_ocr(src):
                if md_candidate.exists():  # parse 已转过，review/generate 直接复用
                    return md_candidate
                info = pdf_to_md_via_ocr(src, md_candidate)
                if sess is not None:
                    sess["ocr_info"] = info
                return md_candidate
        except Exception as e:
            print(f"OCR 转换失败，回退 PDF 原路径：{e}", file=sys.stderr)
        return src
    return src


_QUAL_NOISE_KEYWORDS = ("招标人：", "招标人:", "招标代理机构", "行政监督", "监督机构",
                        "联系人", "联系电话", "电话：", "电话:", "传真", "地址：", "地址:")


def _clean_qual_item(q):
    """资质条目归一化：兼容中英文 key，过滤联系方式类噪音。"""
    if isinstance(q, str):
        req, typ, cert = q.strip(), "", ""
    elif isinstance(q, dict):
        req = str(q.get("requirement") or q.get("要求内容") or "").strip()
        typ = str(q.get("type") or q.get("必备可选") or "").strip()
        cert = str(q.get("certificate") or q.get("所需证书") or "").strip()
    else:
        return None
    if not req:
        return None
    req_nospace = re.sub(r"\s+", "", req)
    if any(k.replace(" ", "") in req_nospace for k in _QUAL_NOISE_KEYWORDS):
        return None
    return {"requirement": req, "type": typ, "certificate": cert}


def slim_points(result: dict) -> dict:
    """提炼前端展示用的招标要点。"""
    scoring = [
        {
            "类别": s.get("category", ""),
            "项目": s.get("item", ""),
            "分值": s.get("score", ""),
            "评审标准": (s.get("criteria", "") or "")[:200],
        }
        for s in result.get("scoring_items", [])
        if s.get("item")
    ]
    quals = []
    for q in (result.get("qualification_requirements") or result.get("资质要求") or []):
        item = _clean_qual_item(q)
        if item:
            quals.append(item)
    return {
        "文件名": result.get("文件名", ""),
        "废标红线": result.get("废标红线", []),
        "文件清单": result.get("文件清单", []),
        "资质要求": quals,
        "评分项": scoring,
        "评分总分": sum(float(s["分值"]) for s in scoring if str(s["分值"]).replace(".", "").isdigit()),
        "预算": result.get("预算", []),
        "时间节点": result.get("时间节点", []),
        "保证金": result.get("保证金", ""),
    }


def _risk_summary(items: list[dict]) -> tuple[int, int]:
    """(总风险数, 废标级风险数)。废标级 = 🔴 开头的类别。"""
    critical = sum(1 for it in items if str(it.get("类别", "")).startswith("🔴"))
    return len(items), critical


def _do_parse(sid: str) -> dict:
    """执行解析并落盘，返回 points。parse 端点与详情接口共用。"""
    parse_bid = _load_parse_bid()
    sess = SESSIONS[sid]
    md_path = normalize_to_md(sess["src"], sess)
    result = parse_bid.parse_bid_document(str(md_path))
    points = slim_points(result)
    ocr_info = sess.get("ocr_info")
    if ocr_info:
        points["OCR识别"] = {
            "总页数": ocr_info.get("total_pages", 0),
            "OCR页数": ocr_info.get("ocr_pages", 0),
            "耗时秒": ocr_info.get("elapsed_sec", 0),
        }
    (OUTPUT_DIR / f"{sid}_要点.json").write_text(
        json.dumps(points, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _index_update(sid, status="parsed")
    return points


def _do_review(sid: str) -> dict:
    """执行审标扫描并落盘结构化结果，返回 review dict。"""
    from bid_toolkit.review.scanner import scan_tender

    CATEGORY_LABELS = {
        "primary": "🔴 废标/否决",
        "bid_types": "🔴 行业特有风险",
        "secondary": "🟡 评分/格式",
        "contract": "🔵 合同约束",
        "customization": "🔗 商务门槛",
        "certifications": "📄 证明材料",
        "time_nodes": "⏰ 时间节点",
        "emphasis_marks": "⚠️ 强调标识",
    }

    sess = SESSIONS[sid]
    md_path = normalize_to_md(sess["src"], sess)
    llm = _get_llm_client()
    result = scan_tender(
        str(md_path),
        with_llm=llm is not None,
        llm_client=_ScanLLMAdapter(llm) if llm else None,
    )

    stats: dict[str, int] = {}
    items = []
    for h in result.hits:
        label = CATEGORY_LABELS.get(h.category, h.category)
        stats[label] = stats.get(label, 0) + 1
        items.append({
            "行号": h.line_num,
            "类别": label,
            "命中词": h.keyword[:30],
            "上下文": (h.context or "")[:120],
        })

    lines = [
        "# 审标报告",
        "",
        f"文件：{sess['stem']}",
        f"命中：{len(result.hits)} 处风险点（已排除噪音+合并同类）",
        f"LLM复核：{'已启用' if llm else '未配置API（仅规则层）'}",
        "",
    ]
    for label, n in sorted(stats.items(), key=lambda x: -x[1]):
        lines.append(f"- {label}：{n} 项")
    lines.append("")
    lines.append("## 风险明细（前 80 条）")
    lines.append("")
    for it in items[:80]:
        lines.append(f"- L{it['行号']} [{it['类别']}] `{it['命中词']}` {it['上下文']}")
    report = "\n".join(lines)
    (OUTPUT_DIR / f"{sid}_审标报告.md").write_text(report, encoding="utf-8")

    # 结构化落盘（详情接口直接读取，避免重启后重扫）
    structured = {"total": len(items), "stats": stats, "items": items[:80]}
    (OUTPUT_DIR / f"{sid}_审标.json").write_text(
        json.dumps(structured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    risk_total, critical = _risk_summary(items)
    idx = _load_index()
    score = idx.get(sid, {}).get("score")
    if score is None:
        score = 0
    _index_update(sid, risk_total=risk_total, critical=critical,
                  score=max(0, 100 - risk_total), status="reviewed")

    return {"total": len(items), "stats": stats, "items": items[:80],
            "llm_used": llm is not None,
            "download": f"/api/download/{sid}_审标报告.md"}


# ─── 接口：核心分析流程（保持原签名不变） ─────────────────────

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """上传招标文件，返回 session_id。支持 pdf / docx / md / txt。"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".pdf", ".docx", ".md", ".txt"}:
        return JSONResponse(status_code=400, content={"error": f"不支持的格式 {ext}，仅支持 pdf/docx/md/txt"})
    sid = uuid.uuid4().hex[:12]
    src = OUTPUT_DIR / f"{sid}_src{ext}"
    src.write_bytes(await file.read())
    stem = Path(file.filename).stem
    SESSIONS[sid] = {"src": src, "stem": stem}
    _index_update(
        sid,
        filename=file.filename,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status="uploaded",
        risk_total=None, critical=None, score=None, duration_sec=None,
    )
    return {"session_id": sid, "filename": file.filename, "size_kb": round(src.stat().st_size / 1024, 1)}


@app.post("/api/parse/{sid}")
def parse(sid: str):
    """解析招标文件 → 结构化招标要点。"""
    sess = _get_session(sid)
    if not sess:
        return JSONResponse(status_code=404, content={"error": "会话不存在，请重新上传"})
    t0 = time.time()
    points = _do_parse(sid)
    _index_update(sid, duration_sec=round(time.time() - t0, 1))
    return {
        "points": points,
        "summary": {
            "废标红线": len(points["废标红线"]),
            "评分项": len(points["评分项"]),
            "评分总分": points["评分总分"],
            "资质要求": len(points["资质要求"]),
            "文件清单": len(points["文件清单"]),
            "OCR页数": points.get("OCR识别", {}).get("OCR页数", 0),
            "总页数": points.get("OCR识别", {}).get("总页数", 0),
        },
    }


@app.post("/api/review/{sid}")
def review(sid: str):
    """三层审标风险扫描，返回结构化风险清单 + 文本报告。"""
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "会话不存在"})
    t0 = time.time()
    result = _do_review(sid)
    elapsed = round(time.time() - t0, 1)
    # 累计耗时（parse + review + generate）
    idx = _load_index().get(sid, {})
    _index_update(sid, duration_sec=round((idx.get("duration_sec") or 0) + elapsed, 1))
    return result


def _clean_checklist_item(item: str) -> str:
    """清洗文件清单条目：去掉序号与尾部括注，得到纯文件名。"""
    name = re.sub(r"^[（(]\d+[)）]\s*", "", item)
    name = re.sub(r"^\d+\s*[、.．]\s*", "", name)
    name = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]", "", name)
    name = re.sub(r"[（(][^)）]*[)）]\s*$", "", name)
    return name.strip()


def _align_outline_with_checklist(sections: list[dict], checklist: list) -> tuple[list[dict], list[str]]:
    """按招标文件「投标文件组成清单」对齐大纲。

    清单中要求、但现有类型大纲未覆盖的条目，自动追加为占位章节，
    使初稿结构跟随每个招标文件自身的格式要求。
    返回 (对齐后的章节列表, 新增章节名列表)。
    """
    if not checklist:
        return sections, []
    titles_norm = [re.sub(r"\s+", "", s["title"]) for s in sections]
    added: list[str] = []
    for item in checklist:
        name = _clean_checklist_item(str(item))
        if not name or len(name) > 30:
            continue
        norm = re.sub(r"\s+", "", name)
        if not norm:
            continue
        if any(norm in t or t in norm for t in titles_norm):
            continue
        sections.append({
            "index": len(sections) + 1,
            "title": name,
            "content": "招标文件「投标文件组成」明确要求包含本项，请按招标文件给定格式编制，勿遗漏。",
            "part": "其他",
            "is_commercial": False,
        })
        titles_norm.append(norm)
        added.append(name)
    return sections, added


@app.post("/api/generate/{sid}")
def generate(sid: str):
    """生成投标文件初稿：已配置 LLM 时逐章节 AI 生成正文，未配置时降级骨架+占位符模式。"""
    sys.path.insert(0, str(REPO / "scripts"))
    from bid_generator import (  # noqa: PLC0415
        load_company_profiles,
        build_rfp_summary,
        parse_outline,
        generate_commercial_section,
        generate_technical_section,
        detect_bid_type,
        TEMPLATES_DIR,
        OUTLINE_FILES,
    )
    from parse_bid import parse_bid_document, read_input_file

    sess = _get_session(sid)
    if not sess:
        return JSONResponse(status_code=404, content={"error": "会话不存在"})

    t0 = time.time()

    # 1) 解析招标文件
    parse_bid = _load_parse_bid()
    md_path = normalize_to_md(sess["src"], sess)
    rfp_data = parse_bid.parse_bid_document(str(md_path))

    # 2) 自动识别标书类型（货物/服务/工程），按类型选大纲；识别失败内置兜底服务类
    try:
        rfp_text = read_input_file(str(md_path))
    except Exception:
        rfp_text = ""
    bid_type, detection_info = detect_bid_type(
        rfp_text, TEMPLATES_DIR / "bid_type_detection.yaml"
    )
    outline_path = TEMPLATES_DIR / OUTLINE_FILES.get(bid_type, "service_outline.md")
    sections = parse_outline(outline_path)

    # 2.5) 按招标文件「投标文件组成清单」对齐大纲：
    #      清单要求但大纲未覆盖的条目自动补充为章节，使结构跟随该招标文件的格式要求
    checklist = [str(x).strip() for x in (rfp_data.get("文件清单") or []) if str(x).strip()]
    sections, checklist_added = _align_outline_with_checklist(sections, checklist)

    # 3) 企业素材（演示用占位素材目录）
    profiles = load_company_profiles(COMPANY_DIR)

    # 4) 组装：商务标模板填充；技术标有 LLM 配置则 AI 生成正文，否则骨架占位
    llm = _get_llm_client()
    llm_ok = 0
    rfp_summary = build_rfp_summary(rfp_data)
    md_parts = [f"# 投标文件（{'AI 初稿' if llm else '初稿骨架'}）：{sess['stem']}\n"]
    for sec in sections:
        if sec["is_commercial"]:
            # generate_commercial_section 返回值已含 "## 标题"，此处不再重复拼接
            md_parts.append("\n" + generate_commercial_section(sec, rfp_summary, profiles))
        elif llm is not None:
            content, ok = generate_technical_section(sec, rfp_summary, profiles, llm)
            md_parts.append("\n" + content + "\n")
            llm_ok += int(ok)
        else:
            md_parts.append(f"\n## {sec['title']}\n")
            req = sec.get("content", "")
            md_parts.append(f"> 本节需按评分项响应展开。招标方相关要求摘要：\n>\n{req[:600]}\n")
            md_parts.append("\n（内容待填写：结合企业素材与评分标准逐条响应）\n")
    md_text = "\n".join(md_parts)
    md_out = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    md_out.write_text(md_text, encoding="utf-8")

    # 5) 废标自查：招标文件废标项 → 投标初稿逐条对照（规则层，不调LLM）
    from bid_toolkit.review.scanner import scan_tender  # noqa: PLC0415
    from bid_toolkit.review.reverse_check import reverse_coverage_check  # noqa: PLC0415

    scan_result = scan_tender(str(md_path))
    dq_check = reverse_coverage_check(scan_result, md_out)
    (OUTPUT_DIR / f"{sid}_废标自查.json").write_text(
        json.dumps(dq_check, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = round(time.time() - t0, 1)
    idx = _load_index().get(sid, {})
    _index_update(sid, duration_sec=round((idx.get("duration_sec") or 0) + elapsed, 1),
                  status="done")

    tech_total = sum(1 for sec in sections if not sec["is_commercial"])
    return {
        "sections": len(sections),
        "bid_type": detection_info.get("name", "服务类"),
        "bid_type_code": bid_type,
        "llm_used": llm is not None,
        "llm_sections_ok": llm_ok,
        "tech_sections": tech_total,
        "checklist_total": len(checklist),
        "checklist_added": checklist_added,
        "dq_check": dq_check,
        "placeholders": sorted(set(re.findall(r"\{[^}]+\}", md_text))),
        "markdown": md_text[:5000],
        "download_md": f"/api/download/{md_out.name}",
    }


@app.get("/api/export/{sid}")
def export_docx(sid: str):
    """投标初稿 md → Word（md2docx 排版转换），每次实时转换以包含最新修正。"""
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    md_path = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    if not md_path.exists():
        return JSONResponse(status_code=404, content={"error": "尚未生成投标初稿"})
    sys.path.insert(0, str(REPO / "scripts"))
    from md2docx import md_to_docx  # noqa: PLC0415

    out_path = OUTPUT_DIR / f"{sid}_投标文件初稿.docx"
    try:
        md_to_docx(md_path.read_text(encoding="utf-8"), str(out_path))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Word转换失败: {e}"})
    if not out_path.exists():
        return JSONResponse(status_code=500, content={"error": "Word转换失败: 未生成文件"})
    return FileResponse(out_path, filename=f"{_recover_stem(sid)}_投标文件初稿.docx")


@app.get("/api/download/{name}")
def download(name: str):
    """下载产物文件。"""
    path = OUTPUT_DIR / name
    if not path.is_file():
        return JSONResponse(status_code=404, content={"error": "文件不存在"})
    return FileResponse(path, filename=name.split("_", 1)[-1])


@app.get("/api/source/{sid}")
def source_file(sid: str):
    """返回上传的原始招标文件。inline 内嵌预览（如 PDF），不触发下载。"""
    sess = _get_session(sid)
    if not sess:
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    src = sess["src"]
    if not src.is_file():
        return JSONResponse(status_code=404, content={"error": "源文件不存在"})
    media = "application/pdf" if src.suffix.lower() == ".pdf" else "application/octet-stream"
    return FileResponse(src, media_type=media, headers={"Content-Disposition": "inline"})


# ─── 接口：历史记录 / 统计 ───────────────────────────────────

@app.get("/api/history")
def history_list():
    """分析记录列表（按创建时间倒序）。"""
    idx = _load_index()
    rows = []
    for sid, e in idx.items():
        rows.append({
            "sid": sid,
            "filename": e.get("filename", sid),
            "created_at": e.get("created_at", ""),
            "status": e.get("status", "uploaded"),
            "risk_total": e.get("risk_total"),
            "critical": e.get("critical"),
            "score": e.get("score"),
            "duration_sec": e.get("duration_sec"),
        })
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"items": rows, "total": len(rows)}


@app.get("/api/history/{sid}")
def history_detail(sid: str):
    """组装单条分析详情：要点 + 审标 + 初稿 md。缺失的产物按需补算/兜底。"""
    sess = _get_session(sid)
    if not sess:
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    idx = _load_index().get(sid, {})

    # 要点：缺失且未解析时按需补算
    points_path = OUTPUT_DIR / f"{sid}_要点.json"
    points = None
    if points_path.exists():
        try:
            points = json.loads(points_path.read_text(encoding="utf-8"))
        except Exception:
            points = None
    if points is None:
        try:
            points = _do_parse(sid)
        except Exception:
            points = None

    # 审标：优先读结构化 json，其次正则解析报告 md，最后现扫兜底
    review_data = None
    review_json = OUTPUT_DIR / f"{sid}_审标.json"
    if review_json.exists():
        try:
            review_data = json.loads(review_json.read_text(encoding="utf-8"))
        except Exception:
            review_data = None
    if review_data is None:
        report_md = OUTPUT_DIR / f"{sid}_审标报告.md"
        if report_md.exists():
            items = []
            pat = re.compile(r"- L(\d+) \[([^\]]+)\] `([^`]*)` (.*)")
            for line in report_md.read_text(encoding="utf-8").splitlines():
                m = pat.match(line)
                if m:
                    items.append({"行号": int(m.group(1)), "类别": m.group(2),
                                  "命中词": m.group(3), "上下文": m.group(4)})
            if items:
                stats: dict[str, int] = {}
                for it in items:
                    stats[it["类别"]] = stats.get(it["类别"], 0) + 1
                review_data = {"total": len(items), "stats": stats, "items": items}
        else:
            try:
                review_data = _do_review(sid)
            except Exception:
                review_data = None

    # 初稿 md
    draft_path = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    draft = draft_path.read_text(encoding="utf-8") if draft_path.exists() else None

    # 废标自查（生成初稿时产出，老记录可能没有）
    dq_check = None
    dq_path = OUTPUT_DIR / f"{sid}_废标自查.json"
    if dq_path.exists():
        try:
            dq_check = json.loads(dq_path.read_text(encoding="utf-8"))
        except Exception:
            dq_check = None

    # 原文 md（docx 转换产物 / pdf OCR 产物存在时返回，供详情页原文对照）
    src_md = None
    src = sess["src"]
    if src.suffix.lower() in {".docx", ".pdf"}:
        candidate = src.with_suffix(".md")
        if candidate.exists():
            src_md = candidate.read_text(encoding="utf-8")
    elif src.suffix.lower() in {".md", ".txt"}:
        src_md = src.read_text(encoding="utf-8")

    risk_total = review_data["total"] if review_data else idx.get("risk_total")
    critical = None
    if review_data:
        critical = sum(1 for it in review_data["items"] if str(it["类别"]).startswith("🔴"))

    return {
        "sid": sid,
        "filename": idx.get("filename", sess["stem"] + src.suffix),
        "created_at": idx.get("created_at", ""),
        "duration_sec": idx.get("duration_sec"),
        "risk_total": risk_total,
        "critical": critical,
        "score": max(0, 100 - risk_total) if isinstance(risk_total, int) else None,
        "points": points,
        "review": review_data,
        "dq_check": dq_check,
        "review_report": (OUTPUT_DIR / f"{sid}_审标报告.md").read_text(encoding="utf-8")
        if (OUTPUT_DIR / f"{sid}_审标报告.md").exists() else None,
        "draft": draft,
        "src_md": src_md[:20000] if src_md else None,
        "download_draft": f"/api/download/{sid}_投标文件初稿.md" if draft else None,
        "download_report": f"/api/download/{sid}_审标报告.md"
        if (OUTPUT_DIR / f"{sid}_审标报告.md").exists() else None,
    }


class DraftBody(BaseModel):
    markdown: str


@app.put("/api/history/{sid}/draft")
def save_draft(sid: str, body: DraftBody):
    """保存修正后的投标文件初稿。"""
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    (OUTPUT_DIR / f"{sid}_投标文件初稿.md").write_text(body.markdown, encoding="utf-8")
    return {"ok": True}


@app.delete("/api/history/{sid}")
def history_delete(sid: str):
    """删除该记录的全部落盘文件与索引项。"""
    if sid not in _load_index() and not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    removed = 0
    for f in OUTPUT_DIR.glob(f"{sid}_*"):
        if f.name == "index.json":
            continue
        f.unlink(missing_ok=True)
        removed += 1
    idx = _load_index()
    idx.pop(sid, None)
    _save_index(idx)
    SESSIONS.pop(sid, None)
    return {"ok": True, "removed": removed}


@app.get("/api/stats")
def stats():
    """工作台统计聚合。"""
    idx = _load_index()
    total = len(idx)
    risk_sum = sum(e["risk_total"] for e in idx.values() if isinstance(e.get("risk_total"), int))
    critical_sum = sum(e["critical"] for e in idx.values() if isinstance(e.get("critical"), int))
    durations = [e["duration_sec"] for e in idx.values() if isinstance(e.get("duration_sec"), (int, float)) and e["duration_sec"] > 0]
    reports = sum(1 for e in idx.values() if e.get("status") == "done")
    return {
        "total_files": total,
        "risk_total": risk_sum,
        "critical_total": critical_sum,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else 0,
        "report_count": reports,
        "critical_ratio": round(critical_sum * 100 / risk_sum) if risk_sum else 0,
        "pass_ratio": round((risk_sum - critical_sum) * 100 / risk_sum) if risk_sum else 0,
    }


# ─── 接口：AI 模型配置 ───────────────────────────────────────

def _mask_key(key: str) -> str:
    return f"sk-***{key[-4:]}" if key else ""


@app.get("/api/config/llm")
def get_llm_config():
    """读取 LLM 配置，api_key 脱敏返回。"""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            cfg = full.get("llm", {}) or {}
        except Exception:
            cfg = {}
    key = str(cfg.get("api_key") or "")
    return {
        "api_key": _mask_key(key),
        "has_key": bool(key),
        "base_url": cfg.get("base_url") or "",
        "model": cfg.get("model") or "",
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": cfg.get("max_tokens", 4096),
        "timeout": cfg.get("timeout", 60),
    }


class LlmConfigBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None


@app.put("/api/config/llm")
def put_llm_config(body: LlmConfigBody):
    """保存 LLM 配置。api_key 传脱敏占位（含 ***）时不覆盖原值。"""
    full: dict = {}
    if CONFIG_PATH.exists():
        try:
            full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            full = {}
    llm = full.get("llm") or {}

    updates = body.model_dump(exclude_none=True)
    for k, v in updates.items():
        if k == "api_key" and "***" in v:
            continue  # 脱敏占位，不回写
        llm[k] = v
    full["llm"] = llm
    # safe_dump 会丢注释，页面已提示；不引入 ruamel 保持依赖最小
    CONFIG_PATH.write_text(yaml.safe_dump(full, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "api_key": _mask_key(str(llm.get("api_key") or ""))}


# ─── 接口：企业素材库 ────────────────────────────────────────

@app.get("/api/company/{name}")
def get_company(name: str):
    """读取企业素材 md（白名单）。"""
    if name not in COMPANY_FILES:
        return JSONResponse(status_code=404, content={"error": "素材不存在"})
    path = COMPANY_DIR / f"{name}.md"
    return {"name": name, "content": path.read_text(encoding="utf-8") if path.exists() else ""}


class CompanyBody(BaseModel):
    content: str


@app.put("/api/company/{name}")
def put_company(name: str, body: CompanyBody):
    """保存企业素材 md（白名单）。"""
    if name not in COMPANY_FILES:
        return JSONResponse(status_code=404, content={"error": "素材不存在"})
    COMPANY_DIR.mkdir(parents=True, exist_ok=True)
    (COMPANY_DIR / f"{name}.md").write_text(body.content, encoding="utf-8")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
