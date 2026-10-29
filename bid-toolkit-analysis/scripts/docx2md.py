"""docx -> markdown 转换（段落 + 表格转 markdown 表格），供 parse_bid 使用。"""
import sys
from pathlib import Path
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_block_items(parent):
    from docx.oxml.ns import qn
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, parent)
        elif child.tag == qn('w:tbl'):
            yield Table(child, parent)


def table_to_md(table: Table) -> str:
    rows = []
    for r in table.rows:
        cells = [' '.join(c.text.split()) or ' ' for c in r.cells]
        rows.append('| ' + ' | '.join(cells) + ' |')
        if len(rows) == 1:
            rows.append('|' + '---|' * len(cells))
    return '\n'.join(rows)


def docx_to_md(path: Path) -> str:
    doc = Document(str(path))
    parts = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if not t:
                continue
            style = (block.style.name or '').lower()
            if 'heading 1' in style:
                parts.append(f'# {t}')
            elif 'heading 2' in style:
                parts.append(f'## {t}')
            elif 'heading 3' in style:
                parts.append(f'### {t}')
            elif 'heading' in style:
                parts.append(f'#### {t}')
            else:
                parts.append(t)
        else:
            parts.append(table_to_md(block))
    return '\n\n'.join(parts)


if __name__ == '__main__':
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix('.md')
    dst.write_text(docx_to_md(src), encoding='utf-8')
    print(f'OK: {dst} ({len(dst.read_text(encoding="utf-8"))} 字符)')
