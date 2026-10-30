"""大纲智能体规则验收的离线单测（不调模型）。"""
from llm_outline import finalize_sections, validate_outline_gaps


def test_scoring_and_checklist_gaps():
    sections = finalize_sections([
        {"title": "投标函", "content": "签署投标函", "part": "商务标"},
        {"title": "实施方案", "content": "进度计划安排", "part": "技术标"},
    ])
    gaps = validate_outline_gaps(
        sections,
        checklist=["投标函", "授权委托书"],
        scoring_pairs=[("进度计划安排", "6"), ("人员配置", "8")],
    )
    assert "授权委托书" in gaps["checklist_missing"]
    assert any("人员配置" in x for x in gaps["scoring_uncovered"])
    assert not any("进度计划安排" in x for x in gaps["scoring_uncovered"])
    assert gaps["ok"] is False


def test_coverage_ok():
    sections = finalize_sections([
        {
            "title": "投标函",
            "content": "对应评分项人员配置（8分）：拟派团队",
            "part": "商务标",
        },
    ])
    gaps = validate_outline_gaps(
        sections,
        checklist=["投标函"],
        scoring_pairs=[("人员配置", "8")],
    )
    assert gaps["ok"] is True
    assert gaps["checklist_missing"] == []
    assert gaps["scoring_uncovered"] == []


if __name__ == "__main__":
    test_scoring_and_checklist_gaps()
    test_coverage_ok()
    print("ok")
