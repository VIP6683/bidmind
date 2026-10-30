# 投标大纲生成提示词

## 用途

招标文件解析完成后（评分融合之后），由 LLM 生成投标初稿的章节大纲。产物为结构化 JSON（`{sid}_大纲.json`），初稿生成时转换为章节列表逐章撰写正文。

**骨架优先级（高于开源 bid-toolkit 的类型模板）：**

1. 招标文件「第六章 投标文件格式 / 目录」清单 — **硬约束保序**
2. 评分树（父项 + `名称（0-N分）` 子项）— 技术章展开深度
3. 采购需求硬点 — 写入 content
4. 货物/服务类型模板 — **仅作禁止清单**，不得当主骨架

## System Prompt

与 `bid-web/backend/llm_outline.py` 中 `SYSTEM_PROMPT` 保持同步（以代码为准）。

核心约束摘要：

1. 清单是唯一骨架，标题原文、顺序不乱
2. 评分父子项全部写入 content（高分项如实施方案拆 5×6 分）
3. 禁止编造清单外商务文件名；禁止无关安装/培训章
4. content 写给人看的编制要点，不是空套话
5. 硬点显性化：封条、社保三个月、人数下限、行业划型等

## User Prompt 模板

```
【投标文件组成清单（硬约束，逐条保序）】
{checklist}

【评分树（父项+子项，须全部写入大纲 content）】
{scoring_items}

【评标办法/评分标准章节原文】
{scoring_section}

【项目简报】
{project_brief}

【采购需求摘录】
{demand_excerpt}

【禁止套用的无关模板章（除非清单原文出现）】
{forbidden_template_titles}

请输出投标文件章节大纲 JSON。
```

## AI 介入时机

| 时机 | 条件 | 作用 |
|------|------|------|
| AI① 补目录 | 规则清单为空 | 从第六章摘录补 `items` |
| AI② 评分融合 | 有 Key | 校正评分项（既有 scoring_fusion） |
| AI③ 起草大纲 | 有 Key | 按目录+评分树写 content |
| AI④ 回修 | 覆盖验收有缺口 | 全量 sections 回修 |
| AI⑤ 按章正文 | 用户触发 generate | 下游初稿（非本模块） |

无 Key：规则按目录出章；目录也没有则 `rules_incomplete` 标红，**禁止静默套 goods_outline**。

## 输出格式

```json
{
  "sections": [
    {
      "title": "投标函",
      "content": "…编制要点…",
      "part": "商务标"
    }
  ]
}
```

## 回归样例

柯坪县冬季救助煤：`demo_output/web/1065d6d5f59a_src.md`

- 清单 ≥14 条，含投标函/业绩/技术文件/中小企业声明函
- 实施方案 criteria 拆出进度/运输/验收/装卸/退换货子项
- 规则大纲无「培训方案」「安装调试」
