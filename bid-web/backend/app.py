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
REPO = Path(__file__).resolve().parents[2] / "bid-toolkit-analysis"
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

from bid_generator import COMMERCIAL_KEYWORDS  # noqa: E402  # 大纲对齐判定商务标用

OUTPUT_DIR = REPO / "demo_output" / "web"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO / "templates" / "user_config.yaml"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"  # 固定使用 DeepSeek API 地址
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


_LLM_UNAVAIL_REASON: str | None = None


def _get_llm_client():
    """按设置页配置动态构建 LLM 客户端；未配置 api_key 时返回 None（走本地模板模式）。

    每次请求重新读取配置，设置页保存后无需重启后端即可生效。
    """
    global _LLM_UNAVAIL_REASON
    _LLM_UNAVAIL_REASON = None
    from llm_client import LLMClient

    if not CONFIG_PATH.exists():
        _LLM_UNAVAIL_REASON = "未找到 AI 配置文件"
        return None
    try:
        full = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        _LLM_UNAVAIL_REASON = "AI 配置文件读取失败"
        return None
    llm_cfg = full.get("llm") or {}
    if not str(llm_cfg.get("api_key") or "").strip():
        _LLM_UNAVAIL_REASON = "未配置 AI Key"
        return None
    try:
        import openai  # noqa: F401
    except ImportError:
        _LLM_UNAVAIL_REASON = "后端未安装 openai 库（pip install openai）"
        return None
    client = LLMClient(config_path=str(CONFIG_PATH))
    if not client.is_available():
        _LLM_UNAVAIL_REASON = "AI 客户端初始化失败，请检查 Key 与网络"
        return None
    return client


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


def _norm_score_cat(cat: str) -> str:
    """评分类别归一化：价格/技术/商务/资信 四大类，竞品对齐。"""
    c = str(cat or "")
    if "价格" in c or "报价" in c:
        return "价格分"
    if "技术" in c:
        return "技术分"
    if "商务" in c or "服务" in c:
        return "商务分"
    if "资信" in c or "业绩" in c or "信用" in c or "企业" in c:
        return "资信分"
    return c.strip() or "未分类"


