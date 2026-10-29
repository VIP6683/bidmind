"""PDF 扫描页检测 + RapidOCR 本地离线识别（新增模块，可整体回退）。

职责：
    - 检测 PDF 中无文字层的扫描页（字符数低于阈值）
    - 扫描页渲染图片走 RapidOCR（纯 CPU，模型随包内置，数据不出网）
    - 文字层丰富的页仍走 pdfplumber（保表格结构），逐页组装 markdown

用法（Web 后端 normalize_to_md 调用）：
    from pdf_ocr import pdf_needs_ocr, pdf_to_md_via_ocr
    if pdf_needs_ocr(pdf):
        info = pdf_to_md_via_ocr(pdf, md_path)  # {"total_pages", "ocr_pages", ...}

命令行自测：
    python pdf_ocr.py 招标文件.pdf -o out.md
"""
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF

# 页文字层字符数低于该值即判定为扫描页（封面/扉页通常也 < 30 字）
OCR_MIN_CHARS = 30
# OCR 渲染倍率（1.5 ≈ 108DPI，实测比 2.0 快 ~25% 且识别字符数不降）
OCR_ZOOM = 1.5
# onnxruntime 推理线程数（实测本机 4 线程优于全核）
OCR_THREADS = 4

_ENGINE = None  # RapidOCR 懒加载单例（首次初始化约 2-4 秒）


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR(
            intra_op_num_threads=OCR_THREADS,
            inter_op_num_threads=OCR_THREADS,
        )
    return _ENGINE


def count_page_chars(pdf_path) -> list[int]:
    """逐页统计文字层字符数（毫秒级，用于扫描页判定）。"""
    doc = fitz.open(str(pdf_path))
    try:
        return [len(page.get_text().strip()) for page in doc]
    finally:
        doc.close()


def pdf_needs_ocr(pdf_path) -> bool:
    """扫描页（无文字层）数量与占比双达标才触发 OCR。

    数字版 PDF 的封面/分隔页也可能字符稀少（实测 115 页标里有 1 页仅 22 字符），
    单页低于阈值不能作为依据：需 ≥2 页扫描页且占比 ≥10%。
    """
    try:
        chars = count_page_chars(pdf_path)
        if not chars:
            return False
        scanned = sum(1 for n in chars if n < OCR_MIN_CHARS)
        return scanned >= 2 and scanned >= len(chars) * 0.1
    except Exception as e:
        print(f"扫描页检测失败（按无需OCR处理）：{e}", file=sys.stderr)
        return False


def ocr_page(page) -> str:
    """单页 OCR：渲染 pixmap → RapidOCR → 按 y 坐标分行、行内按 x 排序拼接。"""
    import numpy as np

    pix = page.get_pixmap(matrix=fitz.Matrix(OCR_ZOOM, OCR_ZOOM))
    # pixmap.samples 为 RGB，RapidOCR 按 cv2 惯例期望 BGR
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = np.ascontiguousarray(img[:, :, :3][:, :, ::-1])

    result, _ = _get_engine()(img, use_cls=False)  # 印刷体扫描件方向正确，关 cls 省一次推理
    if not result:
        return ""
    # result 元素: [box(4点), text, score]
    boxes = []
    for box, text, _score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((min(ys), min(xs), max(xs), max(ys), text))
    # 按 y 中心聚类成行（行高中位数的 0.6 倍内视为同一行）
    boxes.sort(key=lambda b: (b[0] + b[3]) / 2)
    heights = sorted(b[3] - b[0] for b in boxes)
    line_h = max(heights[len(heights) // 2], 10) if heights else 10
    lines: list[list] = []
    for b in boxes:
        yc = (b[0] + b[3]) / 2
        if lines and abs(yc - lines[-1][0]) < line_h * 0.6:
            lines[-1][1].append(b)
        else:
            lines.append([yc, [b]])
    out = []
    for _yc, items in lines:
        items.sort(key=lambda b: b[1])
        # 表格行还原：块间水平间隙大（≥40px）视为不同单元格，用 " | " 连接，
        # 使 OCR 文本保持 md 表格行形态，下游 parse_bid 的表格正则才能命中
        parts: list[str] = []
        prev_max_x = None
        for b in items:
            if prev_max_x is not None and b[1] - prev_max_x >= 40:
                parts.append("|")
            parts.append(b[4])
            prev_max_x = b[2]
        if "|" in parts:  # 多单元格行 → 补首尾竖线，成为完整 md 表格行 | a | b | c |
            out.append("| " + " ".join(parts) + " |")
        else:
            out.append(" ".join(parts))
    return "\n".join(out)


def pdf_to_md_via_ocr(pdf_path, md_path) -> dict:
    """混合模式逐页转 markdown：文字页 pdfplumber（保表格），扫描页 OCR。

    返回 {"total_pages": int, "ocr_pages": int, "out_path": str}
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from parse_bid import _table_to_markdown  # 复用既有表格转md逻辑，不复制代码

    t0 = time.time()
    pdf_path = str(pdf_path)
    doc = fitz.open(pdf_path)
    page_chars = [len(p.get_text().strip()) for p in doc]
    total = doc.page_count
    md_parts: list[str] = []
    ocr_pages = 0

    # pdfplumber 一次性打开，文字页复用其表格结构化能力
    plumber_pdf = None
    try:
        import pdfplumber

        plumber_pdf = pdfplumber.open(pdf_path)
    except Exception as e:
        print(f"pdfplumber 不可用，文字页走 fitz 纯文本：{e}", file=sys.stderr)

    for idx, page in enumerate(doc):
        if page_chars[idx] >= OCR_MIN_CHARS:
            page_md = ""
            if plumber_pdf is not None:
                try:
                    ppage = plumber_pdf.pages[idx]
                    page_md = ppage.extract_text() or ""
                    tables = [
                        _table_to_markdown(t)
                        for t in ppage.extract_tables()
                        if t and t[0]
                    ]
                    tables = [t for t in tables if t]
                    if tables:
                        page_md += "\n\n" + "\n\n".join(tables)
                except Exception as e:
                    print(f"第{idx + 1}页 pdfplumber 提取失败，兜底 fitz：{e}", file=sys.stderr)
                    page_md = page.get_text()
            else:
                page_md = page.get_text()
        else:
            ocr_pages += 1
            page_md = ocr_page(page)
            print(f"[OCR] 第 {idx + 1}/{total} 页完成（累计 {time.time() - t0:.0f}s）",
                  file=sys.stderr)
        md_parts.append(page_md.strip())

    if plumber_pdf is not None:
        plumber_pdf.close()
    doc.close()

    md_path = Path(md_path)
    md_path.write_text("\n\n".join(p for p in md_parts if p), encoding="utf-8")
    return {
        "total_pages": total,
        "ocr_pages": ocr_pages,
        "text_pages": total - ocr_pages,
        "elapsed_sec": round(time.time() - t0, 1),
        "out_path": str(md_path),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="PDF 扫描页 OCR 转 markdown")
    ap.add_argument("pdf", help="输入 PDF 路径")
    ap.add_argument("-o", "--output", help="输出 md 路径（默认同名 .md）")
    args = ap.parse_args()
    out = args.output or str(Path(args.pdf).with_suffix(".md"))
    if not pdf_needs_ocr(args.pdf):
        print("未检测到扫描页，无需 OCR。")
        sys.exit(0)
    info = pdf_to_md_via_ocr(args.pdf, out)
    print(f"完成：总 {info['total_pages']} 页，OCR {info['ocr_pages']} 页，"
          f"耗时 {info['elapsed_sec']}s → {info['out_path']}")
