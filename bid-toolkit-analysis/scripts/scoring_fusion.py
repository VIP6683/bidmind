#!/usr/bin/env python3
"""评分项提取 v3：LLM全文档扫描 + 表格整喂 + 程序裁决（scoring fusion）

设计原则（金额/分值红线）：
- 不再"先定位评分章节"：LLM按结构分片扫描全文档，凡含评分内容的片段都提取，
  合并去重。（历史教训：章节定位命中错误章节 → LLM拿碎片空手而归）
- docx表格整表喂LLM：线性化为带[行,列]标注+表头+合并单元格说明的文本，
  由LLM自行识别表头行/类目头行/评分项行，不再写任何"猜格式"的硬编码规则
- 裁决器（纯程序）按证据等级从高到低，不取平均、谁都没有铁证就亮红灯：
    第1级 声明总分恒等式（铁证，双向）：规则通道总和==声明总分而LLM未命中
          → 规则胜（未匹配LLM条目留痕）；否则LLM胜
    第2级 LLM为主：全文档扫描+整表喂给+每条quote回验通过 → LLM集合为准，
          规则通道未匹配项只留痕（subsumed）不并入总分，绝不静默丢弃
    第3级 LLM失败/返回空 → 回退规则通道（含类目双算结构自检），主流程永不阻断
    第4级 谁都没有铁证（声明不符/无声明总分异常）→ needs_review=true，
          界面红字人工核对原文，绝不悄悄挑一个数
- 提取类LLM任务 temperature=0；总分恒由程序计算，LLM无权干预
- 合并去重只按项目名归一化精确匹配，绝不按quote相似度跨名去重
  （评分标准为模板句，quote包含关系会误删同名不同行的条目——事故实证）
- LLM集合内父子双算去重：类目头分值==名称被其包含的子项分值之和 → 丢弃类目头
- 规则未匹配条目 → LLM原文核验（程序定位上下文窗口，LLM判定是否真实评分项
  并强制quote回验）：真条目补回防LLM漏提，噪声条目淘汰防规则误报
- 核验JSON键名必须与提示词输出一致（扫描'items'/核验'results'），键名错配
  会让核验通道整体空转（柯坪事故：报价30永远补不回）
- 表格条目的quote回验池必须含"行级单元格拼接串"：线性化文本的[行,列]标注
  与合并标记会打断quote连续命中（柯坪"报价 30分"事故实证）
- 名称包含匹配加长度护栏 |长度差|<=max(6,3*短名长)：评分标准段落被规则通道
  误当条目名时（千字长文含"售后服务方案"）不得吸走LLM条目制造假conflict；
  也不可过紧挡掉合法父项名⊂子项名匹配（柯坪装卸流程子项漏配事故）
- 核验补回遵守并入守恒：任何补回使总分超过声明总分 → 判定为同一分值的重复
  表述（如"分值比重30" vs "报价30"），留痕拒绝，绝不静默并入破坏恒等式

主入口：extract_scoring_fusion(md_path, rule_items, llm_client, docx_path=None)
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 扫描参数 ────────────────────────────────────────────────

_CHUNK_TARGET = 12000     # 文本片目标大小（字符），小片按结构边界合并到此规模
_CHUNK_HARD_MAX = 20000   # 单片硬上限（无标题结构的超长段落按段落硬切）
_MAX_LLM_CALLS = 40       # 全文档扫描LLM调用预算（表格+分片共享）
_TABLE_ROW_CAP = 120      # 单表线性化最大行数

# 招标声明总分："总分为100分" / "满分100分" / "分值合计（技术标）：55 分"
_DECLARED_TOTAL_RES = [
    re.compile(r'(?:评审)?总\s*分\s*[为是为约]?\s*(\d{1,3}(?:\.\d+)?)\s*分'),
    re.compile(r'满\s*分\s*[为是]?\s*(\d{1,3}(?:\.\d+)?)\s*分'),
    re.compile(r'分值合计[^\d\n]{0,20}(\d{1,3}(?:\.\d+)?)\s*分'),
]

# 评分内容召回信号（仅用于"哪些片值得送LLM"的召回过滤，不做任何格式判定）
_SCORING_SIGNAL_RE = re.compile(r'\d{1,3}(?:\.\d+)?\s*分|(评分|评审|打分|得分|分值)')


def _norm(s: str) -> str:
    """归一化：去空白，便于子串比对。"""
    return re.sub(r'\s+', '', str(s or ''))


def _name_key(name: str) -> str:
    """项目名归一化：去空白/全半角括号/标点，供匹配。"""
    k = _norm(name)
    k = re.sub(r'[（）()【】\[\]：:，,。．·\-—_]+', '', k)
    return k


def _find_declared_total(text: str):
    """声明总分：全文按 总分→满分→分值合计→分值构成求和 优先级取首个合法值。

    "分值构成：商务部分15分 技术部分55分 报价得分30分" 这类写法以行内各分值
    之和为声明总分（≥2个数才认定）。注：全文搜索可能命中类别小计；即便声明
    取偏，裁决器恒等式只会走"无铁证亮红灯"，绝不会静默给出错误数字。
    """
    if not text:
        return None
    for pat in _DECLARED_TOTAL_RES:
        m = pat.search(text)
        if m:
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if 0 < v <= 1000:
                return v
    # 分值构成行求和（通用写法兜底）。md表格合并单元格会把同一格内容重复输出
    # （|分值构成|分值构成|商务15分..|商务15分..|），故先做周期性重复折叠：
    # 仅当数列为unit≥2的整周期重复时才折叠，避免误伤"四个类别各10分"这类真实列表
    def _fold_periodic(nums):
        n = len(nums)
        for unit in range(2, n // 2 + 1):
            if n % unit == 0 and all(nums[i] == nums[i % unit] for i in range(n)):
                return nums[:unit]
        return nums

    for m in re.finditer(r'分值(?:[构组]成|比重|权值)[^\n]{0,160}', text):
        raw = [float(x) for x in re.findall(r'(\d{1,3}(?:\.\d+)?)\s*分', m.group(0))]
        nums = _fold_periodic(raw)
        if 2 <= len(nums) <= 6:
            # 行前文（通常是表头行）声明"总分"列、且末位数字==其余之和时，
            # 末位是总分而非构成项（柯坪"评分因素|报价部分|商务技术部分|总分"
            # 表头下的"分值比重|30分|70分|100分"——若直接求和会误得200）
            pre = text[max(0, m.start() - 300):m.start()]
            rest_sum = round(sum(nums[:-1]), 2)
            if '总分' in pre and abs(nums[-1] - rest_sum) <= 0.01:
                total = nums[-1]
            else:
                total = round(sum(nums), 2)
            if 0 < total <= 1000:
                return total
    return None


# ── 结构分片 ────────────────────────────────────────────────

# markdown标题 或 中文章节标题（纯txt无#时也能按章节切）
_HEAD_BOUND_RE = re.compile(r'^(?:#{1,6}\s+|第[一二三四五六七八九十百\d]+[章节]\s*)', re.M)


def _split_chunks(text: str):
    """按结构边界（标题行）分片：小片合并到目标大小，绝不丢内容。"""
    if not text or not text.strip():
        return []
    bounds = [m.start() for m in _HEAD_BOUND_RE.finditer(text)]
    if not bounds or bounds[0] != 0:
        bounds.insert(0, 0)
    segments = []
    for i, b in enumerate(bounds):
        e = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        seg = text[b:e]
        if seg.strip():
            segments.append(seg)
    # 单段超限：按换行硬切
    pieces = []
    for seg in segments:
        while len(seg) > _CHUNK_HARD_MAX:
            cut = seg.rfind('\n', _CHUNK_TARGET // 2, _CHUNK_HARD_MAX)
            cut = cut if cut != -1 else _CHUNK_HARD_MAX
            pieces.append(seg[:cut])
            seg = seg[cut:]
        if seg.strip():
            pieces.append(seg)
    # 小片合并
    chunks, buf = [], ''
    for p in pieces:
        if buf and len(buf) + len(p) > _CHUNK_TARGET:
            chunks.append(buf)
            buf = p
        else:
            buf = buf + p if buf else p
    if buf.strip():
        chunks.append(buf)
    return chunks


def _has_scoring_signal(s: str) -> bool:
    return bool(_SCORING_SIGNAL_RE.search(s or ''))


# ── docx表格整表线性化 ──────────────────────────────────────

def _docx_tables_structured(docx_path):
    """python-docx提取全部表格，线性化为带[行,列]标注+合并单元格说明的文本。

    合并单元格标注：与左格同一逻辑单元格标(→同左)，与上格标(↑同上)，
    内容以首个出现的格为准。交给LLM自行判断表头/类目头/评分项行，
    此处不做任何格式猜测。
    """
    texts = []
    try:
        from docx import Document
    except ImportError:
        print('警告：未安装 python-docx，表格结构化通道不可用', file=sys.stderr)
        return texts
    try:
        doc = Document(str(docx_path))
    except Exception as e:
        print(f'表格结构化读取失败：{e}', file=sys.stderr)
        return texts

    def _vmerge_continue(cell):
        """vMerge纵向合并的延续格（python-docx未统一对象时的兜底检测）。"""
        try:
            tcPr = cell._tc.tcPr
            if tcPr is None:
                return False
            from docx.oxml.ns import qn
            vm = tcPr.find(qn('w:vMerge'))
            return vm is not None and vm.get(qn('w:val')) in (None, 'continue')
        except Exception:
            return False

    for t_idx, table in enumerate(doc.tables):
        try:
            grid = [list(row.cells) for row in table.rows]
        except Exception:
            continue
        if len(grid) < 2 or len(grid[0]) < 2:
            continue
        ncols = max(len(r) for r in grid)
        for r in grid:
            while len(r) < ncols:
                r.append(None)
        nrows = len(grid)
        lines = [
            f'【表格#{t_idx + 1}】共{nrows}行×{ncols}列。读取说明：[行,列]标注位置；'
            '单元格文字后标(→同左)或(↑同上)表示与左格/上格是同一逻辑单元格（合并延续），'
            '其内容以首个出现的格为准。'
        ]
        for r in range(nrows):
            if r >= _TABLE_ROW_CAP:
                lines.append(f'（表格超过{_TABLE_ROW_CAP}行，仅显示前{_TABLE_ROW_CAP}行）')
                break
            parts = []
            for c in range(ncols):
                cell = grid[r][c]
                t = str(cell.text or '').strip().replace('\n', ' ') if cell is not None else ''
                tag = ''
                if cell is not None:
                    if c > 0 and grid[r][c - 1] is not None and cell._tc is grid[r][c - 1]._tc:
                        tag = '→同左'
                    elif r > 0 and grid[r - 1][c] is not None and cell._tc is grid[r - 1][c]._tc:
                        tag = '↑同上'
                    elif r > 0 and grid[r - 1][c] is not None and not t and _vmerge_continue(cell):
                        tag = '↑同上'
                parts.append(f'[{r + 1},{c + 1}]{t}({tag})' if tag else f'[{r + 1},{c + 1}]{t}')
            lines.append(' | '.join(parts))
        texts.append('\n'.join(lines))
    return texts


def _table_row_norms(table_text: str):
    """从线性化表格文本重建逐行单元格拼接串（quote回验专用）。

    线性化文本带[行,列]标注与(→同左)/(↑同上)标记，LLM按单元格逐字摘出的
    quote无法在其中连续命中——柯坪"报价 30分"条目即因标注打断quote回验
    失败被误丢。逐行剥掉标注后拼接，quote即可精确回验到所在行。
    """
    norms = []
    for line in (table_text or '').splitlines():
        if not line.lstrip().startswith('['):
            continue
        cells = [re.sub(r'(→同左|↑同上)', '', re.sub(r'\[\d+,\d+\]', '', part))
                 for part in line.split(' | ')]
        norms.append(_norm(''.join(cells)))
    return norms


# ── 通道B：LLM 全文档扫描 ───────────────────────────────────

_LLM_SCAN_SYSTEM_PROMPT = (
    "你是招标文件评分标准提取引擎，只输出严格 JSON，不输出任何解释或 markdown 代码块标记。\n"
    "任务：下面是招标文档全文中的一个片段（可能含多个章节，表格以|分隔的markdown行形式内嵌）。"
    "凡含评分内容的部分都要提取；片段不含评分内容时输出 {\"items\":[]}。\n"
    "输出格式：{\"items\":[{\"category\":\"类别(技术/商务/价格/资信等，原文有类别列则用原值)\","
    "\"item\":\"评分项名称\",\"score\":10,\"criteria\":\"评分标准原文(保留完整计分条件、公式、得X分/最多得Y分逻辑)\","
    "\"logic\":\"公式|阶梯|条件|直接\",\"quote\":\"原文中包含该分值数字的连续片段，20-100字\"}]}\n"
    "硬性规则：\n"
    "1. 逐项提取，不遗漏任何带分值的评分条目；markdown表格中每行一个条目。\n"
    "2. 类目头/父项（分值等于其下明细行之和）不单独输出为评分项，类别名写入子项category，绝父子双算。\n"
    "3. 同一行内多个「得X分」的分支/档位/情形（如优于得X分、满足得Y分）属于同一个评分项，"
    "合并为一条、score取该项满分值，绝不按行内分支拆分累加。\n"
    "4. 分值为该项满分值；区间分（如3-5分）取上限并写进criteria。\n"
    "5. quote 必须逐字摘自片段原文，且必须包含该评分项名称（或其连续4字以上片段）"
    "和分值数字——各评分项的评分标准常为相似模板句，quote不含项目名将无法区分条目；"
    "编造 quote 会导致该条被丢弃。\n"
    "6. 跳过'合计/总计/小计'行和'满分/总分'声明行。\n"
    "7. 只提取本片段原文中真实存在的内容，不要根据常识补全。"
)

_LLM_TABLE_SYSTEM_PROMPT = (
    "你是招标文件评分表解析引擎，只输出严格 JSON，不输出任何解释或 markdown 代码块标记。\n"
    "任务：下面是一张从docx提取的表格，已线性化为带[行,列]标注的文本。"
    "合并单元格的延续位置标有(→同左)或(↑同上)，其内容以首个出现的格为准。\n"
    "表格结构由你自行判断：哪一行是表头，哪一行是类目头/父项行（如\"技术标60分\"纵向合并跨多个明细行的类目行），"
    "哪些行是评分项明细行，不使用任何固定格式假设。\n"
    "输出格式：{\"items\":[{\"category\":\"类别\",\"item\":\"评分项名称\",\"score\":10,"
    "\"criteria\":\"评分标准原文(保留完整计分条件、公式、得X分/最多得Y分逻辑)\","
    "\"logic\":\"公式|阶梯|条件|直接\",\"quote\":\"原文中包含该分值数字的连续片段，20-100字\"}]}\n"
    "硬性规则：\n"
    "1. 逐项提取，不遗漏任何带分值的评分明细行。\n"
    "2. 类目头/父项行（只含类别名或类别+分值合计，其下有明细行）不单独输出为评分项；"
    "其分值等于明细行之和时只输出明细行并把类别名写入category，绝父子双算。\n"
    "3. 行的分值以「分值」列为准：同一行内多个「得X分」的分支/档位/情形属于同一个评分项，"
    "合并为一条、score取分值列数值，绝不按行内分支拆分累加。\n"
    "4. 分值为该项满分值；区间分（如3-5分）取上限并写进criteria。\n"
    "5. quote 必须逐字摘自单元格文字，且必须包含该评分项名称（或其连续4字以上片段）"
    "和分值数字——各行的评分标准常为相似模板句，quote不含项目名将无法区分到具体行；"
    "不要带[行,列]标注和(→同左)/(↑同上)标记；编造 quote 会导致该条被丢弃。\n"
    "6. 跳过表头行和'合计/总计/小计'行。\n"
    "7. 若给出声明总分，全部条目分值之和应等于声明总分；发现不一致时如实提取，不要凑数。\n"
    "8. 该表若不是评分表（如采购清单、技术参数表、日程表），输出 {\"items\":[]}。"
)

_LLM_VERIFY_SYSTEM_PROMPT = (
    "你是招标文件评分项核验引擎，只输出严格 JSON，不输出任何解释或 markdown 代码块标记。\n"
    "任务：下面给出若干候选评分条目（来自程序规则通道，可能有误）及各自在招标文件中的原文上下文窗口。"
    "逐条核验：该条目是否是招标文件评分标准中的真实评分项（有明确分值、属于评审打分内容），"
    "还是被误提的噪声（如采购清单参数、保证金/有效期等商务说明、表头/合计行、普通程序性文字）。\n"
    "输出格式：{\"results\":[{\"item\":\"候选条目名\",\"score\":N,\"is_scoring\":true,"
    "\"quote\":\"逐字摘自该条目上下文、包含条目名称（或其连续4字以上片段）和分值数字的片段\"}]}\n"
    "硬性规则：\n"
    "1. 只依据给出的上下文窗口判断，不要根据常识补全。\n"
    "2. is_scoring=false 时 quote 可为空。\n"
    "3. quote 必须逐字摘自对应条目的上下文窗口原文，否则该条目会被程序丢弃。\n"
    "4. results 数量必须与候选条目数量一致，按原顺序逐条输出。"
)


def _call_llm_json(llm_client, system_prompt, user_text, declared_total=None, key='items'):
    """调用LLM并解析JSON（容忍```json围栏/前后杂文本）。

    key：期望的数组键名（扫描/表格='items'，核验='results'）。
    历史教训：核验提示词输出 {"results":[...]}，而此处硬编码只认'items'，
    导致全部核验批次报"JSON结构不含items数组"、报价条目永远补不回（柯坪事故）。
    """
    hint = f'\n\n【招标文件声明总分：{declared_total:g} 分】' if declared_total else ''
    resp = llm_client.chat_simple(
        system_prompt, user_text + hint,
        temperature=0, max_tokens=8000,
    )
    if not resp:
        raise RuntimeError('LLM返回为空')
    resp = re.sub(r'^```(?:json)?\s*|\s*```$', '', resp.strip())
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        s, e = resp.find('{'), resp.rfind('}')
        if s == -1 or e <= s:
            raise RuntimeError('LLM返回中未找到JSON')
        data = json.loads(resp[s:e + 1])
    items = data.get(key, data.get('items')) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError(f'JSON结构不含{key}数组')
    return [it for it in items if isinstance(it, dict)]


def llm_full_scan(text: str, table_texts: list, llm_client, declared_total=None):
    """通道B：全文档扫描。

    B2 表格通道：每张候选表格整表喂LLM（最结构化、最可靠，优先）；
    B1 文本通道：结构分片逐片扫描，凡含评分信号的片都送LLM。
    返回 (原始条目列表[表格在前], 表格norm列表, 错误列表, 统计dict)。
    """
    raw_items, table_norms, errors = [], [], []
    n_calls = 0
    n_tables_fed = 0
    chunks = _split_chunks(text) if text else []

    # B2：表格整喂（召回过滤=含"数字+分"信号，格式判定全交LLM）
    for t_idx, tt in enumerate(table_texts):
        if n_calls >= _MAX_LLM_CALLS:
            errors.append('达到LLM调用预算上限，后续表格未扫描')
            break
        if not _has_scoring_signal(tt):
            continue
        try:
            raw = _call_llm_json(llm_client, _LLM_TABLE_SYSTEM_PROMPT, tt, declared_total)
            n_calls += 1
        except Exception as e:
            errors.append(f'表格#{t_idx + 1}: {str(e)[:80]}')
            continue
        table_norms.append(_norm(tt))
        # 行级拼接串一并入回验池（LLM按单元格摘的quote在此精确命中）
        table_norms.extend(_table_row_norms(tt))
        n_tables_fed += 1
        for it in raw:
            it['_src'] = 'table'
        raw_items.extend(raw)
        print(f'评分扫描：表格#{t_idx + 1} 提取{len(raw)}条', file=sys.stderr)

    # B1：文本分片扫描
    n_signal = 0
    for c_idx, ch in enumerate(chunks):
        if n_calls >= _MAX_LLM_CALLS:
            errors.append('达到LLM调用预算上限，后续文本片未扫描')
            break
        if not _has_scoring_signal(ch):
            continue
        n_signal += 1
        try:
            raw = _call_llm_json(llm_client, _LLM_SCAN_SYSTEM_PROMPT, ch, declared_total)
            n_calls += 1
        except Exception as e:
            errors.append(f'分片{c_idx + 1}: {str(e)[:80]}')
            continue
        for it in raw:
            it['_src'] = 'prose'
        raw_items.extend(raw)
        if raw:
            print(f'评分扫描：分片{c_idx + 1}/{len(chunks)} 提取{len(raw)}条', file=sys.stderr)

    stats = {
        'llm_calls': n_calls,
        'chunks_total': len(chunks),
        'chunks_signal': n_signal,
        'tables_fed': n_tables_fed,
    }
    return raw_items, table_norms, errors, stats


def _merge_scan_items(raw_items: list):
    """扫描结果合并去重（防静默丢失）。

    - 同名同分值：同一条目（后到者仅补空字段）；
    - 同名不同分值：保留先到者（表格通道构造在前、优先），冲突留痕；
    - 不同名称：绝不因quote相似/包含而合并——各评分项的评分标准常为相似
      模板句，quote包含关系会误删同名不同行的条目（重力仪售后服务方案事故
      实证）。不同名条目一律保留，交由裁决器和声明总分恒等式兜底。
    """
    merged, conflicts = [], []

    def _nk(it):
        return _name_key(it.get('item', ''))

    for it in raw_items:
        try:
            score = float(it.get('score', 0))
        except (TypeError, ValueError):
            continue
        k = _nk(it)
        dup = None
        if k:
            dup = next((m for m in merged if _nk(m) == k), None)
        if dup is not None:
            if abs(float(dup['score']) - score) > 0.01:
                conflicts.append({
                    'item': it.get('item', ''),
                    'kept_item': dup.get('item', ''),
                    'kept_score': float(dup['score']),
                    'dropped_score': score,
                    'src': it.get('_src', ''),
                    'reason': '同一评分项在不同表格/片段提取到不同分值，保留表格通道优先值',
                })
            else:
                if (not dup.get('criteria')) and it.get('criteria'):
                    dup['criteria'] = str(it['criteria'])[:500]
                if (not dup.get('logic')) and it.get('logic'):
                    dup['logic'] = it['logic']
            continue
        merged.append(it)
    return merged, conflicts


def _dedupe_parent_child(items: list):
    """LLM集合内父子双算去重（纯结构+名称包含特征，与行业词汇无关，通用）。

    若某项分值 == 其名称被该项名称包含的若干其他条目分值之和，
    则该项为类目头行（其下明细行已完整表达），丢弃父项保留子项。
    子项名不含父项名时不动（避免误伤分值巧合的独立条目）。
    """
    if len(items) < 2:
        return items
    dropped = set()
    for i, p in enumerate(items):
        if i in dropped:
            continue
        try:
            p_score = float(p.get('score', 0))
        except (TypeError, ValueError):
            continue
        pi = _name_key(p.get('item', ''))
        if not pi:
            continue
        children = []
        for j, c in enumerate(items):
            if j == i or j in dropped:
                continue
            cj = _name_key(c.get('item', ''))
            if cj and cj != pi and pi in cj:
                children.append(j)
        if len(children) >= 2:
            try:
                csum = round(sum(float(items[j].get('score', 0)) for j in children), 2)
            except (TypeError, ValueError):
                continue
            if abs(csum - p_score) <= 0.01:
                dropped.add(i)
                print(f'评分扫描：父子双算去重，丢弃类目头「{p.get("item", "")[:30]}」'
                      f'{p_score:g}分（{len(children)}个子项分值之和相等）', file=sys.stderr)
    return [it for idx, it in enumerate(items) if idx not in dropped]


def _locate_item_window(text: str, name: str, win_before=400, win_after=700):
    """在全文中定位候选条目名称的上下文窗口（程序侧文本定位，供LLM核验）。"""
    key = re.sub(r'\s+', '', str(name or ''))[:12]
    if not key or not text:
        return ''
    pat = re.compile(r'\s*'.join(re.escape(ch) for ch in key))
    candidates = list(pat.finditer(text))[:8]
    if not candidates:
        return ''

    def _window(m):
        s = max(0, m.start() - win_before)
        e = min(len(text), m.end() + win_after)
        return text[s:e]

    for m in candidates:
        w = _window(m)
        if _has_scoring_signal(w):
            return w
    return _window(candidates[0])


def llm_verify_rule_items(text: str, rule_items: list, llm_client):
    """对规则通道中未被LLM集合匹配的条目做LLM原文核验（证据制，防LLM漏提+防规则噪声）。

    返回 (verified_items, dropped)。核验失败/超时不阻断主流程（返回空，条目按未通过留痕）。
    """
    verified, dropped = [], []
    if not rule_items or llm_client is None:
        return verified, dropped
    src_norm = _norm(text or '')
    for b0 in range(0, len(rule_items), 8):
        batch = rule_items[b0:b0 + 8]
        blocks = []
        for idx, r in enumerate(batch):
            w = _locate_item_window(text, r.get('item', ''))[:1500]
            blocks.append(f"【候选{idx + 1}】条目：{str(r.get('item', ''))[:100]} | 分值：{r.get('score')}\n"
                          f"上下文：\n{w if w else '（原文中未定位到该条目）'}")
        try:
            data = _call_llm_json(llm_client, _LLM_VERIFY_SYSTEM_PROMPT,
                                  '\n\n'.join(blocks), None, key='results')
            results = data.get('results') if isinstance(data, dict) else data
            if not isinstance(results, list) or len(results) != len(batch):
                raise RuntimeError('核验结果数量与候选不一致')
        except Exception as e:
            print(f'评分核验：LLM核验失败，本批{len(batch)}条按未通过留痕 - {str(e)[:80]}', file=sys.stderr)
            for r in batch:
                dropped.append({'item': r.get('item', ''), 'score': r.get('score'),
                                'reject_reason': f'LLM核验失败({str(e)[:60]})'})
            continue
        for r, res in zip(batch, results):
            if not isinstance(res, dict):
                res = {}
            if not res.get('is_scoring'):
                dropped.append({'item': r.get('item', ''), 'score': r.get('score'),
                                'reject_reason': 'LLM核验：非评分项（噪声/表头/清单等）'})
                continue
            quote = str(res.get('quote', '') or '')
            q = _norm(quote)
            item_name = _norm(res.get('item') or r.get('item', ''))
            ok = ((q and q in src_norm)
                  or (q and len(q) >= 12 and q[:12] in src_norm)
                  or (item_name and len(item_name) >= 4 and item_name in src_norm))
            if not ok:
                dropped.append({'item': r.get('item', ''), 'score': r.get('score'),
                                'reject_reason': 'LLM核验：quote回验失败(原文中未找到)'})
                continue
            try:
                score = float(res.get('score') if res.get('score') is not None else r.get('score'))
            except (TypeError, ValueError):
                score = float(r.get('score', 0))
            if score <= 0 or score > 100:
                dropped.append({'item': r.get('item', ''), 'score': score,
                                'reject_reason': f'LLM核验：分值越界({score})'})
                continue
            verified.append({
                'category': '',
                'item': str(res.get('item') or r.get('item', '')).strip(),
                'score': score,
                'criteria': str(r.get('criteria', '') or '')[:500],
                'logic': '',
                'quote': quote[:200],
                'verified': True,
            })
    return verified, dropped


def verify_items(llm_items: list, source_norm: str, extra_norms=None, declared_total=None):
    """evidence 回验 + 分值合法性校验。返回 (通过条目, 拒绝条目)。

    回验池：全文归一化文本 + 各表格线性化文本（表格quote含线性化上下文时仍可回验）。
    """
    norms = [n for n in ([source_norm] + list(extra_norms or [])) if n]

    def _present(s: str) -> bool:
        return any(s in n for n in norms)

    ok, rejected = [], []
    max_score = float(declared_total) if (declared_total and declared_total >= 100) else 100.0
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
            (quote and _present(quote))
            or (quote and len(quote) >= 12 and _present(quote[:12]))
            or (item_name and len(item_name) >= 4 and _present(item_name))
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


# ── 裁决器 ──────────────────────────────────────────────────

def _match_llm_rule(lit: dict, rule_item: dict) -> bool:
    """LLM条目与规则条目匹配：先项目名，后类别列。

    名称包含匹配加长度护栏：|长度差| <= max(6, 3*短名长度)。合法的包含
    匹配如"业绩"⊂"同类项目业绩"（差4）、"项目实施方案"⊂"项目实施方案-货物
    装卸流程及装卸管理方案"（差13）均通过；而评分标准段落被规则通道误当
    条目名时（如千字长文含"售后服务方案"六个字），长度差远超护栏 → 不匹配，
    交由LLM原文核验判真伪。
    （校园假conflict事故实证：规则垃圾长条目包含吸走LLM条目制造18≠8假冲突；
    柯坪事故实证：护栏过紧(2x)会挡掉合法父项名⊂子项名匹配，造成父子拆分
    reconciliation漏配出假conflict）
    类别列匹配仅当规则项名基本就是类别名（归一化长度差≤2，如"技术标"vs"技术"、
    "商务部分"vs"商务"），防止"技术参数要求"这类真实评分项名误吸类别词造成假冲突。
    """
    ri = _name_key(rule_item.get('item', ''))
    lk = _name_key(lit.get('item', ''))
    if ri and lk:
        if ri == lk:
            return True
        if (len(ri) >= 2 and len(lk) >= 2 and (ri in lk or lk in ri)
                and abs(len(ri) - len(lk)) <= max(6, 3 * min(len(ri), len(lk)))):
            return True
    ck = _name_key(lit.get('category', ''))
    if ri and ck:
        if ri == ck:
            return True
        if (min(len(ri), len(ck)) >= 2 and (ri in ck or ck in ri)
                and abs(len(ri) - len(ck)) <= 2):
            return True
    return False


def _detect_double_count(items: list):
    """程序结构自检：类目双算检测（纯结构特征，与行业词汇无关，通用）。

    (a) 完全重复条目：同名同分值出现≥2次（如"价格30分"出现两遍）；
    (b) 父项双算：某项分值 == 其后连续若干项分值之和（≥2项，如 价格30 == 技术20+商务10）。
    返回命中描述列表（空=自检通过）。
    """
    hits = []
    vals, names = [], []
    for it in items:
        try:
            vals.append(float(it.get('score', 0)))
        except (TypeError, ValueError):
            vals.append(0.0)
        names.append(_name_key(it.get('item', '')))
    # (a) 重复条目
    cnt = Counter((names[i], round(vals[i], 2)) for i in range(len(items)) if names[i])
    for (k, s), n in cnt.items():
        if n >= 2:
            hits.append(f'条目「{k}」{s:g}分重复出现{n}次（疑似类目双算）')
    # (b) 父项 == 后续连续子项之和
    for i in range(len(items)):
        run_sum = 0.0
        run = []
        for j in range(i + 1, len(items)):
            run_sum += vals[j]
            run.append(j)
            if run_sum > vals[i] + 0.01:
                break
            if abs(run_sum - vals[i]) <= 0.01 and len(run) >= 2:
                desc = '、'.join(names[j] or f'{vals[j]:g}分' for j in run)
                hits.append(
                    f'第{i + 1}项「{names[i]}」{vals[i]:g}分 == 其后{len(run)}项之和（{desc}）— 疑似父子双算')
                break
    return hits


_RULE_META_NAMES = (
    '分值比重', '分值构成', '分值权值', '权重构成', '权值构成',
    '评分因素', '商务技术部分', '报价部分', '总分',
)


def _is_rule_meta_item(name: str) -> bool:
    """分值构成/比重汇总行不是评分项（如「分值比重|30分|70分|100分」）。"""
    compact = re.sub(r'\s+', '', str(name or ''))
    if not compact:
        return True
    if compact in _RULE_META_NAMES:
        return True
    return any(k in compact for k in ('分值比重', '分值构成', '权重构成', '权值构成'))


def _clean_rule_items(rule_items: list):
    rule_clean = []
    for rit in (rule_items or []):
        try:
            score = float(rit.get('score', 0))
        except (TypeError, ValueError):
            continue
        if score <= 0 or score > 100:
            continue
        item = str(rit.get('item', '') or '').strip()
        if _is_rule_meta_item(item):
            continue
        rule_clean.append({
            'category': str(rit.get('category', '') or ''),
            'item': item,
            'score': score,
            'criteria': str(rit.get('criteria', '') or '')[:500],
            'channel': 'rule',
        })
    return rule_clean


def arbiter(rule_items: list, llm_items: list, declared_total=None):
    """程序裁决器：按证据等级从高到低，不取平均，谁都没有铁证就亮红灯。

    裁决顺序：
    第1级 声明总分恒等式（铁证，双向）：规则通道总和==声明总分而LLM未命中
          → 规则胜，未匹配LLM条目留痕（excluded）
    第2级 LLM为主：全文档扫描+每条quote回验通过 → LLM集合为准；
          规则条目按名称/类别匹配到LLM条目时做融合标记或父子拆分 reconciliation，
          未匹配规则项留痕（subsumed），绝不静默丢弃
    第3级 LLM失败/返回空 → 规则通道兜底（类目双算结构自检不过则亮红灯）
    第4级 声明总分存在但LLM未命中 → 亮灯人工复核

    返回 (merged, conflicts, total, check, subsumed, excluded, red_flag, rule_self_check)
    """
    rule_clean = _clean_rule_items(rule_items)
    rule_sum = round(sum(m['score'] for m in rule_clean), 2)
    llm_sum = round(sum(float(l['score']) for l in (llm_items or [])), 2)

    rule_self = bool(declared_total) and abs(rule_sum - declared_total) <= 0.01
    llm_self = (
        bool(declared_total) and bool(llm_items)
        and abs(llm_sum - declared_total) <= 0.01
    )

    subsumed, excluded, conflicts = [], [], []

    # ── 第1级：声明总分恒等式（铁证，双向） ──
    if declared_total and rule_self and not llm_self:
        merged = [dict(m) for m in rule_clean]
        for lit in (llm_items or []):
            hit = next((m for m in merged if _match_llm_rule(lit, m)), None)
            if hit is None:
                excluded.append({
                    'item': lit.get('item', ''),
                    'score': lit.get('score'),
                    'quote': lit.get('quote', ''),
                    'reason': '规则通道总分与声明总分一致，该LLM条目未匹配规则项'
                              '（疑似拆分/幻觉多算），为保护总分恒等式未合并',
                })
                continue
            # 分值以规则通道为准（恒等式保护），仅回填LLM扫描到的展示字段——
            # 规则通道只提名称+分值，评审标准/类别为空时界面明细无内容
            # （检验机构事故实证：总分100正确但15条明细的评分标准全空）
            if (not hit.get('criteria')) and lit.get('criteria'):
                hit['criteria'] = str(lit['criteria'])[:500]
            if (not hit.get('category')) and lit.get('category'):
                hit['category'] = str(lit['category'])
            if (not hit.get('quote')) and lit.get('quote'):
                hit['quote'] = str(lit['quote'])[:200]
            if (not hit.get('logic')) and lit.get('logic'):
                hit['logic'] = lit['logic']
            if hit.get('channel') == 'rule':
                hit['channel'] = 'fused'
        return merged, [], rule_sum, 'pass', [], excluded, False, []

    # ── 第2级：LLM为主（全文档扫描+逐条quote回验通过） ──
    if llm_items:
        merged = [{
            'category': l.get('category', ''),
            'item': l['item'],
            'score': l['score'],
            'criteria': l.get('criteria', ''),
            'logic': l.get('logic', ''),
            'quote': l.get('quote', ''),
            'channel': 'llm',
        } for l in llm_items]
        # 规则条目 → LLM条目匹配（每个LLM条目至多归并一条规则项）
        rule_hits = {}
        for m in merged:
            for i, r in enumerate(rule_clean):
                if _match_llm_rule({'item': m['item'], 'category': m.get('category', '')}, r):
                    rule_hits.setdefault(i, []).append(m)
                    break
        # 父子拆分 reconciliation：多个规则/LLM条目互为父子时分值之和一致则不冲突
        for i, hits in rule_hits.items():
            r = rule_clean[i]
            hit_sum = round(sum(float(h['score']) for h in hits), 2)
            if abs(hit_sum - r['score']) <= 0.01:
                for h in hits:
                    h['channel'] = 'fused'
                    if (not h.get('criteria')) and r.get('criteria'):
                        h['criteria'] = r['criteria'][:500]
                    if r.get('logic') and not h.get('logic'):
                        h['logic'] = r['logic']
            else:
                for h in hits:
                    conflicts.append({
                        'item': r['item'],
                        'rule_score': r['score'],
                        'llm_score': h['score'],
                        'llm_item': h['item'],
                        'llm_quote': h.get('quote', ''),
                    })
        for i, r in enumerate(rule_clean):
            if i not in rule_hits:
                subsumed.append({
                    'item': r['item'],
                    'score': r['score'],
                    'reason': 'LLM全文档扫描（每条quote回验通过）为准，该规则条目'
                              '未被LLM集合匹配，不并入总分（留痕备查）',
                })
        check = 'pass' if llm_self else ('fail' if declared_total else 'unknown')
        red_flag = bool(declared_total) and not llm_self
        return merged, conflicts, llm_sum, check, subsumed, [], red_flag, []

    # ── 第3级：LLM不可用 → 规则通道兜底（类目双算结构自检） ──
    double_hits = _detect_double_count(rule_clean)
    if double_hits:
        return ([dict(m) for m in rule_clean], [], rule_sum,
                'fail' if declared_total else 'unknown', [], [], True, double_hits)
    check = 'pass' if rule_self else ('fail' if declared_total else 'unknown')
    return ([dict(m) for m in rule_clean], [], rule_sum, check, [], [], False, double_hits)


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


def extract_scoring_fusion(md_path, rule_items=None, llm_client=None, docx_path=None):
    """评分项提取主入口（v3：全文档扫描 + 表格整喂 + 程序裁决）。

    参数：
        md_path: 招标文件路径（md/txt/docx/pdf均可，web层传 {sid}_src.md）
        rule_items: 通道A结果（parse_bid_document 的 scoring_items）
        llm_client: LLMClient 实例（None 则仅规则通道）
        docx_path: 原始docx路径（提供时表格整表结构化喂LLM，web层传 sess['src']）
    返回 dict：
        items / total_score / declared_total / total_check / channel
        conflicts / rejected / subsumed / excluded / llm_error / needs_review
        rule_self_check / llm_used
    """
    text = _read_plain_text(md_path) if md_path else ''
    declared = _find_declared_total(text)

    table_texts = _docx_tables_structured(docx_path) if docx_path else []

    llm_items, rejected, llm_error = [], [], None
    scan_stats = {}
    rule_self_check = []
    if llm_client is not None:
        try:
            raw_items, table_norms, scan_errors, scan_stats = llm_full_scan(
                text, table_texts, llm_client, declared)
            for e in scan_errors:
                print(f'评分扫描警告: {e}', file=sys.stderr)
            merged_raw, _scan_conflicts = _merge_scan_items(raw_items)
            src_norm = _norm(text or '')
            llm_items, rejected = verify_items(
                merged_raw, src_norm, table_norms, declared)
            # LLM集合内父子双算去重（类目头+明细行被同时提取时丢弃类目头）
            llm_items = _dedupe_parent_child(llm_items)
            # 规则通道未被LLM匹配的条目 → LLM原文核验（证据制）：
            # 真实评分项补回（防LLM漏提），噪声条目淘汰（防规则误报）
            unmatched = [
                r for r in _clean_rule_items(rule_items or [])
                if not any(_match_llm_rule({'item': l['item'], 'category': l.get('category', '')}, r)
                           for l in llm_items)
            ]
            if unmatched:
                print(f'评分核验：{len(unmatched)}条规则条目未被LLM匹配，进入原文核验', file=sys.stderr)
                v_items, v_dropped = llm_verify_rule_items(text or '', unmatched, llm_client)
                # 并入守恒：核验补回的条目不得使总分超过声明总分——声明总分恒等式
                # 是第1级铁证，任何补回若破坏恒等式即为重复表述（如柯坪"分值比重30"
                # 与已核验补回的"报价30"同值），留痕拒绝、绝不静默并入
                cur = round(sum(float(l['score']) for l in llm_items), 2)
                for v in v_items:
                    if declared is not None and cur + float(v['score']) > declared + 0.01:
                        v_dropped.append({
                            'item': v['item'], 'score': v['score'],
                            'reject_reason': f'并入将使总分超过声明总分{declared:g}分，'
                                             '疑似同一分值的重复表述，留痕不并入'})
                        continue
                    cur = round(cur + float(v['score']), 2)
                    llm_items.append(v)
                rejected.extend(v_dropped)
                llm_items = _dedupe_parent_child(_merge_scan_items(llm_items)[0])
                # 已被LLM明确判定"非评分项"或因破坏总分恒等式被拒的规则条目，
                # 不再进入裁决器——防止其残留的长条目名通过包含匹配制造假conflict、
                # 或在subsumed中与rejected重复留痕。核验异常/quote回验失败的条目
                # 不在此列（未经判定，保留在规则通道由裁决器处理）
                judged_keys = {
                    _name_key(str(d.get('item', '')))
                    for d in v_dropped
                    if ('非评分项' in str(d.get('reject_reason', ''))
                        or '超过声明总分' in str(d.get('reject_reason', '')))
                }
                if judged_keys:
                    rule_items = [
                        r for r in (rule_items or [])
                        if _name_key(str(r.get('item', ''))) not in judged_keys]
        except Exception as e:
            llm_error = str(e)[:200]
            print(f'评分融合：LLM通道失败，回退规则通道 - {e}', file=sys.stderr)

    merged, conflicts, total, check, subsumed, excluded, red_flag, double_hits = arbiter(
        rule_items, llm_items, declared)
    rule_self_check = double_hits

    # 数据来源摘要（全文扫描统计）
    if scan_stats:
        source_section = (
            f"全文档结构扫描：文本片{scan_stats.get('chunks_total', 0)}个"
            f"（含评分信号{scan_stats.get('chunks_signal', 0)}个）、"
            f"结构化表格{scan_stats.get('tables_fed', 0)}张、"
            f"LLM调用{scan_stats.get('llm_calls', 0)}次"
        )
    else:
        source_section = '全文档结构扫描（LLM未启用，规则通道）' if llm_client is None else ''

    channel = 'rule'
    if llm_items and not (rule_items or []):
        channel = 'llm'
    elif llm_items:
        channel = 'fused'
    if subsumed:
        channel = 'llm'  # LLM完整集合替代规则通道
    if red_flag and llm_items:
        channel = 'llm'

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
        'excluded': excluded,
        'llm_error': llm_error,
        # 无声明总分但总分异常(>105，常为汇总行双算)也必须人工复核
        'needs_review': check == 'fail' or bool(conflicts) or red_flag
        or (declared is None and total > 105),
        'rule_self_check': rule_self_check,
        'llm_used': bool(llm_items),
    }
