#!/usr/bin/env python3
"""评分项双通道融合（scoring fusion）

设计原则（金额/分值红线）：
- 通道A（程序）：规则提取的候选评分项（parse_bid.scoring_items）
- 通道B（LLM）：评标章节原文送 DeepSeek 做语义提取，每条强制携带 quote 原文引用
- 裁决器（纯程序）：
    1. evidence 回验：quote/项目名必须在章节原文中真实存在（防幻觉）
    2. schema 硬校验：分值 0 < s <= 100（或声明总分）
    3. 总分恒等式：总分 = sum(各条分值)，由程序计算，与招标声明总分比对
       —— 恒等式不通过时绝不静默展示，标记 needs_review 交人工确认
- LLM 失败/超时/schema 不合法 → 回退通道A结果，主流程永不阻断

主入口：extract_scoring_fusion(md_path, rule_items, llm_client)
"""

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 章节定位 ────────────────────────────────────────────────

# 评标/评分章节标题：第X章 评标方法与评标标准 / 一、评分办法 / 评分细则 等
# 允许 markdown 转换产物的前导 # 标记（web层 {sid}_src.md）
_SECTION_HEAD_RE = re.compile(
    r'^\s*#{0,6}\s*(?:第[一二三四五六七八九十百\d]+[章节]|[\d一二三四五六七八九十]{1,3}[、.．])?\s*'
    r'(评标方法|评标办法|评标标准|评分办法|评分标准|评审办法|评审标准|评分细则|评审细则)'
    r'[与和及]?(评标标准|评分标准|评分细则)?\s*[（(]?[^。\n]{0,20}[)）]?\s*:?\s*$',
    re.M,
)
# 任意章节标题（用于确定评标章节的结束边界）
_CHAPTER_HEAD_RE = re.compile(
    r'^\s*#{0,6}\s*第[一二三四五六七八九十百\d]+[章节]\s*[、.．:：]?\s*\S+', re.M,
)
# 招标声明总分："总分为100分" / "满分100分" / "分值合计（技术标）：55 分"
_DECLARED_TOTAL_RES = [
    re.compile(r'(?:评审)?总\s*分\s*[为是为约]?\s*(\d{1,3}(?:\.\d+)?)\s*分'),
    re.compile(r'满\s*分\s*[为是]?\s*(\d{1,3}(?:\.\d+)?)\s*分'),
    re.compile(r'分值合计[^\d\n]{0,20}(\d{1,3}(?:\.\d+)?)\s*分'),
]


def _norm(s: str) -> str:
    """归一化：去空白，统一全角括号，便于子串比对。"""
    return re.sub(r'\s+', '', str(s or ''))


def _is_toc_line(line: str) -> bool:
    """目录行特征：含制表符/省略号线/行尾页码。"""
    if '\t' in line or '…' in line:
        return True
    return bool(re.search(r'\.{2,}\s*\d+\s*$', line) or re.search(r'\s\d{1,3}\s*$', line))


def locate_scoring_section(text: str):
    """定位「评标方法与评分标准」章节，返回 (章节原文, 声明总分或None)。"""
    section = ''
    candidates = list(_SECTION_HEAD_RE.finditer(text))

    def _line_of(m):
        ls = text.rfind('\n', 0, m.end()) + 1
        le = text.find('\n', m.end())
        return text[ls:le if le != -1 else len(text)].strip()

    # 过滤目录行（"第五章 评标方法与评标标准\t64"这类TOC条目），优先真实标题
    real = [m for m in candidates if not _is_toc_line(_line_of(m))]
    for m in (real or candidates):
        start = m.start()
        # 实际标题行的起止（m.start()可能因^\s*回吞前一换行落在上一行末）
        real_line_start = text.rfind('\n', 0, m.end()) + 1
        line_end = text.find('\n', m.end())
        if line_end == -1:
            line_end = len(text)
        # 章节结束 = 评标标题行之后的下一个"第X章"标题
        nxt = _CHAPTER_HEAD_RE.search(text, line_end + 1)
        end = nxt.start() if nxt else min(len(text), real_line_start + 40000)
        sec = text[real_line_start:end]
        # 候选章节需含实质内容（>500字或含分值表特征），否则跳过（目录区命中）
        if len(sec) > 500 or '分值' in sec or '评分' in sec:
            section = sec
            break
    if not section:
        # 兜底：找含评分关键词的短标题行（无"第X章"前缀的评分表标题）
        for line_m in re.finditer(r'^\s*#{0,6}\s*[^\n]{0,30}(评分|评审|打分)[^\n]{0,20}$', text, re.M):
            line = line_m.group(0).strip()
            if len(line) < 30 and ('办法' in line or '标准' in line or '细则' in line) and not _is_toc_line(line):
                start = line_m.start()
                nxt = _CHAPTER_HEAD_RE.search(text, line_m.end())
                end = nxt.start() if nxt else min(len(text), start + 30000)
                section = text[start:end]
                break

    declared = None
    search_pool = section if section else text
    for pat in _DECLARED_TOTAL_RES:
        dm = pat.search(search_pool)
        if dm:
            try:
                v = float(dm.group(1))
                if 0 < v <= 1000:
                    declared = v
                    break
            except ValueError:
                pass
    return section, declared


