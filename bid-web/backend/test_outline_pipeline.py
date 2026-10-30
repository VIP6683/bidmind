"""大纲主链路离线回归：清单抽取 + 评分树拆分 + 规则大纲（不调模型）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "bid-toolkit-analysis"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse_bid  # noqa: E402
import llm_outline  # noqa: E402

KEPING_MD = ROOT / "demo_output" / "web" / "1065d6d5f59a_src.md"


def test_keping_format_toc_checklist():
    text = KEPING_MD.read_text(encoding="utf-8")
    items = parse_bid.extract_document_checklist(text)
    assert len(items) >= 14, items
    assert items[0] == "投标函"
    assert "业绩" in items
    assert "技术文件" in items
    assert "中小企业声明函" in items
    # 不得把格式正文「一、投标函」后的填空段卷进来
    assert not any("经营地址" in x for x in items)


def test_expand_implementation_children():
    criteria = (
        "实施方案包含（1）项目进度计划安排（0-6分）：节点清晰；"
        "（2）运输配送方案（0-6分）：运力配置；"
        "（3）货物交接、验收流程（0-6分）：过磅计量；"
        "（4）货物装卸流程及装卸管理方案（0-6分）：扬尘管控；"
        "（5）退换货流程（0-6分）：清运时限。"
    )
    kids = llm_outline.expand_scoring_children(criteria)
    names = [k["item"] for k in kids]
    assert any("进度" in n for n in names), names
    assert any("运输" in n for n in names), names
    assert any("验收" in n or "交接" in n for n in names), names
    assert any("装卸" in n for n in names), names
    assert any("退换" in n for n in names), names
    assert len(kids) >= 5


def test_rule_outline_uses_checklist_not_goods_template(tmp_path: Path | None = None):
    from pathlib import Path as P
    out = P(tmp_path) if tmp_path else ROOT / "demo_output" / "web" / "_test_outline"
    out.mkdir(parents=True, exist_ok=True)
    text = KEPING_MD.read_text(encoding="utf-8")
    rfp = {
        "文件清单": parse_bid.extract_document_checklist(text),
        "scoring_items": [
            {
                "评分项": "项目实施方案",
                "分值": 30,
                "评分标准": (
                    "（1）项目进度计划安排（0-6分）；"
                    "（2）运输配送方案（0-6分）；"
                    "（3）货物交接、验收流程（0-6分）；"
                    "（4）货物装卸流程及装卸管理方案（0-6分）；"
                    "（5）退换货流程（0-6分）"
                ),
            },
            {"评分项": "人员配备", "分值": 10, "评分标准": "至少10人，近三个月社保"},
            {"评分项": "报价", "分值": 30, "评分标准": "最低价基准"},
        ],
    }
    outline = llm_outline.generate_rule_outline(
        "testchk",
        str(KEPING_MD),
        rfp,
        out,
        fusion=None,
        checklist_meta={"source": "rule", "count": len(rfp["文件清单"])},
    )
    assert outline is not None
    titles = [s["title"] for s in outline["sections"]]
    assert titles[0] == "投标函"
    assert "技术文件" in titles
    assert not any("培训方案" in t for t in titles)
    assert not any("安装" in t for t in titles)
    blob = "".join(s.get("content") or "" for s in outline["sections"])
    assert "进度" in blob or "运输" in blob


def test_rule_outline_empty_checklist_no_silent_template(tmp_path=None):
    from pathlib import Path as P
    out = P(tmp_path) if tmp_path else ROOT / "demo_output" / "web" / "_test_outline2"
    out.mkdir(parents=True, exist_ok=True)
    rfp = {"文件清单": [], "scoring_items": [{"评分项": "人员配备", "分值": 10, "评分标准": ""}]}
    outline = llm_outline.generate_rule_outline(
        "testempty",
        str(KEPING_MD),
        rfp,
        out,
        checklist_meta={"source": "missing", "empty": True, "count": 0},
    )
    assert outline is not None
    assert outline.get("source") == "rules_incomplete"
    titles = [s["title"] for s in outline["sections"]]
    assert any("未识别" in t for t in titles)
    assert not any("培训方案" in t for t in titles)


if __name__ == "__main__":
    test_keping_format_toc_checklist()
    test_expand_implementation_children()
    test_rule_outline_uses_checklist_not_goods_template()
    test_rule_outline_empty_checklist_no_silent_template()
    # 既有验收
    from test_outline_agent import test_coverage_ok, test_scoring_and_checklist_gaps

    test_scoring_and_checklist_gaps()
    test_coverage_ok()
    print("ok")
