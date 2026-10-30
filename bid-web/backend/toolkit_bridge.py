"""把 bid-toolkit-analysis 已有脚本能力接到 Web（先接线、后优化）。

当前接入：
  - 排版引擎 bid_engine.md_to_docx（模板 / 暗标）
  - 格式自检 fix_bid_format.scan_document
  - AI 味检测 ai_flavor_radar.FlavorRadar
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2] / "bid-toolkit-analysis"
SCRIPTS = REPO / "scripts"
TEMPLATES = REPO / "templates"

FORMAT_TEMPLATES = ("government", "enterprise", "engineering", "goods")


def _ensure_scripts_path() -> None:
    p = str(SCRIPTS)
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_script(name: str):
    _ensure_scripts_path()
    path = SCRIPTS / name
    if not path.exists():
        raise FileNotFoundError(f"脚本不存在: {path}")
    spec = importlib.util.spec_from_file_location(f"toolkit_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    # 部分脚本互相 import 同目录模块，需挂到 sys.modules
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def list_format_templates() -> list[dict]:
    """可用排版模板（yaml 存在才列出）。"""
    labels = {
        "government": "政府采购（宋体/1.5倍行距）",
        "enterprise": "企业投标",
        "engineering": "工程类",
        "goods": "货物类",
    }
    out = []
    for key in FORMAT_TEMPLATES:
        yml = TEMPLATES / f"{key}.yaml"
        out.append({
            "id": key,
            "label": labels.get(key, key),
            "available": yml.exists(),
            "path": str(yml) if yml.exists() else None,
        })
    return out


def export_with_engine(
    md_path: Path,
    out_path: Path,
    template: str | None = "government",
    dark_mode: bool = False,
    auto_fix: bool = True,
) -> dict[str, Any]:
    """Markdown → Word，走标书排版引擎（非简易 md2docx）。"""
    engine = _load_script("bid_engine.py")
    tpl = template if template in FORMAT_TEMPLATES else None
    if tpl == "goods" and not (TEMPLATES / "goods.yaml").exists():
        tpl = "government"
    config = engine.load_config(template_name=tpl) if tpl else engine.load_config()
    md_text = Path(md_path).read_text(encoding="utf-8")
    engine.md_to_docx(
        md_text,
        str(out_path),
        auto_fix=auto_fix,
        dark_mode=dark_mode,
        config=config,
    )
    return {
        "ok": out_path.exists(),
        "output": str(out_path),
        "template": tpl or "default",
        "dark_mode": dark_mode,
        "engine": "bid_engine",
    }


def format_check_docx(docx_path: Path, auto_fix: bool = False) -> dict[str, Any]:
    """对 Word 做格式自检；可选自动修复并另存。"""
    from docx import Document  # noqa: PLC0415

    fix = _load_script("fix_bid_format.py")
    doc = Document(str(docx_path))
    issues = fix.scan_document(doc, str(docx_path))
    # 序列化：去掉不可 JSON 的对象
    serial_issues = []
    by_type: dict[str, int] = {}
    for it in issues:
        if not isinstance(it, dict):
            continue
        t = str(it.get("type") or "其他")
        by_type[t] = by_type.get(t, 0) + 1
        serial_issues.append({
            "type": t,
            "subtype": it.get("subtype") or "",
            "paragraph": it.get("paragraph", 0),
            "detail": str(it.get("detail") or "")[:300],
            "text": str(it.get("text") or "")[:120],
        })
    fixed_count = 0
    fixed_path = None
    if auto_fix and serial_issues:
        fixed_count = fix.fix_document(doc, issues, str(docx_path))
        fixed_path = docx_path.with_name(docx_path.stem + "_格式修复.docx")
        doc.save(str(fixed_path))
    report = fix.generate_report(issues, docx_path.name)
    return {
        "ok": True,
        "file": str(docx_path),
        "issue_count": len(serial_issues),
        "by_type": by_type,
        "issues": serial_issues[:200],
        "report": report,
        "fixed_count": fixed_count,
        "fixed_path": str(fixed_path) if fixed_path else None,
        "source": "fix_bid_format.scan_document",
    }


def ai_flavor_scan_text(text: str, file_path: str = "<draft>") -> dict[str, Any]:
    """AI 味雷达（结构化结果）。"""
    radar_mod = _load_script("ai_flavor_radar.py")
    radar = radar_mod.FlavorRadar(mode="bid")
    result = radar.scan(text or "", file_path=file_path)
    # 复用官方 JSON 结构
    payload = json.loads(radar_mod.format_json_report(result))
    payload["source"] = "ai_flavor_radar"
    return payload


def ai_flavor_scan_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.suffix.lower() == ".docx":
        from docx import Document  # noqa: PLC0415
        doc = Document(str(path))
        text = "\n".join(p.text for p in doc.paragraphs)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    return ai_flavor_scan_text(text, file_path=str(path))


def capability_matrix() -> list[dict]:
    """源码能力 vs Web 接入状态（供前端/产品对照）。"""
    return [
        {"id": "engine", "name": "标书排版引擎", "cli": "bid engine", "web": True, "complete": True},
        {"id": "format_check", "name": "格式自检", "cli": "bid check", "web": True, "complete": True},
        {"id": "ai_flavor", "name": "AI味检测", "cli": "bid desense(雷达)", "web": True, "complete": True},
        {"id": "desense_words", "name": "敏感词目录扫描", "cli": "desensitization_scan", "web": False, "complete": "partial", "note": "脚本面向目录批扫，需改成单文件扫描再接"},
        {"id": "dark_mode", "name": "暗标模式", "cli": "bid engine --暗标", "web": True, "complete": True, "note": "随排版引擎参数暴露"},
        {"id": "templates", "name": "多格式模板", "cli": "--template", "web": True, "complete": True},
        {"id": "pitfall", "name": "踩坑检查", "cli": "pitfall_check", "web": False, "complete": True},
        {"id": "typo", "name": "错别字", "cli": "bid_typo_check", "web": False, "complete": True},
        {"id": "consistency", "name": "前后不一致", "cli": "bid_consistency_check", "web": False, "complete": True},
        {"id": "similarity", "name": "查重", "cli": "bid_similarity", "web": False, "complete": True},
        {"id": "rfp", "name": "RFP招标生成", "cli": "bid rfp", "web": False, "complete": True},
        {"id": "materials", "name": "素材库管家", "cli": "bid materials", "web": False, "complete": True},
        {"id": "watermark", "name": "水印", "cli": "bid watermark", "web": False, "complete": True},
        {"id": "mermaid", "name": "Mermaid图表", "cli": "bid engine --mermaid-api", "web": False, "complete": "partial"},
        {"id": "gui", "name": "桌面GUI", "cli": "bid gui", "web": False, "complete": True, "note": "与 Web 产品并行，按需启动"},
        {"id": "orchestrate", "name": "编排/框架锁定", "cli": "bid orchestrate", "web": False, "complete": True},
        {"id": "render", "name": "content.json渲染", "cli": "bid render", "web": False, "complete": True},
    ]
