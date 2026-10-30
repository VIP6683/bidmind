"""LLM 大纲生成（分析阶段独立模块，可整体回退）。

职责：招标文件解析 + 评分融合完成后，调用 LLM 生成投标初稿章节大纲，
落盘 `{sid}_大纲.json` 供初稿生成（app.generate 第 2.5 步）优先消费。

设计原则：
    - 独立文件独立方法，不改动现有解析/初稿流水线，便于紧急回退
    - 招标文件「投标文件组成清单」为硬约束：程序在 LLM 输出后做覆盖校验
      （enforce_checklist），缺失条目按原位置补占位、保持清单相对顺序
    - LLM 不可用 / 返回为空 / JSON 损坏一律返回 None（不落盘、不抛出），
      初稿自然回退「静态模板 + _align_outline_with_checklist」规则路径
    - 与 app.py 通过可选参数依赖注入复用其 _title_match_score 等匹配器，
      本模块不反向 import app，避免循环导入

提示词规范文档：bid-toolkit-analysis/docs/prompts/outline_generation.md
（代码内 SYSTEM_PROMPT 与文档保持同步，md 仅为规范说明）
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ─── 常量 ────────────────────────────────────────────────────

# 占位文案与 app.py _align_outline_with_checklist 逐字一致
_PLACEHOLDER = "招标文件「投标文件组成」明确要求包含本项，请按招标文件给定格式编制，勿遗漏。"

# 清单条目过滤：归一化后超长视为噪声（政采目录项偶发较长）
_MAX_ITEM_LEN = 40

# 清单条目 ↔ LLM 章节的匹配阈值（与 app.py _MATCH_THRESHOLD 一致）
_MATCH_THRESHOLD = 6.0

# 需求章节摘录上限（保首尾截断，与 scoring_fusion 同手法）
_DEMAND_LIMIT = 12000
# 评分标准章节摘录上限
_SCORING_LIMIT = 8000

# 评分子项：xxx（0-6分）/ xxx（0～6分）
_SUB_SCORE_RE = re.compile(
    r'([\u4e00-\u9fffA-Za-z0-9、/]{2,40}?)'
    r'[（(]\s*(0\s*[-~～至到]\s*\d+(?:\.\d+)?)\s*分\s*[)）]'
)

SYSTEM_PROMPT = (
    "你是资深投标文件结构专家，熟悉政府采购与工程招投标。"
    "你的任务是设计「给人看的投标文件大纲」（兼作下游写正文提纲），"
    "只输出严格 JSON，不输出解释或 markdown 围栏。\n"
    "硬性规则：\n"
    "1. 「投标文件组成清单」是唯一骨架：每个条目必须对应一章，"
    "标题保留清单原文，相对顺序不得打乱；不得用货物/服务通用模板章名替换。\n"
    "2. 语义归类：投标函/报价/授权/声明函/承诺书/偏离表/业绩等 part=商务标；"
    "技术文件及评分要求的实施方案/人员/质量/应急/售后等 part=技术标。\n"
    "3. 评分树必须落地：父项与子项均要在 content 中出现名称+分值；"
    "若清单有「技术文件」等总括章，把高分技术评分项作为该章下的子节要点展开"
    "（例如项目实施方案拆成进度/运输/验收/装卸/退换货各0-6分），也可在总括章后"
    "紧跟补充技术专章，但不得打乱清单原有条目顺序。\n"
    "4. 禁止编造清单中没有的商务文件名；禁止添加与本项目无关的章节"
    "（如无安装要求则不要「安装调试」「培训方案」）。\n"
    "5. 每章 content 写 4-8 条可执行编制要点（子节标题+评分锚点+招标硬点），"
    "面向投标人填写/撰写，不写空洞套话；人工数据用 {中文占位符}。\n"
    "6. 采购需求硬点必须显式写入对应章：如封条防掺假、近三个月社保、"
    "人员数量下限、中小企业行业划型、响应时限、煤质指标等。\n"
    "7. 有清单时章节数≈清单条数+必要技术补充章；无清单时才可按评分树组织，"
    "并在首章 content 注明「目录未识别，待人工核对」。\n"
    "输出格式：{\"sections\":[{\"title\":\"章节名\",\"content\":\"编制要求\","
    "\"part\":\"商务标|技术标|其他\"}]}"
)

CHECKLIST_RECOVER_PROMPT = (
    "你从招标文件「第六章 投标文件格式」或「投标文件组成/目录」中提取投标文件目录条目。"
    "只输出 JSON：{\"items\":[\"投标函\",\"报价一览表\",...]}。"
    "要求：保留原文短标题；按原文顺序；不要格式正文里的填空段；不要解释。"
)

# 内置默认商务关键词（与 bid_generator.COMMERCIAL_KEYWORDS 一致；
# 运行环境已挂载 REPO/scripts 时优先用真源，此处仅作导入失败兜底）
_FALLBACK_COMMERCIAL_KEYWORDS = [
    "投标函", "开标一览表", "报价明细", "报价表", "中小企业声明函",
    "投标保证金", "授权委托书", "营业执照", "资质证书", "保证金",
]


def _commercial_keywords() -> list[str]:
    try:
        from bid_generator import COMMERCIAL_KEYWORDS  # noqa: PLC0415
        return list(COMMERCIAL_KEYWORDS)
    except Exception:  # noqa: BLE001 - 脱离 app 环境单测时兜底
        return _FALLBACK_COMMERCIAL_KEYWORDS


# ─── 内置默认清洗/匹配（app.py 未注入时使用）──────────────────

def _default_clean(item: str) -> str:
    """清洗清单条目：去序号与尾部括注（与 app.py _clean_checklist_item 同规则）。"""
    name = re.sub(r"^[（(]\d+[)）]\s*", "", item)
    name = re.sub(r"^\d+\s*[、.．]\s*", "", name)
    name = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]", "", name)
    name = re.sub(r"^第[一二三四五六七八九十百\d]+[项章节篇]\s*", "", name)
    name = re.sub(r"[（(][^)）]*[)）]\s*$", "", name)
    return name.strip()


def _default_norm(name: str) -> str:
    """章节名归一化：去序号前缀与空白（与 app.py _norm_title 同规则）。"""
    name = _default_clean(name)
    name = re.sub(r"^[一二三四五六七八九十百]+\s*[、.．]\s*", "", name)
    return re.sub(r"\s+", "", name)


def _default_match_score(title: str, item: str) -> float:
    """简化匹配：归一化互相包含强匹配，否则共有字符计分（app 注入 _title_match_score 时不用此函数）。"""
    if not title or not item:
        return 0.0
    if title == item:
        return 20.0
    if title in item or item in title:
        return 10.0
    return 2.0 * len(set(title) & set(item))


# ─── 输入组装 ────────────────────────────────────────────────

def _clean_checklist(raw_items: list, clean) -> list[str]:
    """清洗去重清单条目，超长过滤（与 app.py L639-646 同规则）。"""
    items: list[str] = []
    for raw in raw_items:
        name = clean(str(raw))
        norm = _default_norm(str(raw))
        if not norm or len(norm) > _MAX_ITEM_LEN or name in items:
            continue
        items.append(name)
    return items


def _iter_scoring_items(rfp_data: dict, fusion: dict | None) -> list[dict]:
    """统一取出评分条目列表（fusion 优先）。"""
    if isinstance(fusion, dict) and isinstance(fusion.get("items"), list) and fusion["items"]:
        return [it for it in fusion["items"] if isinstance(it, dict)]
    raw = rfp_data.get("scoring_items") or []
    return [it for it in raw if isinstance(it, dict)]


def _item_name_score_criteria(it: dict) -> tuple[str, str, str]:
    name = str(it.get("item") or it.get("评分项") or it.get("名称") or "").strip()
    score = str(it.get("score") or it.get("分值") or "").strip()
    criteria = str(it.get("criteria") or it.get("评分标准") or "").strip()
    return name, score, criteria


def expand_scoring_children(criteria: str) -> list[dict]:
    """从评分标准原文拆「名称（0-N分）」子项，供大纲强制展开。"""
    if not criteria:
        return []
    children: list[dict] = []
    seen: set[str] = set()
    for m in _SUB_SCORE_RE.finditer(criteria):
        name = re.sub(r'^[（(\d)）]、.\s]+', '', m.group(1)).strip(" ：:，,、")
        span = m.group(2).replace(" ", "")
        key = re.sub(r"\s+", "", name)
        if len(key) < 2 or key in seen:
            continue
        # 过滤类目废话
        if name in ("得分", "分值", "满分", "评分"):
            continue
        seen.add(key)
        children.append({"item": name, "score": span, "criteria": ""})
    return children[:12]


def _enrich_criteria_from_md(name: str, criteria: str, md_text: str) -> str:
    """评分标准在规则通道常被截断到 200~300 字；从评标章节原文补全以便拆子项。"""
    if not md_text or not name:
        return criteria
    # 已有足够子项信号则不扩
    if len(expand_scoring_children(criteria)) >= 3:
        return criteria
    section = _locate_scoring_section(md_text) or md_text
    key = re.sub(r"\s+", "", name)
    # 在评标原文中定位父项名，向后取一段
    for m in re.finditer(re.escape(name), section):
        window = section[m.start(): m.start() + 1800]
        if "0-" in window or "0～" in window or "0~" in window or "分）" in window:
            # 合并：原文窗口优先（信息更全）
            return window if len(window) > len(criteria) else criteria
    # 宽松：去空白后找
    compact = re.sub(r"\s+", "", section)
    idx = compact.find(key)
    if idx >= 0:
        # 无法精确映射回原文位置时，至少用 criteria + 窗口内子项句
        pass
    return criteria


def build_scoring_tree(
    rfp_data: dict,
    fusion: dict | None,
    md_text: str | None = None,
) -> list[dict]:
    """父项 + 规则拆出的 children，供提示词与覆盖校验。"""
    cached = fusion.get("scoring_tree") if isinstance(fusion, dict) else None
    if isinstance(cached, list) and cached and not md_text:
        # 已由上游补全过；无新 md 时直接复用
        return cached

    tree: list[dict] = []
    for it in _iter_scoring_items(rfp_data, fusion)[:40]:
        name, score, criteria = _item_name_score_criteria(it)
        if not name:
            continue
        # 噪声：分值比重/权值说明
        if "分值比重" in name or name in ("技术部分", "商务部分"):
            continue
        if md_text:
            criteria = _enrich_criteria_from_md(name, criteria, md_text)
        kids = it.get("children") if isinstance(it.get("children"), list) else None
        if not kids:
            kids = expand_scoring_children(criteria)
        node = {
            "item": name,
            "score": score,
            "category": str(it.get("category") or it.get("类别") or "").strip(),
            "criteria": criteria[:800],
            "children": kids,
        }
        tree.append(node)
    return tree


def _collect_scoring_lines(rfp_data: dict, fusion: dict | None) -> list[str]:
    """评分树 → 提示词行（含子项）。"""
    lines: list[str] = []
    for it in build_scoring_tree(rfp_data, fusion):
        cat = it.get("category") or ""
        name, score = it["item"], it.get("score") or ""
        head = f"- [{cat}] {name}（{score}分）" if score else f"- [{cat}] {name}"
        if it.get("criteria"):
            head += f"：{it['criteria'][:180]}"
        lines.append(head)
        for ch in it.get("children") or []:
            cn = str(ch.get("item") or "").strip()
            cs = str(ch.get("score") or "").strip()
            if not cn:
                continue
            lines.append(f"  - 子项 {cn}（{cs}分）" if cs else f"  - 子项 {cn}")
    return lines


def _build_project_brief(rfp_data: dict) -> str:
    """项目简报：名称/预算/期限/保证金/资质门槛（截断控量）。"""
    parts: list[str] = []
    name = str(rfp_data.get("文件名") or "").strip()
    if name:
        parts.append(f"项目：{name}")
    budget = rfp_data.get("预算") or []
    if budget:
        parts.append("预算：" + "；".join(str(x)[:80] for x in budget[:5]))
    times = rfp_data.get("时间节点") or []
    if times:
        parts.append("时间节点：" + "；".join(str(x)[:80] for x in times[:8]))
    deposit = str(rfp_data.get("保证金") or "").strip()
    if deposit:
        parts.append(f"保证金：{deposit[:80]}")
    quals = rfp_data.get("资质要求") or []
    if quals:
        parts.append("资质门槛：\n" + "\n".join(f"- {str(x)[:100]}" for x in quals[:10]))
    return "\n".join(parts) if parts else "（未提取到项目简报）"


def _locate_section_by_keywords(md_text: str, keywords: str, limit: int) -> str:
    """按关键词定位一级章节，保首尾截断；找不到返回空串。

    keywords 为 | 分隔的正则交替项，如 "采购需求|技术需求"。
    """
    if not md_text:
        return ""
    heading = re.compile(
        rf"^#{{1,4}}\s*.*({keywords}).*$", re.MULTILINE,
    )
    matches = list(heading.finditer(md_text))
    if not matches:
        return ""
    m0 = matches[0]
    # 章节终点 = 下一个同级或更高级标题（按 # 数量），取不到就到文末
    level = len(re.match(r"^#+", md_text[m0.start():]).group(0))
    next_h = re.compile(rf"^#{{1,{level}}}\s", re.MULTILINE)
    nxt = next_h.search(md_text, m0.end())
    start, end = m0.start(), nxt.start() if nxt else len(md_text)
    section = md_text[start:end].strip()
    if len(section) > limit:
        section = (
            section[: limit * 3 // 4]
            + "\n……（中间省略）……\n"
            + section[-limit // 4:]
        )
    return section


def _locate_demand_section(md_text: str) -> str:
    """定位采购/技术/服务需求章节，保首尾截断；找不到返回空串。"""
    return _locate_section_by_keywords(
        md_text, "采购需求|技术需求|服务需求|项目需求|采购内容|技术要求", _DEMAND_LIMIT,
    )


def _locate_scoring_section(md_text: str) -> str:
    """定位评标办法/评分标准章节原文（分值锚定与子节细化的关键输入）。"""
    return _locate_section_by_keywords(
        md_text, "评标办法|评分标准|评标方法|评分细则|评审标准", _SCORING_LIMIT,
    )


def _template_titles(md_text: str) -> list[str]:
    """按类型识别取静态大纲模板的章节标题列表（仅供 LLM 参考）。"""
    try:
        from bid_generator import (  # noqa: PLC0415
            TEMPLATES_DIR, OUTLINE_FILES, detect_bid_type, parse_outline,
        )
        bid_type, _ = detect_bid_type(md_text, TEMPLATES_DIR / "bid_type_detection.yaml")
        outline_path = TEMPLATES_DIR / OUTLINE_FILES.get(bid_type, "service_outline.md")
        sections = parse_outline(outline_path)
        return [s["title"] for s in sections]
    except Exception:  # noqa: BLE001 - 参考信息取不到不影响主流程
        return []


# ─── LLM 输出解析（照抄 scoring_fusion 三级容错模式）──────────

def _parse_llm_json(resp: str) -> dict:
    """解析 LLM 返回的 JSON（容忍 ```json 围栏 / 前后杂文本）。"""
    resp = resp.strip()
    resp = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp)
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        s, e = resp.find("{"), resp.rfind("}")
        if s == -1 or e <= s:
            raise RuntimeError("LLM返回中未找到JSON") from None
        data = json.loads(resp[s:e + 1])
    if not isinstance(data, dict):
        raise RuntimeError("LLM返回不是JSON对象")
    return data


# ─── 核心逻辑 ────────────────────────────────────────────────

def enforce_checklist(
    llm_sections: list[dict],
    checklist: list[str],
    clean=None,
    norm=None,
    match_score=None,
    threshold: float = _MATCH_THRESHOLD,
) -> tuple[list[dict], dict]:
    """清单硬约束程序裁决：缺失条目按原位置补占位、保持清单相对顺序。

    参数：
        llm_sections: LLM 输出的章节列表（[{title, content, part}]）
        checklist: 清洗后的清单条目
        clean/norm/match_score: app.py 注入的清洗/归一化/匹配函数；
            None 时用内置简化版（保证独立可测）

    返回：
        (重建后的章节列表, coverage 统计)
        coverage: {"total", "matched", "missing_patched", "extras", "skipped"}
    """
    clean = clean or _default_clean
    norm = norm or _default_norm
    match_score = match_score or _default_match_score

    secs = [s for s in llm_sections if isinstance(s, dict) and str(s.get("title") or "").strip()]
    items: list[str] = []
    for raw in checklist:
        name = clean(str(raw))
        if not norm(str(raw)) or name in items:
            continue
        items.append(name)

    if not items:
        return secs, {"skipped": True, "total": 0, "matched": 0,
                      "missing_patched": [], "extras": []}

    # 1) 清单条目按序绑定未占用的最佳 LLM 章节（子序列匹配，保序）
    bound: dict[int, int] = {}   # checklist_index -> llm_section_index
    used: set[int] = set()
    cursor = 0                   # 只向后搜索，强制清单条目相对顺序
    for ci, item in enumerate(items):
        t = norm(item)
        best_si, best_s = -1, 0.0
        for si in range(cursor, len(secs)):
            if si in used:
                continue
            s = match_score(t, norm(str(secs[si].get("title") or "")))
            if s > best_s:
                best_si, best_s = si, s
        if best_si >= 0 and best_s >= threshold:
            bound[ci] = best_si
            used.add(best_si)
            cursor = best_si + 1

    # 2) 未绑定的 LLM 章节（extras）挂到原位置前方最近的已绑定条目之后
    extra_after: dict[int, list[dict]] = {}   # checklist_index -> [section]
    tail_extras: list[dict] = []
    anchors = sorted(bound.values())           # 已绑定章节的原位置（升序）
    for si, sec in enumerate(secs):
        if si in used:
            continue
        prev = [a for a in anchors if a < si]
        if prev:
            ci_of_anchor = next(ci for ci, a in bound.items() if a == prev[-1])
            extra_after.setdefault(ci_of_anchor, []).append(sec)
        else:
            tail_extras.append(sec)

    # 3) 按清单顺序重建：缺失条目补占位；extras 跟在其锚点条目后
    rebuilt: list[dict] = []
    missing: list[str] = []
    for ci, item in enumerate(items):
        if ci in bound:
            rebuilt.append(secs[bound[ci]])
        else:
            missing.append(item)
            rebuilt.append({"title": item, "content": _PLACEHOLDER, "part": None})
        rebuilt.extend(extra_after.get(ci, []))
    rebuilt.extend(tail_extras)

    coverage = {
        "skipped": False,
        "total": len(items),
        "matched": len(bound),
        "missing_patched": missing,
        "extras": [str(s.get("title") or "") for s in tail_extras]
        + [str(s.get("title") or "") for grp in extra_after.values() for s in grp],
    }
    return rebuilt, coverage


def finalize_sections(raw_sections: list[dict]) -> list[dict]:
    """字段程序裁决，输出与 parse_outline 逐字段同构的章节列表。

    is_commercial / part 以程序判定为准（LLM 的 part 仅作归类建议）：
    - 商务关键词命中、标题含"业绩"、LLM 归类为商务标/其他 → 商务标（模板填充）
    - 其余为技术标
    """
    keywords = _commercial_keywords()
    out: list[dict] = []
    for sec in raw_sections:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "").strip()
        if not title:
            continue
        title = title[:60]
        content = str(sec.get("content") or "").strip() or _PLACEHOLDER
        llm_part = str(sec.get("part") or "").strip()
        is_comm = (
            "业绩" in title
            or any(k in title for k in keywords)
            or llm_part in ("商务标", "其他")
        )
        part = "商务标" if is_comm else (llm_part if llm_part == "技术标" else "技术标")
        out.append({
            "index": len(out) + 1,
            "title": title,
            "content": content,
            "part": part,
            "is_commercial": is_comm,
        })
    return out


def _scoring_pairs(rfp_data: dict, fusion: dict | None) -> list[tuple[str, str]]:
    """(评分项名称, 分值) 列表，供覆盖校验；含子项名称。"""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for it in build_scoring_tree(rfp_data, fusion):
        for name, score in (
            [(it["item"], str(it.get("score") or ""))]
            + [
                (str(ch.get("item") or ""), str(ch.get("score") or ""))
                for ch in (it.get("children") or [])
            ]
        ):
            key = re.sub(r"\s+", "", name)
            if len(key) < 2 or key in seen:
                continue
            if "分值比重" in name:
                continue
            seen.add(key)
            pairs.append((name, score))
    return pairs


def recover_checklist_via_llm(llm_client, md_text: str) -> list[str]:
    """AI①：规则抽不到目录时，从格式章摘录让模型补清单。"""
    if llm_client is None or not md_text:
        return []
    # 优先截取第六章附近，控 token
    m = re.search(r'第[一二三四五六七八九十百\d]+章\s*[^\n]{0,20}投标文件格式', md_text)
    if m:
        excerpt = md_text[m.start(): m.start() + 6000]
    else:
        toc = re.search(r'(?m)^目录\s*$', md_text)
        excerpt = md_text[toc.start(): toc.start() + 4000] if toc else md_text[:5000]
    user_prompt = (
        "【招标文件摘录】\n"
        f"{excerpt}\n\n"
        "请提取投标文件目录/组成清单条目 JSON。"
    )
    try:
        resp = llm_client.chat_simple(
            CHECKLIST_RECOVER_PROMPT, user_prompt, temperature=0.0, max_tokens=2000,
        )
        if not resp:
            return []
        data = _parse_llm_json(resp)
        items = data.get("items")
        if not isinstance(items, list):
            return []
        return [str(x).strip() for x in items if str(x).strip() and len(str(x).strip()) >= 2][:40]
    except Exception as exc:  # noqa: BLE001
        print(f"[清单兜底] LLM 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []


def resolve_checklist(
    rfp_data: dict,
    md_path: str,
    llm_client=None,
    clean=None,
) -> tuple[list[str], dict]:
    """清单解析：规则优先，空则 AI① 兜底。返回 (清单, meta)。"""
    clean = clean or _default_clean
    meta = {"source": "rule", "recovered": False, "empty": False}
    raw = [str(x) for x in (rfp_data.get("文件清单") or []) if str(x).strip()]
    checklist = _clean_checklist(raw, clean)
    if checklist:
        meta["count"] = len(checklist)
        return checklist, meta

    try:
        md_text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        md_text = ""

    # 再跑一次规则（防止 rfp 缓存旧结果）
    try:
        import parse_bid  # noqa: PLC0415
        raw2 = parse_bid.extract_document_checklist(md_text)
        checklist = _clean_checklist([str(x) for x in raw2], clean)
        if checklist:
            rfp_data["文件清单"] = checklist
            meta.update({"source": "rule_reparse", "count": len(checklist)})
            return checklist, meta
    except Exception:  # noqa: BLE001
        pass

    recovered = recover_checklist_via_llm(llm_client, md_text)
    checklist = _clean_checklist(recovered, clean)
    if checklist:
        rfp_data["文件清单"] = checklist
        meta.update({"source": "llm_recover", "recovered": True, "count": len(checklist)})
        return checklist, meta

    meta.update({"source": "missing", "empty": True, "count": 0})
    return [], meta


def validate_outline_gaps(
    sections: list[dict],
    checklist: list[str],
    scoring_pairs: list[tuple[str, str]],
    clean=None,
    norm=None,
    match_score=None,
) -> dict:
    """规则验收：清单是否逐条落章、评分项名称是否出现在某章 title/content。"""
    _, coverage = enforce_checklist(
        sections, checklist, clean=clean, norm=norm, match_score=match_score,
    )
    blob = re.sub(
        r"\s+",
        "",
        "".join(str(s.get("title") or "") + str(s.get("content") or "") for s in sections),
    )
    uncovered: list[str] = []
    for name, score in scoring_pairs:
        key = re.sub(r"\s+", "", name)
        if len(key) < 2:
            continue
        hit = key in blob or (len(key) >= 6 and key[:6] in blob)
        if not hit:
            uncovered.append(f"{name}（{score}分）" if score else name)
    checklist_missing = list(coverage.get("missing_patched") or [])
    return {
        "ok": not checklist_missing and not uncovered,
        "checklist_missing": checklist_missing,
        "scoring_uncovered": uncovered[:30],
        "coverage": coverage,
    }


def _persist_outline(
    sid: str,
    output_dir: Path,
    checklist: list[str],
    sections: list[dict],
    coverage: dict,
    model: str,
    extra: dict | None = None,
) -> dict:
    result = {
        "sid": sid,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checklist": checklist,
        "sections": sections,
        "coverage": coverage,
        "model": model,
    }
    if extra:
        result.update(extra)
    out_path = Path(output_dir) / f"{sid}_大纲.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def generate_rule_outline(
    sid: str,
    md_path: str,
    rfp_data: dict,
    output_dir: Path,
    clean=None,
    norm=None,
    match_score=None,
    fusion: dict | None = None,
    checklist_meta: dict | None = None,
) -> dict | None:
    """无模型时的规则大纲：清单保序；禁止静默套货物/服务通用模板。

    - 有清单：按清单出章，技术总括章挂评分树子节要点
    - 无清单：仅按评分树出「待核对」技术章，并标记 incomplete（不灌安装/培训等模板章）
    """
    clean = clean or _default_clean
    checklist = _clean_checklist(
        [str(x) for x in (rfp_data.get("文件清单") or []) if str(x).strip()],
        clean,
    )
    try:
        md_text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        md_text = ""
    tree = build_scoring_tree(rfp_data, fusion, md_text=md_text)
    raw: list[dict] = []

    def _tech_content_for(title: str) -> str:
        """把评分树要点挂到技术相关章。"""
        bits: list[str] = []
        for it in tree:
            name, score = it["item"], it.get("score") or ""
            # 报价类挂到报价相关章
            if "报价" in name or "价格" in name:
                if "报价" not in title and "一览" not in title and "开标" not in title:
                    continue
            elif "技术" not in title and "方案" not in title and "实施" not in title:
                # 非技术总括章：仅挂名称命中的评分项
                if _default_norm(name) not in _default_norm(title) and name[:4] not in title:
                    if title not in ("技术文件", "技术方案", "项目实施方案"):
                        continue
            line = f"{name}（{score}分）" if score else name
            kids = it.get("children") or []
            if kids:
                kid_txt = "；".join(
                    f"{c.get('item')}（{c.get('score')}分）" if c.get("score") else str(c.get("item"))
                    for c in kids if c.get("item")
                )
                line += f" → 须分节编制：{kid_txt}"
            elif it.get("criteria"):
                line += f"：{it['criteria'][:80]}"
            bits.append(line)
            if len(bits) >= 8:
                break
        if bits:
            return "按招标文件编制。" + " | ".join(bits)
        return "按招标文件评分标准与采购需求编制本章；规则通道未能细化为子节要点，建议配置模型后重跑。"

    if checklist:
        for t in checklist:
            is_tech = any(k in t for k in ("技术", "方案", "实施", "服务"))
            raw.append({
                "title": t,
                "content": _tech_content_for(t) if is_tech or t == "技术文件" else _PLACEHOLDER,
                "part": "技术标" if is_tech or t == "技术文件" else None,
            })
        # 清单未点名的高分技术项 → 追加专章（插在清单之后，保清单相对顺序）
        used = {_default_norm(t) for t in checklist}
        for it in tree:
            name = it["item"]
            if "报价" in name or "价格" in name or "分值比重" in name:
                continue
            nkey = _default_norm(name)
            if nkey in used or any(nkey in u or u in nkey for u in used if len(u) >= 4):
                continue
            try:
                sc = float(it.get("score") or 0)
            except (TypeError, ValueError):
                sc = 0.0
            if sc < 8:
                continue
            raw.append({
                "title": name,
                "content": _tech_content_for(name),
                "part": "技术标",
            })
            used.add(nkey)
        source = "rules_checklist"
    else:
        # 禁止套用 goods_outline：只给评分树骨架 + 醒目警告
        raw.append({
            "title": "【待核对】投标文件目录未识别",
            "content": (
                "规则未能从招标文件抽出「投标文件格式/目录」。"
                "请人工对照第六章目录补全章节，或配置模型后重新分析。"
                "以下章节仅按评分项临时列出，不代表正式装订顺序。"
            ),
            "part": "其他",
        })
        for it in tree:
            name = it["item"]
            if "分值比重" in name:
                continue
            raw.append({
                "title": name,
                "content": _tech_content_for(name),
                "part": "技术标" if "报价" not in name and "价格" not in name else "商务标",
            })
        source = "rules_incomplete"

    if not raw:
        return None
    rebuilt, coverage = enforce_checklist(
        raw, checklist, clean=clean, norm=norm, match_score=match_score,
    )
    sections = finalize_sections(rebuilt)
    if not sections:
        return None
    result = _persist_outline(
        sid, output_dir, checklist, sections, coverage, model="rules",
        extra={
            "source": source,
            "checklist_meta": checklist_meta or {"source": "rule", "count": len(checklist)},
            "scoring_tree": tree,
            "gaps": {
                "checklist_missing": [] if checklist else ["（目录未识别）"],
                "scoring_uncovered": [],
            },
        },
    )
    print(f"[规则大纲] {source}：{len(sections)}章（未调用模型）", file=sys.stderr)
    return result


REPAIR_SYSTEM = (
    SYSTEM_PROMPT
    + "\n你正在根据程序验收缺口修正已有大纲。必须输出完整 sections JSON（全量，不是 diff），"
      "保留已合格章节，只补缺口：把缺失清单条目按原顺序插入，把未覆盖评分项写入承接章节 content。"
)


def repair_outline(
    llm_client,
    sections: list[dict],
    gaps: dict,
    scoring_block: str,
    checklist: list[str],
) -> list[dict] | None:
    """把规则验收缺口喂回模型，返回修正后的 raw sections。"""
    if llm_client is None:
        return None
    missing = gaps.get("checklist_missing") or []
    uncovered = gaps.get("scoring_uncovered") or []
    if not missing and not uncovered:
        return None
    current = json.dumps(
        [{"title": s.get("title"), "content": s.get("content"), "part": s.get("part")}
         for s in sections],
        ensure_ascii=False,
    )
    user_prompt = (
        f"【当前大纲】\n{current[:14000]}\n\n"
        f"【清单未落章（必须补齐且保序）】\n"
        + ("\n".join(f"- {x}" for x in missing) or "（无）")
        + "\n\n【评分项未出现在任何章节（必须写入某章 content）】\n"
        + ("\n".join(f"- {x}" for x in uncovered) or "（无）")
        + f"\n\n【评分项全集】\n{scoring_block}\n\n"
        f"【投标文件组成清单】\n"
        + "\n".join(f"{i}. {x}" for i, x in enumerate(checklist, 1))
        + "\n\n请输出修正后的完整 JSON。"
    )
    resp = llm_client.chat_simple(REPAIR_SYSTEM, user_prompt, temperature=0.1, max_tokens=8000)
    if not resp:
        return None
    data = _parse_llm_json(resp)
    raw = data.get("sections")
    if not isinstance(raw, list) or not raw:
        return None
    return raw


def run_outline_agent(
    sid: str,
    md_path: str,
    rfp_data: dict,
    fusion: dict | None,
    llm_client,
    output_dir: Path,
    match_score=None,
    clean=None,
    norm=None,
    max_repair: int = 2,
) -> tuple[dict | None, list[dict]]:
    """清单解析 → 模型起草 → 规则验收 → 缺口回修。返回 (大纲产物, 步骤轨迹)。"""
    steps: list[dict] = []

    # 0) 清单：规则优先，空则 AI① 兜底（禁止静默套类型模板）
    checklist, checklist_meta = resolve_checklist(
        rfp_data, md_path, llm_client=llm_client, clean=clean,
    )
    if checklist_meta.get("empty"):
        steps.append({
            "name": "checklist",
            "status": "warn",
            "detail": "未识别到投标文件格式目录；已禁止套用货物/服务通用模板",
        })
    elif checklist_meta.get("recovered"):
        steps.append({
            "name": "checklist",
            "status": "ok",
            "detail": f"规则未命中，AI 兜底补目录 {checklist_meta.get('count', 0)} 条",
        })
    else:
        steps.append({
            "name": "checklist",
            "status": "ok",
            "detail": (
                f"格式目录 {checklist_meta.get('count', len(checklist))} 条"
                f"（来源 {checklist_meta.get('source')}）"
            ),
        })

    # 丰富评分树 children，写回 fusion 供前端/落盘
    try:
        _md_for_tree = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        _md_for_tree = ""
    tree = build_scoring_tree(rfp_data, fusion, md_text=_md_for_tree)
    if isinstance(fusion, dict):
        fusion["scoring_tree"] = tree
        by_name = {
            re.sub(r"\s+", "", str(it.get("item") or it.get("评分项") or "")): it
            for it in (fusion.get("items") or [])
            if isinstance(it, dict)
        }
        for node in tree:
            key = re.sub(r"\s+", "", node["item"])
            if key in by_name and not by_name[key].get("children"):
                by_name[key]["children"] = node.get("children") or []

    scoring_pairs = _scoring_pairs(rfp_data, fusion)
    scoring_block = "\n".join(_collect_scoring_lines(rfp_data, fusion)) or "（未提取到评分项）"
    n_children = sum(len(n.get("children") or []) for n in tree)
    steps.append({
        "name": "scoring_tree",
        "status": "ok",
        "detail": f"评分树 {len(tree)} 父项 / {n_children} 子项",
    })

    if llm_client is None:
        outline = generate_rule_outline(
            sid, md_path, rfp_data, output_dir,
            clean=clean, norm=norm, match_score=match_score,
            fusion=fusion, checklist_meta=checklist_meta,
        )
        steps.append({
            "name": "outline",
            "status": "degraded" if checklist else "ok",
            "detail": (
                "未配置模型：已按格式目录生成规则大纲"
                if checklist else
                "未配置模型且目录未识别：仅输出评分树占位，请补 Key 后重跑"
            ),
        })
        return outline, steps

    outline = generate_outline(
        sid, md_path, rfp_data, fusion, llm_client, output_dir,
        match_score=match_score, clean=clean, norm=norm,
        checklist=checklist, checklist_meta=checklist_meta,
    )
    if outline is None:
        outline = generate_rule_outline(
            sid, md_path, rfp_data, output_dir,
            clean=clean, norm=norm, match_score=match_score,
            fusion=fusion, checklist_meta=checklist_meta,
        )
        steps.append({
            "name": "outline_llm",
            "status": "error",
            "detail": "模型起草失败，已回退规则大纲（仍按格式目录，未套通用模板）",
        })
        return outline, steps

    steps.append({
        "name": "outline_llm",
        "status": "ok",
        "detail": f"模型起草 {len(outline.get('sections') or [])} 章",
    })

    persist_extra_base = {
        "checklist_meta": checklist_meta,
        "scoring_tree": tree,
    }

    for rnd in range(max_repair):
        gaps = validate_outline_gaps(
            outline["sections"], checklist, scoring_pairs,
            clean=clean, norm=norm, match_score=match_score,
        )
        # 无清单时不因 checklist_missing 死循环回修
        if not checklist:
            gaps["checklist_missing"] = []
            gaps["ok"] = not gaps.get("scoring_uncovered")
        if gaps["ok"]:
            steps.append({
                "name": "outline_validate",
                "status": "ok",
                "detail": "清单与评分项均已覆盖",
            })
            outline["gaps"] = gaps
            _persist_outline(
                sid, output_dir, checklist, outline["sections"],
                outline.get("coverage") or {},
                str(outline.get("model") or ""),
                extra={
                    **persist_extra_base,
                    "source": "llm+rules",
                    "repair_rounds": rnd,
                    "gaps": {"checklist_missing": [], "scoring_uncovered": []},
                },
            )
            return outline, steps
        steps.append({
            "name": "outline_validate",
            "status": "retry",
            "detail": (
                f"缺口：清单缺 {len(gaps['checklist_missing'])}，"
                f"评分项未覆盖 {len(gaps['scoring_uncovered'])}，第 {rnd + 1} 轮回修"
            ),
        })
        try:
            raw = repair_outline(
                llm_client, outline["sections"], gaps, scoring_block, checklist,
            )
        except Exception as exc:  # noqa: BLE001
            steps.append({
                "name": "outline_repair",
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            break
        if not raw:
            steps.append({
                "name": "outline_repair",
                "status": "error",
                "detail": "回修返回为空",
            })
            break
        rebuilt, coverage = enforce_checklist(
            raw, checklist, clean=clean, norm=norm, match_score=match_score,
        )
        sections = finalize_sections(rebuilt)
        if not sections:
            break
        outline = _persist_outline(
            sid, output_dir, checklist, sections, coverage,
            str(outline.get("model") or ""),
            extra={
                **persist_extra_base,
                "source": "llm+rules",
                "repair_rounds": rnd + 1,
                "gaps": {
                    "checklist_missing": gaps["checklist_missing"],
                    "scoring_uncovered": gaps["scoring_uncovered"],
                },
            },
        )
        steps.append({
            "name": "outline_repair",
            "status": "ok",
            "detail": f"第 {rnd + 1} 轮回修完成，现 {len(sections)} 章",
        })

    final_gaps = validate_outline_gaps(
        (outline or {}).get("sections") or [], checklist, scoring_pairs,
        clean=clean, norm=norm, match_score=match_score,
    )
    if not checklist:
        final_gaps["checklist_missing"] = ["（目录未识别）"]
        final_gaps["ok"] = False
    steps.append({
        "name": "outline_validate",
        "status": "ok" if final_gaps["ok"] else "warn",
        "detail": (
            "验收通过" if final_gaps["ok"]
            else (
                f"仍有缺口：清单 {len(final_gaps['checklist_missing'])}，"
                f"评分项 {len(final_gaps['scoring_uncovered'])}（已写入大纲供人工补）"
            )
        ),
    })
    if outline:
        outline["gaps"] = final_gaps
        outline["checklist_meta"] = checklist_meta
        outline["scoring_tree"] = tree
        _persist_outline(
            sid, output_dir, checklist, outline["sections"],
            outline.get("coverage") or {},
            str(outline.get("model") or ""),
            extra={
                **persist_extra_base,
                "source": outline.get("source") or "llm+rules",
                "gaps": {
                    "checklist_missing": final_gaps["checklist_missing"],
                    "scoring_uncovered": final_gaps["scoring_uncovered"],
                },
            },
        )
    return outline, steps


def generate_outline(
    sid: str,
    md_path: str,
    rfp_data: dict,
    fusion: dict | None,
    llm_client,
    output_dir: Path,
    match_score=None,
    clean=None,
    norm=None,
    checklist: list[str] | None = None,
    checklist_meta: dict | None = None,
) -> dict | None:
    """生成并落盘 {sid}_大纲.json；LLM 不可用/失败返回 None（不抛出、不落盘）。"""
    if llm_client is None:
        return None
    try:
        if checklist is None:
            checklist, checklist_meta = resolve_checklist(
                rfp_data, md_path, llm_client=llm_client, clean=clean,
            )
        checklist_meta = checklist_meta or {"source": "rule", "count": len(checklist or [])}
        md_text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
        tree = build_scoring_tree(rfp_data, fusion, md_text=md_text)
        # 临时挂到 fusion，让 _collect_scoring_lines 读到补全后的 children
        if isinstance(fusion, dict):
            fusion["scoring_tree"] = tree
        scoring_lines = _collect_scoring_lines(rfp_data, fusion)
        brief = _build_project_brief(rfp_data)

        demand = _locate_demand_section(md_text)
        scoring_text = _locate_scoring_section(md_text)
        # 类型模板仅作「禁止清单」提示，不再作为参考骨架喂给模型
        tpl_titles = _template_titles(md_text)
        forbid = [t for t in tpl_titles if any(
            k in t for k in ("安装", "培训", "调试", "选型", "节能环保")
        )]

        if checklist:
            checklist_block = "\n".join(f"{i}. {x}" for i, x in enumerate(checklist, 1))
        else:
            checklist_block = (
                "（未提取到清单：请仅按评分树组织技术章，首章标明目录未识别，"
                "禁止使用货物/服务通用模板章名）"
            )
        scoring_block = "\n".join(scoring_lines) if scoring_lines else "（未提取到评分项）"
        forbid_block = "\n".join(f"- {t}" for t in forbid) if forbid else "（无）"

        user_prompt = (
            f"【投标文件组成清单（硬约束，逐条保序）】\n{checklist_block}\n\n"
            f"【评分树（父项+子项，须全部写入大纲 content）】\n{scoring_block}\n\n"
            f"【评标办法/评分标准章节原文】\n{scoring_text or '（未定位到评标章节）'}\n\n"
            f"【项目简报】\n{brief}\n\n"
            f"【采购需求摘录】\n{demand or '（未定位到需求章节）'}\n\n"
            f"【禁止套用的无关模板章（除非清单原文出现）】\n{forbid_block}\n\n"
            f"请输出投标文件章节大纲 JSON。"
        )
        resp = llm_client.chat_simple(SYSTEM_PROMPT, user_prompt,
                                      temperature=0.2, max_tokens=8000)
        if not resp:
            raise RuntimeError("LLM返回为空")
        data = _parse_llm_json(resp)
        raw_sections = data.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise RuntimeError("JSON结构不含sections数组")

        rebuilt, coverage = enforce_checklist(
            raw_sections, checklist, clean=clean, norm=norm, match_score=match_score,
        )
        sections = finalize_sections(rebuilt)
        if not sections:
            raise RuntimeError("大纲章节为空")

        try:
            model = str(llm_client.config.get("model", ""))
        except Exception:  # noqa: BLE001
            model = ""
        result = {
            "sid": sid,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "checklist": checklist,
            "sections": sections,
            "coverage": coverage,
            "model": model,
            "source": "llm",
            "checklist_meta": checklist_meta,
            "scoring_tree": tree,
        }
        out_path = Path(output_dir) / f"{sid}_大纲.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        patched = coverage.get("missing_patched") or []
        print(f"[LLM大纲] 就绪：{len(sections)}章，清单覆盖"
              f"{coverage.get('matched', 0)}/{coverage.get('total', 0)}，"
              f"补占位{len(patched)}项，IR={out_path.name}")
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"[LLM大纲] 生成失败（初稿将回退规则路径）: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None