# ── 通道B：LLM 语义提取 ─────────────────────────────────────

_LLM_SYSTEM_PROMPT = (
    "你是招标文件评分标准提取引擎，只输出严格 JSON，不输出任何解释或 markdown 代码块标记。\n"
    "任务：从给定的「评标方法与评分标准」章节原文中提取全部评分项。\n"
    "输出格式：{\"items\":[{\"category\":\"类别(技术/商务/价格/资信等，原文有类别列则用原值)\","
    "\"item\":\"评分项名称\",\"score\":10,\"criteria\":\"评分标准原文(保留完整计分条件、公式、得X分/最多得Y分逻辑)\","
    "\"logic\":\"公式|阶梯|条件|直接\",\"quote\":\"原文中包含该分值数字的连续片段，20-100字\"}]}\n"
    "硬性规则：\n"
    "1. 逐项提取，不遗漏任何带分值的条目；表格中每行一个条目。\n"
    "2. 合并单元格的父项含多个子项时，拆成子项，分值各归各，绝不父子双算。\n"
    "3. 分值为该项满分值；区间分（如3-5分）取上限并写进criteria。\n"
    "4. quote 必须逐字摘自原文（含分值数字），用于程序回验；编造 quote 会导致该条被丢弃。\n"
    "5. 跳过'合计/总计/小计'行和表头行。\n"
    "6. 若给出声明总分，全部条目分值之和应等于声明总分；发现不一致时如实提取，不要凑数。"
)


def llm_extract_scoring(section_text: str, llm_client, declared_total=None):
    """调用 LLM 提取评分项，返回原始条目列表（未回验）。"""
    if not section_text or llm_client is None:
        return []
    # 限制输入长度（评标章节一般 <8k 字；超长截断保首尾）
    user_text = section_text
    if len(user_text) > 30000:
        user_text = user_text[:20000] + '\n……（中间省略）……\n' + user_text[-8000:]
    hint = f'\n\n【招标文件声明总分：{declared_total:g} 分】' if declared_total else ''
    resp = llm_client.chat_simple(
        _LLM_SYSTEM_PROMPT, user_text + hint,
        temperature=0.2, max_tokens=8000,
    )
    if not resp:
        raise RuntimeError('LLM返回为空')

    # 解析 JSON（容忍 ```json 围栏 / 前后杂文本）
    resp = resp.strip()
    resp = re.sub(r'^```(?:json)?\s*|\s*```$', '', resp)
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        s, e = resp.find('{'), resp.rfind('}')
        if s == -1 or e <= s:
            raise RuntimeError('LLM返回中未找到JSON')
        data = json.loads(resp[s:e + 1])
    items = data.get('items') if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError('JSON结构不含items数组')
    if not items:
        raise RuntimeError('LLM未提取到评分项(章节可能为索引表碎片格式)')
    return [it for it in items if isinstance(it, dict)]


