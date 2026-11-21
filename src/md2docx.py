# -*- coding: utf-8 -*-
"""Markdown → Word 转换（覆盖本组文档用到的全部语法）。
标题/表格/列表/引用/代码块/粗体/行内代码/分隔线。中文排版：雅黑+宋体。
运行：python src/md2docx.py  → 报告/*.docx
"""
import os, re, sys
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACC = RGBColor(0x9A, 0x2A, 0x2A)
INK = RGBColor(0x1F, 0x29, 0x37)

def _set_font(run, size=11, bold=False, color=None, mono=False, name=None):
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    zh = name or ("Consolas" if mono else "微软雅黑")
    run.font.name = zh
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体" if mono else zh)

def _inline(par, text, size=11, base_bold=False, color=None):
    """解析 **粗体**、`行内代码`、*斜体*。"""
    tokens = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)
    for tk in tokens:
        if not tk:
            continue
        if tk.startswith("**") and tk.endswith("**"):
            r = par.add_run(tk[2:-2]); _set_font(r, size, True, color)
        elif tk.startswith("`") and tk.endswith("`"):
            r = par.add_run(tk[1:-1]); _set_font(r, size - 0.5, base_bold, RGBColor(0x8B, 0x2E, 0x2E), mono=True)
        else:
            r = par.add_run(tk); _set_font(r, size, base_bold, color)

def _code_block(doc, lines):
    for ln in lines:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0); p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(0.6)
        r = p.add_run(ln if ln else " ")
        _set_font(r, 9.5, False, RGBColor(0x33, 0x41, 0x55), mono=True)

def _table(doc, rows):
    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            txt = row[j] if j < len(row) else ""
            _inline(p, txt, size=10, base_bold=(i == 0))
            if i == 0:
                sh = cell._tc.get_or_add_tcPr()
                el = sh.makeelement(qn("w:shd"), {qn("w:fill"): "F5E9E9"})
                sh.append(el)

def convert(md_path, docx_path):
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "微软雅黑"; st.font.size = Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    for sec in doc.sections:
        sec.top_margin = sec.bottom_margin = Cm(2.2)
        sec.left_margin = sec.right_margin = Cm(2.4)

    lines = open(md_path, encoding="utf-8").read().splitlines()
    i, in_code, code_buf, table_buf = 0, False, [], []
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            if in_code:
                _code_block(doc, code_buf); code_buf = []
            in_code = not in_code; i += 1; continue
        if in_code:
            code_buf.append(ln); i += 1; continue
        # 表格收集
        if ln.strip().startswith("|") and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")]]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            _table(doc, rows); doc.add_paragraph(); continue
        s = ln.strip()
        if not s:
            i += 1; continue
        if s.startswith("#"):
            m = re.match(r"(#+)\s+(.*)", s)
            lvl = len(m.group(1)); txt = m.group(2)
            if lvl == 1:
                p = doc.add_heading(level=0)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _inline(p, txt, 20, True, INK)
            else:
                p = doc.add_heading(level=min(lvl - 1, 3))
                _inline(p, txt, {1: 16, 2: 14, 3: 12}.get(lvl - 1, 12), True,
                        ACC if lvl == 2 else INK)
        elif s.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            _inline(p, s.lstrip("> ").strip(), 10.5, False, SUB := RGBColor(0x64, 0x74, 0x8B))
        elif re.match(r"^\s*[-*]\s+", ln):
            p = doc.add_paragraph(style="List Bullet")
            _inline(p, re.sub(r"^\s*[-*]\s+", "", ln))
        elif re.match(r"^\s*\d+\.\s+", ln):
            p = doc.add_paragraph(style="List Number")
            _inline(p, re.sub(r"^\s*\d+\.\s+", "", ln))
        elif s == "---":
            p = doc.add_paragraph(); r = p.add_run("─" * 40)
            _set_font(r, 9, False, RGBColor(0xD5, 0xCE, 0xC4))
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        else:
            p = doc.add_paragraph()
            _inline(p, s)
        i += 1
    doc.save(docx_path)
    return docx_path

if __name__ == "__main__":
    jobs = [("研究报告.md", "研究报告.docx"),
            ("报告白话版.md", "报告白话版.docx"),
            ("知识点讲解.md", "知识点讲解.docx"),
            ("演讲稿.md", "演讲稿.docx"),
            ("README.md", "README.docx")]
    for src, dst in jobs:
        out = convert(os.path.join(ROOT, src), os.path.join(ROOT, dst))
        print("→", dst, os.path.getsize(out), "bytes")