def slim_points(result: dict, fusion: dict | None = None) -> dict:
    """提炼前端展示用的招标要点。

    fusion: 评分项双通道融合结果（scoring_fusion.extract_scoring_fusion）。
    提供时评分项/总分以融合结果为准（总分恒由程序计算，LLM 无权干预）。
    """
    src_items = (fusion or {}).get("items") or result.get("scoring_items", [])
    scoring = [
        {
            "类别": s.get("category", ""),
            "项目": s.get("item", ""),
            "分值": s.get("score", ""),
            "评审标准": (s.get("criteria", "") or "")[:200],
        }
        for s in src_items
        if s.get("item")
    ]
    quals = []
    for q in (result.get("qualification_requirements") or result.get("资质要求") or []):
        item = _clean_qual_item(q)
        if item:
            quals.append(item)
    points = {
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
    if fusion:
        points["评分校验"] = {
            "声明总分": fusion.get("declared_total"),
            "校验": fusion.get("total_check"),      # pass / fail / unknown
            "提取通道": fusion.get("channel"),       # rule / llm / fused
            "需人工复核": fusion.get("needs_review"),
            "冲突数": len(fusion.get("conflicts") or []),
            "数据来源": fusion.get("source_section", ""),
        }
    # 分类小计（竞品对齐："技术标评分项分值合计：78分"）
    cat_sum: dict[str, float] = {}
    for s in scoring:
        try:
            v = float(s["分值"])
        except (TypeError, ValueError):
            continue
        label = _norm_score_cat(s.get("类别", ""))
        cat_sum[label] = round(cat_sum.get(label, 0) + v, 2)
    points["评分分类汇总"] = cat_sum
    return points


def _risk_summary(items: list[dict]) -> tuple[int, int]:
    """(总风险数, 废标级风险数)。废标级 = 🔴 开头的类别。"""
    critical = sum(1 for it in items if str(it.get("类别", "")).startswith("🔴"))
    return len(items), critical


def _do_parse(sid: str) -> dict:
    """执行解析并落盘，返回 points。parse 端点与详情接口共用。"""
    parse_bid = _load_parse_bid()
    sess = SESSIONS[sid]
    md_path = normalize_to_md(sess["src"], sess)
    # docx 原件走结构化表格通道（评分5列表/合并单元格依赖此通道）
    extra_tables = None
    if Path(sess["src"]).suffix.lower() == ".docx":
        try:
            extra_tables = parse_bid.extract_tables_from_docx(str(sess["src"]))
        except Exception as e:
            print(f"docx表格通道失败（忽略）: {e}", file=sys.stderr)
    # 智能体：规则解析 + 评分融合 + 模型大纲起草 + 规则验收回修
    import analysis_agent  # noqa: PLC0415

    llm = _get_llm_client()
    bundle = analysis_agent.run(
        sid,
        str(md_path),
        str(sess["src"]),
        extra_tables,
        OUTPUT_DIR,
        llm,
        match_score=_title_match_score,
        clean=_clean_checklist_item,
        norm=_norm_title,
        llm_unavail_reason=_LLM_UNAVAIL_REASON,
    )
    result = bundle["rfp"]
    fusion = bundle["fusion"]
    sess["agent"] = bundle.get("agent")
    sess["outline"] = bundle.get("outline")

    points = slim_points(result, fusion)
    if bundle.get("agent"):
        points["智能体"] = {
            "mode": bundle["agent"].get("mode"),
            "llm_used": bundle["agent"].get("llm_used"),
            "outline_source": bundle["agent"].get("outline_source"),
            "outline_chapters": bundle["agent"].get("outline_chapters"),
            "checklist_meta": bundle["agent"].get("checklist_meta") or {},
            "scoring_tree_parents": bundle["agent"].get("scoring_tree_parents"),
            "scoring_tree_children": bundle["agent"].get("scoring_tree_children"),
            "warning": bundle["agent"].get("warning"),
            "steps": bundle["agent"].get("steps") or [],
            "gaps": bundle["agent"].get("gaps") or {},
        }
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

    # 结构化落盘（详情接口直接读取，避免重启后重扫；items 存全量与 total 一致）
    structured = {"total": len(items), "stats": stats, "items": items}
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
    sess = _get_session(sid) or {}
    outline = sess.get("outline") or {}
    agent = points.get("智能体") or sess.get("agent") or {}
    return {
        "points": points,
        "agent": agent,
        "outline": {
            "source": (outline or {}).get("source") or agent.get("outline_source"),
            "chapters": len((outline or {}).get("sections") or []),
            "gaps": (outline or {}).get("gaps") or {},
            "checklist_meta": (outline or {}).get("checklist_meta")
            or agent.get("checklist_meta")
            or {},
            "scoring_tree": (outline or {}).get("scoring_tree") or [],
        },
        "summary": {
            "废标红线": len(points["废标红线"]),
            "评分项": len(points["评分项"]),
            "评分总分": points["评分总分"],
            "资质要求": len(points["资质要求"]),
            "文件清单": len(points["文件清单"]),
            "OCR页数": points.get("OCR识别", {}).get("OCR页数", 0),
            "总页数": points.get("OCR识别", {}).get("总页数", 0),
            "大纲章节": len((outline or {}).get("sections") or []),
            "智能体模式": agent.get("mode"),
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
    name = re.sub(r"^第[一二三四五六七八九十百\d]+[项章节篇]\s*", "", name)
    name = re.sub(r"[（(][^)）]*[)）]\s*$", "", name)
    return name.strip()


_CN_NUM_PREFIX = re.compile(r"^[一二三四五六七八九十百]+\s*[、.．]\s*")


def _norm_title(name: str) -> str:
    """章节名归一化：去序号前缀、空白与尾部括注，用于模糊匹配。"""
    name = _clean_checklist_item(name)
    name = _CN_NUM_PREFIX.sub("", name)
    return re.sub(r"\s+", "", name)


def _lcs_substring(a: str, b: str) -> str:
    """最长公共子串（滚动数组，标题短，开销可忽略）。"""
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best, best_end = 0, 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, best_end = cur[j], j
        prev = cur
    return b[best_end - best:best_end] if best else ""


# 仅靠这些泛词命中不算匹配（"说明/情况"这类词各章节名里太常见）
_GENERIC_TOKENS = {"说明", "情况", "相关", "其他", "有关", "要求", "格式", "文件", "材料"}


def _title_match_score(title: str, item: str) -> float:
    """大纲章节名与清单条目的匹配分（纯字符串统计，不含项目特定词表）。

    信号：最长公共子串 + 2×共有字符 + 包含关系分级加成 + 尾部核心词加成。
    - 名称几乎相等（差≤2字）：同一份文件，强匹配；
    - 短名整体包含于长名且短名≥4字（如"投标保证金"⊂"投标保证金交纳证明"）：
      核心相同，强匹配；
    - 包含但短名≤3字（如"承诺书"⊂"采购代理服务费支付承诺书"）：
      大概率是另一份专门文件，降权；
    - 仅靠泛词（"说明"等）命中的公共子串不计分。
    """
    if not title or not item:
        return 0.0
    lcs = _lcs_substring(title, item)
    lcs_score = 0.0 if lcs in _GENERIC_TOKENS else float(len(lcs))
    score = lcs_score + 2.0 * len(set(title) & set(item))
    # 尾部核心词加成（如"…业绩"对"…业绩情况表"），放在包含降权之前一并参与惩罚
    if len(title) >= 2 and title[-2:] in item:
        score += 2.0
    if title == item or (title in item or item in title) and abs(len(title) - len(item)) <= 2:
        score += 10.0
    elif title in item or item in title:
        contained = title if title in item else item
        if len(contained) >= 4:
            score += 10.0
        else:
            score *= 0.4
    return score


# 无匹配时"证照类"章节的挂靠优先级（通用投标常识：营业执照=民事责任能力证明）
_CERT_KEYWORDS = (("民事责任", 5.0), ("独立承担", 5.0), ("资质", 3.0), ("证明材料", 2.0))
# 兜底挂靠判定：得分低于阈值的章节挂到附件最多的"技术大项"上
_MATCH_THRESHOLD = 6.0


def _align_outline_with_checklist(
    sections: list[dict], checklist: list
) -> tuple[list[dict], list[str]]:
    """按招标文件「投标文件目录/组成清单」重建大纲。

    目录的条目名称与顺序为最高优先级：默认大纲章节模糊匹配到目录条目后并入
    （作为其子节内容），目录有而大纲没有的条目补占位，大纲有而目录没有的
    章节降级挂靠到最接近的条目下，确保产物结构与目录一一对应。
    返回 (重建后的章节列表, 说明列表)。
    """
    if not checklist:
        return sections, []

    # 1) 清洗目录条目（保持给定顺序）
    items: list[str] = []
    for raw in checklist:
        name = _clean_checklist_item(str(raw))
        norm = _norm_title(str(raw))
        if not norm or len(norm) > 30 or name in items:
            continue
        items.append(name)

    # 2) 大纲章节 → 目录条目 最佳匹配（每个章节只归入一个条目）
    attach: dict[int, list[dict]] = {i: [] for i in range(len(items))}
    unmatched: list[dict] = []
    for sec in sections:
        t = _norm_title(sec["title"])
        if not t:
            continue
        best_i, best_s = -1, 0.0
        for i, name in enumerate(items):
            s = _title_match_score(t, _norm_title(name))
            if s > best_s:
                best_i, best_s = i, s
        if best_i >= 0 and best_s >= _MATCH_THRESHOLD:
            attach[best_i].append(sec)
        else:
            unmatched.append(sec)

    # 3) 未匹配章节兜底：证照类按关键词挂靠；其余挂到已并入章节最多的条目（技术大项）
    notes: list[str] = []
    for sec in unmatched:
        t = _norm_title(sec["title"])
        target = -1
        if any(k in t for k in ("营业执照", "资质", "许可", "证照")):
            best_w = 0.0
            for i, name in enumerate(items):
                w = sum(weight for kw, weight in _CERT_KEYWORDS if kw in name)
                if w > best_w:
                    target, best_w = i, w
        if target < 0:
            target = max(attach, key=lambda i: len(attach[i]))
        attach[target].append(sec)
        notes.append(f"{sec['title']} → {items[target]}")

    # 4) 按目录顺序重建章节：有并入内容的用并入内容，否则补占位
    placeholder = "招标文件「投标文件组成」明确要求包含本项，请按招标文件给定格式编制，勿遗漏。"
    rebuilt: list[dict] = []
    for i, name in enumerate(items):
        atts = attach[i]
        if len(atts) == 1 and _norm_title(atts[0]["title"]) == _norm_title(name):
            content = atts[0].get("content", "")
        else:
            content = "\n\n".join(
                f"### {a['title']}\n\n{a.get('content', '')}".strip() for a in atts
            )
        rebuilt.append({
            "index": len(rebuilt) + 1,
            "title": name,
            "content": content or placeholder,
            "part": atts[0]["part"] if atts else "其他",
            "is_commercial": any(k in name for k in COMMERCIAL_KEYWORDS),
        })
    return rebuilt, notes


def _load_llm_outline_sections(sid: str) -> tuple[list[dict] | None, list[str]]:
    """读取分析阶段 LLM 大纲产物（llm_outline.py 落盘的 {sid}_大纲.json）。

    返回 (sections, checklist_added)；文件不存在/JSON 损坏/结构非法时
    返回 (None, [])，调用方回退「模板 + 清单对齐」规则路径。
    checklist_added 取 coverage.missing_patched（程序补占位的清单条目名）。
    """
    path = OUTPUT_DIR / f"{sid}_大纲.json"
    if not path.exists():
        return None, []
    try:
        import llm_outline  # noqa: PLC0415  # 函数内导入，与 scoring_fusion 同模式

        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("sections")
        if not isinstance(raw, list) or not raw:
            return None, []
        sections = llm_outline.finalize_sections(raw)  # 读取侧再裁决一次，防手改损坏
        if not sections:
            return None, []
        cov = data.get("coverage") or {}
        added = cov.get("missing_patched")
        return sections, [str(x) for x in added] if isinstance(added, list) else []
    except Exception as e:  # noqa: BLE001 - 大纲产物不可用一律回退规则路径
        print(f"LLM大纲读取失败（回退规则路径）: {e}", file=sys.stderr)
        return None, []


@app.post("/api/generate/{sid}")
def generate(sid: str, use_ai: bool = False):
    """生成投标文件初稿。

    默认（use_ai=False）仅本地模板骨架模式，不调用 AI；
    仅当前端显式传入 use_ai=true 且已配置 LLM Key 时，才逐章节 AI 生成正文。
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from bid_generator import (  # noqa: PLC0415
        load_company_profiles,
        build_rfp_summary,
        parse_outline,
        generate_commercial_section,
        generate_technical_section,
        inject_company_material,
        detect_bid_type,
        COMMERCIAL_KEYWORDS,
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

    # 2) 自动识别标书类型（货物/服务/工程）；类型用于响应字段与回退路径选大纲
    try:
        rfp_text = read_input_file(str(md_path))
    except Exception:
        rfp_text = ""
    bid_type, detection_info = detect_bid_type(
        rfp_text, TEMPLATES_DIR / "bid_type_detection.yaml"
    )

    # 2.5) 大纲：优先消费分析阶段 LLM 大纲产物（llm_outline.py，清单硬约束）；
    #      缺失/损坏（老记录、分析时 LLM 失败）回退「静态模板 + 清单对齐」规则路径
    checklist = [str(x).strip() for x in (rfp_data.get("文件清单") or []) if str(x).strip()]
    sections, checklist_added = _load_llm_outline_sections(sid)
    if sections is None:
        outline_path = TEMPLATES_DIR / OUTLINE_FILES.get(bid_type, "service_outline.md")
        sections = parse_outline(outline_path)
        # 目录条目的名称与顺序为最终结构，默认大纲章节匹配并入，缺失条目补占位
        sections, checklist_added = _align_outline_with_checklist(sections, checklist)

    # 3) 企业素材（演示用占位素材目录）
    profiles = load_company_profiles(COMPANY_DIR)

    # 4) 组装：商务标模板填充；技术标仅在 use_ai=True 且已配置 LLM 时 AI 生成正文，否则骨架占位
    llm = _get_llm_client() if use_ai else None
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
            inject_company_material(sec, profiles)
            md_parts.append(f"\n## {sec['title']}\n")
            req = sec.get("content", "")
            md_parts.append(f"{req}\n")
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
def export_docx(
    sid: str,
    engine: str = "toolkit",
    template: str = "government",
    dark: bool = False,
):
    """投标初稿 md → Word。

    - engine=toolkit（默认）：走标书排版引擎 bid_engine + 格式模板
    - engine=simple：旧版简易 md2docx
    - template：government|enterprise|engineering|goods
    - dark：暗标模式
    """
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    md_path = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    if not md_path.exists():
        return JSONResponse(status_code=404, content={"error": "尚未生成投标初稿"})
    out_path = OUTPUT_DIR / f"{sid}_投标文件初稿.docx"
    try:
        if engine == "simple":
            sys.path.insert(0, str(REPO / "scripts"))
            from md2docx import md_to_docx  # noqa: PLC0415

            md_to_docx(md_path.read_text(encoding="utf-8"), str(out_path))
        else:
            import toolkit_bridge  # noqa: PLC0415

            toolkit_bridge.export_with_engine(
                md_path, out_path, template=template or "government", dark_mode=bool(dark),
            )
            meta = {
                "template": template,
                "dark_mode": bool(dark),
                "engine": "bid_engine",
            }
            (OUTPUT_DIR / f"{sid}_排版.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
            )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Word转换失败: {e}"})
    if not out_path.exists():
        return JSONResponse(status_code=500, content={"error": "Word转换失败: 未生成文件"})
    return FileResponse(out_path, filename=f"{_recover_stem(sid)}_投标文件初稿.docx")


@app.get("/api/toolkit/capabilities")
def toolkit_capabilities():
    """源码能力矩阵：哪些已接到 Web。"""
    import toolkit_bridge  # noqa: PLC0415

    return {
        "templates": toolkit_bridge.list_format_templates(),
        "capabilities": toolkit_bridge.capability_matrix(),
    }


@app.post("/api/toolkit/{sid}/format-check")
def toolkit_format_check(sid: str, auto_fix: bool = False):
    """对已导出的 Word 做格式自检（fix_bid_format）。无 docx 时先用排版引擎生成。"""
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    import toolkit_bridge  # noqa: PLC0415

    md_path = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    docx_path = OUTPUT_DIR / f"{sid}_投标文件初稿.docx"
    if not docx_path.exists():
        if not md_path.exists():
            return JSONResponse(status_code=404, content={"error": "尚未生成投标初稿"})
        try:
            toolkit_bridge.export_with_engine(md_path, docx_path, template="government")
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"先导出 Word 失败: {e}"})
    try:
        result = toolkit_bridge.format_check_docx(docx_path, auto_fix=auto_fix)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"格式自检失败: {e}"})
    (OUTPUT_DIR / f"{sid}_格式自检.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    if result.get("report"):
        (OUTPUT_DIR / f"{sid}_格式自检报告.txt").write_text(result["report"], encoding="utf-8")
    return result


@app.post("/api/toolkit/{sid}/ai-flavor")
def toolkit_ai_flavor(sid: str):
    """对投标初稿做 AI 味检测。"""
    if not _get_session(sid):
        return JSONResponse(status_code=404, content={"error": "记录不存在"})
    import toolkit_bridge  # noqa: PLC0415

    md_path = OUTPUT_DIR / f"{sid}_投标文件初稿.md"
    if not md_path.exists():
        return JSONResponse(status_code=404, content={"error": "尚未生成投标初稿"})
    try:
        result = toolkit_bridge.ai_flavor_scan_file(md_path)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"AI味检测失败: {e}"})
    (OUTPUT_DIR / f"{sid}_AI味.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


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

    outline = None
    outline_path = OUTPUT_DIR / f"{sid}_大纲.json"
    if outline_path.exists():
        try:
            outline = json.loads(outline_path.read_text(encoding="utf-8"))
        except Exception:
            outline = None
    agent = (points or {}).get("智能体")
    agent_path = OUTPUT_DIR / f"{sid}_智能体.json"
    if agent_path.exists():
        try:
            agent = json.loads(agent_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _load_json(name: str):
        p = OUTPUT_DIR / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    toolkit = {
        "format_check": _load_json(f"{sid}_格式自检.json"),
        "ai_flavor": _load_json(f"{sid}_AI味.json"),
        "layout": _load_json(f"{sid}_排版.json"),
    }

    return {
        "sid": sid,
        "filename": idx.get("filename", sess["stem"] + src.suffix),
        "created_at": idx.get("created_at", ""),
        "duration_sec": idx.get("duration_sec"),
        "risk_total": risk_total,
        "critical": critical,
        "score": max(0, 100 - risk_total) if isinstance(risk_total, int) else None,
        "points": points,
        "outline": outline,
        "agent": agent,
        "review": review_data,
        "dq_check": dq_check,
        "toolkit": toolkit,
        "review_report": (OUTPUT_DIR / f"{sid}_审标报告.md").read_text(encoding="utf-8")
        if (OUTPUT_DIR / f"{sid}_审标报告.md").exists() else None,
        "draft": draft,
        "src_md": src_md[:20000] if src_md else None,
        "download_draft": f"/api/download/{sid}_投标文件初稿.md" if draft else None,
        "download_report": f"/api/download/{sid}_审标报告.md"
        if (OUTPUT_DIR / f"{sid}_审标报告.md").exists() else None,
        "download_docx": f"/api/export/{sid}" if draft else None,
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
        "base_url": DEEPSEEK_BASE_URL,  # 固定 DeepSeek 地址
        "model": cfg.get("model") or "deepseek-chat",
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
        if k == "base_url":
            continue  # base_url 固定为 DeepSeek，忽略前端传值
        llm[k] = v
    llm["base_url"] = DEEPSEEK_BASE_URL  # 强制写死
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
