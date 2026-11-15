"""Tiny document model rendered to both HTML->PDF (Chrome) and DOCX (python-docx), styled like our Phase-2 report."""
from __future__ import annotations

import html
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BLUE, NAVY, INK = "#2E75B5", "#1F3864", "#1A1A1A"


class Doc:
    def __init__(self):
        self.blocks = []
        self.fig_no = 0
        self.tab_no = 0

    # ---- builders
    def h1(self, t): self.blocks.append(("h1", t))
    def h2(self, t): self.blocks.append(("h2", t))
    def h3(self, t): self.blocks.append(("h3", t))
    def p(self, t): self.blocks.append(("p", t))
    def bullets(self, items): self.blocks.append(("ul", items))
    def numbered(self, items): self.blocks.append(("ol", items))
    def code(self, t, caption=None): self.blocks.append(("code", t, caption))
    def pagebreak(self): self.blocks.append(("pb",))
    def note(self, t): self.blocks.append(("note", t))

    def table(self, header, rows, caption=None, widths=None, small=False):
        cap = None
        if caption:
            self.tab_no += 1
            cap = f"Table {self.tab_no}: {caption}"
        self.blocks.append(("table", header, rows, cap, widths, small))
        return self.tab_no

    def fig(self, path, caption, width=100):
        self.fig_no += 1
        self.blocks.append(("fig", path, f"Figure {self.fig_no}: {caption}", width))
        return self.fig_no

    # =========================================================================== HTML / PDF
    def to_html(self, cover: dict) -> str:
        css = f"""
@import url('https://fonts.googleapis.com/css2?family=Carlito:ital,wght@0,400;0,700;1,400&display=swap');
@page {{ size: Letter; margin: 0.78in 0.72in 0.8in 0.72in; }}
body {{ font-family: Carlito, Calibri, 'Segoe UI', Arial, sans-serif; color:{INK}; font-size:11pt; line-height:1.38; margin:0; }}
h1 {{ color:{BLUE}; font-weight:400; font-size:17pt; margin:16pt 0 6pt; page-break-after:avoid; }}
h2 {{ color:{NAVY}; font-size:12.5pt; margin:12pt 0 4pt; page-break-after:avoid; }}
h3 {{ color:{NAVY}; font-size:11pt; margin:9pt 0 3pt; page-break-after:avoid; }}
p {{ margin:0 0 6pt; text-align:justify; }}
ul,ol {{ margin:2pt 0 7pt 0; padding-left:18pt; }} li {{ margin-bottom:2.5pt; text-align:justify; }}
table {{ border-collapse:collapse; width:100%; margin:4pt 0 3pt; font-size:9.6pt; page-break-inside:auto; }}
table.small {{ font-size:8.6pt; }}
tr {{ page-break-inside:avoid; }}
th {{ background:{NAVY}; color:#fff; text-align:left; padding:4pt 5pt; font-weight:700; border:0.6pt solid {NAVY}; }}
td {{ padding:3.5pt 5pt; border:0.6pt solid #b8c2d6; vertical-align:top; }}
tr:nth-child(even) td {{ background:#f3f6fb; }}
.cap {{ font-size:9.2pt; color:#3d4a5c; font-style:italic; margin:2pt 0 9pt; text-align:center; }}
.fig {{ text-align:center; margin:6pt 0 2pt; page-break-inside:avoid; }}
.fig img {{ max-width:100%; }}
pre {{ font-family:Menlo,Consolas,monospace; font-size:8.1pt; background:#f5f7fa; border:0.6pt solid #d0d7e2; padding:6pt 8pt;
      white-space:pre-wrap; line-height:1.3; margin:3pt 0 8pt; page-break-inside:avoid; }}
code {{ font-family:Menlo,Consolas,monospace; font-size:9pt; background:#f1f3f7; padding:0 2pt; }}
.note {{ border-left:3pt solid {BLUE}; background:#f2f7fc; padding:5pt 9pt; margin:4pt 0 9pt; }}
.pb {{ page-break-after:always; }}
.cover {{ height:8.9in; display:flow-root; position:relative; page-break-after:always; text-align:center; font-family:Carlito,Calibri,sans-serif; }}
.cover .dept {{ font-weight:700; font-size:15pt; margin-top:0.45in; line-height:2.1; }}
.cover img {{ width:1.5in; margin:0.32in 0 0.25in; }}
.cover .course {{ font-weight:700; font-size:20pt; margin-top:0.25in; }}
.cover .phase {{ font-weight:700; font-size:16pt; margin-top:4pt; line-height:1.3; }}
.cover .meta {{ text-align:left; margin:0.42in 0 0 0.15in; font-size:12pt; }}
.cover .meta b {{ font-size:13pt; }}
.cover .meta table {{ width:auto; border:0; margin:6pt 0; font-size:12pt; }}
.cover .meta td {{ border:0; padding:2pt 0; background:none !important; }}
"""
        out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(cover['title'])}</title><style>{css}</style></head><body>"]
        c = cover
        out.append(f"""<div class=cover><div class=dept>-DEPARTMENT OF SOFTWARE ENGINEERING-<br>-FAST NUCES ISLAMABAD-</div>
<img src="{c['logo']}"><div class=course>Comp Networks</div><div class=phase>{c['phase1']}<br>{c['phase2']}</div>
<div class=meta><b>Submitted to:</b><div style="margin:12pt 0 18pt 1.45in">{c['to']}</div><b>Submitted by:</b>
<table><tr><td style="width:2.2in">{c['students'][0][0]}</td><td>{c['students'][0][1]}</td></tr>
<tr><td>{c['students'][1][0]}</td><td>{c['students'][1][1]}</td></tr>
<tr><td><b style="font-size:12pt">Class / Section:</b></td><td>{c['section']}</td></tr></table>
<div style="margin-top:22pt"><b style="font-size:12pt">Date :</b> &nbsp;&nbsp;&nbsp; <b><u>{c["date"]}</u></b></div></div></div>""")
        for b in self.blocks:
            k = b[0]
            if k == "h1":
                out.append(f"<h1>{b[1]}</h1>")
            elif k == "h2":
                out.append(f"<h2>{b[1]}</h2>")
            elif k == "h3":
                out.append(f"<h3>{b[1]}</h3>")
            elif k == "p":
                out.append(f"<p>{b[1]}</p>")
            elif k == "note":
                out.append(f"<div class=note>{b[1]}</div>")
            elif k in ("ul", "ol"):
                out.append(f"<{k}>" + "".join(f"<li>{i}</li>" for i in b[1]) + f"</{k}>")
            elif k == "code":
                out.append(f"<pre>{html.escape(b[1])}</pre>" + (f"<div class=cap>{b[2]}</div>" if b[2] else ""))
            elif k == "pb":
                out.append("<div class=pb></div>")
            elif k == "table":
                _, header, rows, cap, widths, small = b
                ws = "".join(f"<col style='width:{w}%'>" for w in widths) if widths else ""
                h = f"<table class='{'small' if small else ''}'><colgroup>{ws}</colgroup><tr>" + "".join(f"<th>{x}</th>" for x in header) + "</tr>"
                for r in rows:
                    h += "<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>"
                out.append(h + "</table>" + (f"<div class=cap>{cap}</div>" if cap else ""))
            elif k == "fig":
                _, path, cap, w = b
                out.append(f"<div class=fig><img src='{path}' style='width:{w}%'></div><div class=cap>{cap}</div>")
        out.append("</body></html>")
        return "\n".join(out)

    def to_pdf(self, html_path: str, pdf_path: str, title: str):
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=15000",
                        f"--print-to-pdf={pdf_path}", "file://" + os.path.abspath(html_path)], check=True, capture_output=True,
                       timeout=600)
        import fitz
        d = fitz.open(pdf_path)
        for i, pg in enumerate(d):
            r = pg.rect
            pg.draw_rect(fitz.Rect(36, 36, r.width - 36, r.height - 36), color=(0, 0, 0), width=1.2)
            if i > 0:
                pg.insert_text((r.width / 2 - 8, r.height - 46), str(i + 1), fontsize=9, fontname="helv", color=(0.25, 0.25, 0.25))
        d.set_metadata({"title": title, "author": "Ahmed Tanweer, Salman Ahmad"})
        tmp = pdf_path + ".tmp.pdf"
        d.save(tmp, garbage=3, deflate=True)
        d.close()
        os.replace(tmp, pdf_path)

    # =========================================================================== DOCX
    def to_docx(self, path: str, cover: dict, root: str):
        from docx import Document
        from docx.enum.section import WD_SECTION
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, Inches, RGBColor

        D = Document()
        sec = D.sections[0]
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
        for side in ("left_margin", "right_margin"):
            setattr(sec, side, Inches(0.8))
        sec.top_margin, sec.bottom_margin = Inches(0.8), Inches(0.8)
        # page border like the Phase-2 report
        sp = sec._sectPr
        pb = OxmlElement("w:pgBorders")
        pb.set(qn("w:offsetFrom"), "page")
        for side in ("top", "left", "bottom", "right"):
            e = OxmlElement(f"w:{side}")
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), "10")
            e.set(qn("w:space"), "24")
            e.set(qn("w:color"), "000000")
            pb.append(e)
        sp.append(pb)
        # page numbers in footer
        fp = sec.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
            r = fp.add_run()
            if tag:
                f = OxmlElement("w:fldChar")
                f.set(qn("w:fldCharType"), tag)
                r._r.append(f)
            else:
                it = OxmlElement("w:instrText")
                it.text = txt
                r._r.append(it)
        st = D.styles["Normal"]
        st.font.name = "Calibri"
        st.font.size = Pt(11)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
        st.paragraph_format.space_after = Pt(5)
        st.paragraph_format.line_spacing = 1.12

        def run_md(par, text, size=None, color=None, bold_all=False):
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"</?(span|a|u)[^>]*>", "", text)
            toks = re.split(r"(<b>|</b>|<i>|</i>|<code>|</code>|<sub>|</sub>|<sup>|</sup>)", text)
            b = i = c = sub = sup = False
            for t in toks:
                if t in ("<b>", "</b>"):
                    b = t == "<b>"
                    continue
                if t in ("<i>", "</i>"):
                    i = t == "<i>"
                    continue
                if t in ("<code>", "</code>"):
                    c = t == "<code>"
                    continue
                if t in ("<sub>", "</sub>"):
                    sub = t == "<sub>"
                    continue
                if t in ("<sup>", "</sup>"):
                    sup = t == "<sup>"
                    continue
                if not t:
                    continue
                r = par.add_run(html.unescape(re.sub(r"<[^>]+>", "", t)))
                r.bold = b or bold_all
                r.italic = i
                r.font.subscript = sub
                r.font.superscript = sup
                if c:
                    r.font.name = "Consolas"
                    r.font.size = Pt(9)
                if size:
                    r.font.size = Pt(size)
                if color:
                    r.font.color.rgb = RGBColor.from_string(color.strip("#"))

        def shade(cell, hexcol):
            tcPr = cell._tc.get_or_add_tcPr()
            s = OxmlElement("w:shd")
            s.set(qn("w:val"), "clear")
            s.set(qn("w:color"), "auto")
            s.set(qn("w:fill"), hexcol.strip("#"))
            tcPr.append(s)

        # ---- cover
        c = cover
        for t in ("-DEPARTMENT OF SOFTWARE ENGINEERING-", "-FAST NUCES ISLAMABAD-"):
            p = D.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(14)
            run_md(p, f"<b>{t}</b>", size=15)
        p = D.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(18)
        p.add_run().add_picture(os.path.join(root, c["logo_file"]), width=Inches(1.5))
        p = D.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(18)
        run_md(p, "<b>Comp Networks</b>", size=20)
        for t in (c["phase1"], c["phase2"]):
            p = D.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(0)
            run_md(p, f"<b>{t}</b>", size=16)
        p = D.add_paragraph()
        p.paragraph_format.space_before = Pt(30)
        run_md(p, "<b>Submitted to:</b>", size=13)
        p = D.add_paragraph()
        p.paragraph_format.left_indent = Inches(1.45)
        run_md(p, c["to"], size=12)
        p = D.add_paragraph()
        p.paragraph_format.space_before = Pt(12)
        run_md(p, "<b>Submitted by:</b>", size=13)
        for n, r_ in c["students"] + [("<b>Class / Section:</b>", c["section"])]:
            p = D.add_paragraph()
            p.paragraph_format.tab_stops.add_tab_stop(Inches(2.3))
            p.paragraph_format.space_after = Pt(2)
            run_md(p, f"{n}\t{r_}", size=12)
        p = D.add_paragraph()
        p.paragraph_format.space_before = Pt(16)
        run_md(p, f"<b>Date :</b>     <b>{c['date']}</b>", size=12)
        p.add_run().add_break(WD_BREAK.PAGE)

        for b in self.blocks:
            k = b[0]
            if k in ("h1", "h2", "h3"):
                p = D.add_paragraph()
                p.paragraph_format.space_before = Pt(14 if k == "h1" else 9)
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.keep_with_next = True
                if k == "h1":
                    run_md(p, b[1], size=17, color=BLUE)
                else:
                    run_md(p, b[1], size=12.5 if k == "h2" else 11, color=NAVY, bold_all=True)
            elif k == "p":
                p = D.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                run_md(p, b[1])
            elif k == "note":
                p = D.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.2)
                run_md(p, b[1], color="#1F3864")
            elif k in ("ul", "ol"):
                for it in b[1]:
                    p = D.add_paragraph(style="List Bullet" if k == "ul" else "List Number")
                    run_md(p, it)
            elif k == "code":
                p = D.add_paragraph()
                r = p.add_run(b[1])
                r.font.name = "Consolas"
                r.font.size = Pt(7.5)
                if b[2]:
                    cp = D.add_paragraph()
                    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run_md(cp, f"<i>{b[2]}</i>", size=9)
            elif k == "pb":
                D.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            elif k == "table":
                _, header, rows, cap, widths, small = b
                t = D.add_table(rows=1, cols=len(header))
                t.style = "Table Grid"
                t.alignment = WD_TABLE_ALIGNMENT.CENTER
                fs = 8 if small else 9
                for j, h in enumerate(header):
                    cell = t.rows[0].cells[j]
                    shade(cell, NAVY)
                    cell.paragraphs[0].text = ""
                    run_md(cell.paragraphs[0], f"<b>{h}</b>", size=fs, color="#FFFFFF")
                for ri, row in enumerate(rows):
                    cells = t.add_row().cells
                    for j, v in enumerate(row):
                        cells[j].paragraphs[0].text = ""
                        run_md(cells[j].paragraphs[0], str(v), size=fs)
                        if ri % 2 == 1:
                            shade(cells[j], "#F3F6FB")
                if widths:
                    tot = 6.9
                    for row in t.rows:
                        for j, w in enumerate(widths):
                            row.cells[j].width = Inches(tot * w / 100)
                if cap:
                    cp = D.add_paragraph()
                    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run_md(cp, f"<i>{cap}</i>", size=9)
            elif k == "fig":
                _, img, cap, w = b
                p = D.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(os.path.join(root, img.replace("../", "")), width=Inches(6.9 * w / 100))
                cp = D.add_paragraph()
                cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run_md(cp, f"<i>{cap}</i>", size=9)
        D.core_properties.title = cover["title"]
        D.core_properties.author = "Ahmed Tanweer, Salman Ahmad"
        D.save(path)
