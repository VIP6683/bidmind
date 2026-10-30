"""分析智能体：规则抽取 + 模型语义，自动产出要点、评分与大纲。

分工（不可互换）：
    - 本地规则：读文件、抽表格、清单保序、商务/技术归类、总分恒等式、覆盖验收
    - 模型：读懂评标表与采购需求、起草章节大纲、按验收缺口回修
失败降级：无 Key / 模型报错时仍给出规则大纲，并在轨迹里标明 degraded。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


def _step(name: str, status: str, detail: str, elapsed: float | None = None) -> dict:
    rec = {"name": name, "status": status, "detail": detail}
    if elapsed is not None:
        rec["elapsed_sec"] = round(elapsed, 2)
    return rec


def run(
    sid: str,
    md_path: str,
    src_path: str,
    extra_tables,
    output_dir: Path,
    llm_client,
    match_score=None,
    clean=None,
    norm=None,
) -> dict:
    """执行解析智能体。返回 rfp / fusion / outline / agent。"""
    steps: list[dict] = []
    t_all = time.time()
    llm_ok = llm_client is not None and getattr(llm_client, "is_available", lambda: True)()
    mode = "hybrid" if llm_ok else "rules_only"

    # 1) 规则解析招标文件
    t0 = time.time()
    import parse_bid  # noqa: PLC0415

    rfp = parse_bid.parse_bid_document(str(md_path), extra_tables=extra_tables)
    n_score = len(rfp.get("scoring_items") or [])
    n_list = len(rfp.get("文件清单") or [])
    steps.append(_step(
        "rule_parse", "ok",
        f"规则抽取：清单 {n_list} 条，评分项候选 {n_score} 条",
        time.time() - t0,
    ))

    # 2) 评分双通道（规则候选 + 模型校正 + 程序裁决）
    fusion = None
    t0 = time.time()
    try:
        import scoring_fusion  # noqa: PLC0415

        fusion = scoring_fusion.extract_scoring_fusion(
            str(md_path),
            rfp.get("scoring_items", []),
            llm_client if llm_ok else None,
            docx_path=str(src_path) if str(src_path).lower().endswith(".docx") else None,
        )
        (Path(output_dir) / f"{sid}_评分融合.json").write_text(
            json.dumps(fusion, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        channel = (fusion or {}).get("channel") or "rule"
        n_items = len((fusion or {}).get("items") or [])
        steps.append(_step(
            "scoring_fusion", "ok",
            f"评分融合通道 {channel}，采纳 {n_items} 项"
            + ("（无模型，仅规则）" if not llm_ok else ""),
            time.time() - t0,
        ))
    except Exception as exc:  # noqa: BLE001
        steps.append(_step(
            "scoring_fusion", "error",
            f"融合失败，沿用规则评分项：{type(exc).__name__}: {exc}",
            time.time() - t0,
        ))

    # 3) 大纲智能体：模型起草 → 规则验收 → 缺口回修
    t0 = time.time()
    import llm_outline  # noqa: PLC0415

    outline, outline_steps = llm_outline.run_outline_agent(
        sid, str(md_path), rfp, fusion, llm_client if llm_ok else None, output_dir,
        match_score=match_score, clean=clean, norm=norm,
    )
    for st in outline_steps:
        st.setdefault("elapsed_sec", None)
        steps.append(st)
    if outline_steps:
        steps[-1]["elapsed_sec"] = round(time.time() - t0, 2)

    source = (outline or {}).get("source") or ("rules_fallback" if not llm_ok else "unknown")
    agent = {
        "sid": sid,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "llm_used": bool(llm_ok),
        "outline_source": source,
        "outline_chapters": len((outline or {}).get("sections") or []),
        "elapsed_sec": round(time.time() - t_all, 2),
        "steps": steps,
        "warning": None if llm_ok else "未配置 AI 模型，大纲与评分仅走本地规则，偏差会明显大于智能体路径。请到「AI模型配置」填写 Key 后重新分析。",
    }
    (Path(output_dir) / f"{sid}_智能体.json").write_text(
        json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {"rfp": rfp, "fusion": fusion, "outline": outline, "agent": agent}