def verify_items(llm_items: list, source_norm: str):
    """evidence 回验 + 分值合法性校验。返回 (通过条目, 拒绝条目)。"""
    ok, rejected = [], []
    max_score = 100.0
    for it in llm_items:
        try:
            score = float(it.get('score', 0))
        except (TypeError, ValueError):
            rejected.append({**it, 'reject_reason': '分值非法'})
            continue
        if score <= 0 or score > max_score:
            rejected.append({**it, 'reject_reason': f'分值越界({score})'})
            continue
        item_name = _norm(it.get('item', ''))
        quote = _norm(it.get('quote', ''))
        # 三级回验：quote 全文 → quote 前12字 → 项目名
        verified = (
            (quote and quote in source_norm)
            or (quote and len(quote) >= 12 and quote[:12] in source_norm)
            or (item_name and len(item_name) >= 4 and item_name in source_norm)
        )
        if not verified:
            rejected.append({**it, 'reject_reason': 'evidence回验失败(原文中未找到)'})
            continue
        ok.append({
            'category': str(it.get('category', '') or ''),
            'item': str(it.get('item', '') or '').strip(),
            'score': score,
            'criteria': str(it.get('criteria', '') or '')[:500],
            'logic': str(it.get('logic', '') or ''),
            'quote': str(it.get('quote', '') or '')[:200],
            'verified': True,
        })
    return ok, rejected


# ── 裁决器：A/B 融合 ────────────────────────────────────────

def _name_key(name: str) -> str:
    """项目名归一化：去空白/全半角括号/末尾标点，供匹配。"""
    k = _norm(name)
    k = re.sub(r'[（）()【】\[\]：:，,。．·\-—_]+', '', k)
    return k


def _match(a: str, b: str) -> bool:
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # 包含匹配：允许短类别名（价格/业绩）匹配长项目名（价格分/类似案例业绩）
    if len(ka) >= 2 and len(kb) >= 2 and (ka in kb or kb in ka):
        return True
    return False


def _match_llm_rule(lit: dict, rule_item: dict) -> bool:
    """LLM条目与规则条目匹配：项目名或类别列 任一命中即视为同一条目。

    LLM 常把类别列放 category、具体评分内容放 item；
    规则表格通道的 item 往往就是类别列，因此双向都要比对。
    """
    rname = rule_item.get('item', '')
    return _match(rname, lit.get('item', '')) or _match(rname, lit.get('category', ''))


def fuse(rule_items: list, llm_items: list, declared_total=None):
    """融合两通道结果。返回 (merged, conflicts, total, check, subsumed)。

    裁决规则：
    1. LLM条目与规则条目按名称/类别匹配：同分值→fused；不同分值→先收集，
       若多个LLM子项分值之和==规则父项分值 → 父子拆分一致（子项附为children，
       不算冲突）；否则记录conflict。
    2. 声明总分已知且 LLM 全集总和==声明总分而规则总和!=声明总分时，
       规则通道整体不可信（如把采购人信息表误当评分表），以 LLM 完整集合为准，
       未匹配上的规则项降级到 subsumed。
    """
    rule_clean = []
    for rit in (rule_items or []):
        try:
            score = float(rit.get('score', 0))
        except (TypeError, ValueError):
            continue
        if score <= 0 or score > 100:
            continue
        rule_clean.append({
            'category': str(rit.get('category', '') or ''),
            'item': str(rit.get('item', '') or '').strip(),
            'score': score,
            'criteria': str(rit.get('criteria', '') or '')[:500],
            'channel': 'rule',
        })
    rule_sum = round(sum(m['score'] for m in rule_clean), 2)
    llm_sum = round(sum(float(l['score']) for l in (llm_items or [])), 2)

    # 裁决2：LLM全集自洽且规则不自洽 → LLM完整集合为准
    if (
        declared_total and (llm_items or [])
        and abs(llm_sum - declared_total) <= 0.01
        and abs(rule_sum - declared_total) > 0.01
    ):
        merged = [{
            'category': l.get('category', ''),
            'item': l['item'],
            'score': l['score'],
            'criteria': l.get('criteria', ''),
            'logic': l.get('logic', ''),
            'quote': l.get('quote', ''),
            'channel': 'llm',
        } for l in llm_items]
        subsumed = [{'item': m['item'], 'score': m['score'],
                     'reason': '规则项与声明总分不符，已由LLM完整集合替代'}
                    for m in rule_clean]
        return merged, [], llm_sum, 'pass', subsumed

    merged = [dict(m) for m in rule_clean]
    conflicts = []
    subsumed = []
    for lit in (llm_items or []):
        hit = None
        for m in merged:
            if _match_llm_rule(lit, m):
                hit = m
                break
        if hit is None:
            merged.append({
                'category': lit.get('category', ''),
                'item': lit['item'],
                'score': lit['score'],
                'criteria': lit.get('criteria', ''),
                'logic': lit.get('logic', ''),
                'quote': lit.get('quote', ''),
                'channel': 'llm',
            })
            continue
        if abs(float(hit['score']) - float(lit['score'])) <= 0.01:
            hit['channel'] = 'fused'
            if (not hit.get('criteria')) and lit.get('criteria'):
                hit['criteria'] = lit['criteria'][:500]
            if lit.get('logic'):
                hit['logic'] = lit['logic']
        else:
            # 分值不同：先挂到待定冲突，最后统一判断是否父子拆分一致
            hit.setdefault('_llm_alts', []).append(lit)

    # 父子拆分判定：LLM子项分值之和==规则父项分值 → 一致，附children
    for m in merged:
        alts = m.pop('_llm_alts', None)
        if not alts:
            continue
        alt_sum = round(sum(float(a['score']) for a in alts), 2)
        if abs(alt_sum - float(m['score'])) <= 0.01:
            m['children'] = [
                {'item': a['item'], 'score': a['score'],
                 'criteria': a.get('criteria', '')[:300]}
                for a in alts
            ]
            m['channel'] = 'fused'
        else:
            for a in alts:
                conflicts.append({
                    'item': m['item'],
                    'rule_score': m['score'],
                    'llm_score': a['score'],
                    'llm_item': a['item'],
                    'llm_quote': a.get('quote', ''),
                })

    # 去重（同名同分值）
    seen, unique = set(), []
    for m in merged:
        k = (_name_key(m['item']), float(m['score']))
        if k in seen:
            continue
        seen.add(k)
        unique.append(m)

    total = round(sum(float(m['score']) for m in unique), 2)
    if not declared_total:
        check = 'unknown'
    elif abs(total - declared_total) <= 0.01:
        check = 'pass'
    else:
        check = 'fail'
    return unique, conflicts, total, check, subsumed


