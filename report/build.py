"""Build the Phase-3 report (PDF + DOCX) from the results.  Run:  .venv/bin/python report/build.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from report.doc import Doc, ROOT   # noqa: E402
from report import content          # noqa: E402

COVER = {
    "title": "Phase 3 - Research Implementation and Experimental Evaluation",
    "logo": "assets/logo_0.png", "logo_file": "report/assets/logo_0.png",
    "phase1": "Phase 3: Research Implementation", "phase2": "&amp; Experimental Evaluation",
    "to": "Mam Laraib Afzal",
    "students": [("Ahmed Tanweer", "23i-3019"), ("Salman Ahmad", "23i-6029")],
    "section": "AI-C", "date": "14/11/2026",
}

if __name__ == "__main__":
    R = json.load(open(os.path.join(ROOT, "results", "all_results.json")))
    doc = Doc()
    content.build(doc, R)
    out = os.path.join(ROOT, "report")
    html_path = os.path.join(out, "Phase3_report.html")
    open(html_path, "w").write(doc.to_html(COVER))
    name = "23i-3019_23i-6029_Phase3_Implementation_Evaluation"
    doc.to_pdf(html_path, os.path.join(ROOT, f"{name}.pdf"), COVER["title"])
    cov = dict(COVER, phase2="& Experimental Evaluation")
    doc.to_docx(os.path.join(ROOT, f"{name}.docx"), cov, ROOT)
    print("built", name, "figures", doc.fig_no, "tables", doc.tab_no)