# ── 主入口 ──────────────────────────────────────────────────

def _read_plain_text(path) -> str:
    """按文件类型读取纯文本（md/txt直读，docx/pdf复用parse_bid的内联表格提取）。"""
    p = Path(path)
    suf = p.suffix.lower()
    if suf in ('.docx', '.pdf'):
        try:
            import parse_bid
            return parse_bid.read_input_file(str(p))
        except SystemExit:
            return ''
        except Exception as e:
            print(f'评分融合：{suf}文本读取失败 {e}', file=sys.stderr)
            return ''
    return p.read_text(encoding='utf-8', errors='ignore')


def extract_scoring_fusion(md_path, rule_items=None, llm_client=None):
    """评分项双通道融合主入口。

    参数：
        md_path: 招标文件路径（md/txt/docx/pdf均可，web层传 {sid}_src.md）
        rule_items: 通道A结果（parse_bid_document 的 scoring_items）
        llm_client: LLMClient 实例（None 则仅规则通道）
    返回 dict：
        items / total_score / declared_total / total_check / channel
        conflicts / rejected / llm_error / needs_review
    """
    text = _read_plain_text(md_path)

    section, declared = locate_scoring_section(text) if text else ('', None)

    llm_items, rejected, llm_error = [], [], None
    if llm_client is not None and section:
        try:
            raw = llm_extract_scoring(section, llm_client, declared)
            src_norm = _norm(section)
            llm_items, rejected = verify_items(raw, src_norm)
        except Exception as e:
            llm_error = str(e)[:200]
            print(f'评分融合：LLM通道失败，回退规则通道 - {e}', file=sys.stderr)

    merged, conflicts, total, check, subsumed = fuse(rule_items, llm_items, declared)

    # 数据来源章节（竞品对齐字段："第五章《评标方法与评标标准》"式引用）
    source_section = ''
    if section:
        first_line = section.strip().splitlines()[0] if section.strip() else ''
        source_section = first_line.lstrip('#').strip()

    channel = 'rule'
    if llm_items and not (rule_items or []):
        channel = 'llm'
    elif llm_items:
        channel = 'fused'
    if subsumed:
        channel = 'llm'  # LLM完整集合替代规则通道

    return {
        'items': merged,
        'total_score': total,
        'declared_total': declared,
        'total_check': check,
        'channel': channel,
        'source_section': source_section,
        'conflicts': conflicts,
        'rejected': rejected,
        'subsumed': subsumed,
        'llm_error': llm_error,
        # 无声明总分但总分异常(>105，常为汇总行双算)也必须人工复核
        'needs_review': check == 'fail' or bool(conflicts)
        or (declared is None and total > 105),
        'llm_used': bool(llm_items),
    }
