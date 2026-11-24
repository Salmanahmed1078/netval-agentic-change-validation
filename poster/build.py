"""Build the Phase-5 A3 research poster and the 2.5 ft x 5 ft standee from shared blocks.

    .venv/bin/python poster/charts.py      # charts from results/all_results.json
    .venv/bin/python poster/build.py       # HTML -> vector PDF via headless Chrome

Both products use the same stylesheet (style.css), header, palette, typefaces, diagram,
charts and wording, so they read as one set. Every number below comes from the Phase-3
logs (results/all_results.json) and matches the Phase-4 paper.
"""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
REPO = "https://github.com/Salmanahmed1078/netval-agentic-change-validation"

TITLE = ("Intent-Aware Agentic Validation of Network Configuration Changes in CI/CD Pipelines "
         "Using a Two&#8209;Tier Network Digital Twin")
TITLE_A3 = TITLE.replace(" Using", "<br>Using")
TITLE_ST = ("Intent-Aware Agentic Validation of Network<br>Configuration Changes in CI/CD Pipelines<br>"
            "Using a Two&#8209;Tier Network Digital Twin")
AUTHORS = "Ahmed Tanweer &nbsp;·&nbsp; Salman Ahmad"
AFFIL = ("Department of Artificial Intelligence and Data Science, National University of Computer and "
         "Emerging Sciences (FAST-NUCES), Islamabad, Pakistan")
EMAILS = "i233019@isb.nu.edu.pk &nbsp;·&nbsp; i236029@isb.nu.edu.pk"
KICKER = "FAST-NUCES Islamabad &nbsp;·&nbsp; Computer Networks Research Project &nbsp;·&nbsp; 2026"

ARCH = open(os.path.join(HERE, "arch.svg")).read()

HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=block" rel="stylesheet">
<link rel="stylesheet" href="style.css">
<style>{css}</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"></script>
</head><body>"""

QR_JS = """<script>
(function(){
  var q = qrcode(0, 'M'); q.addData('%s'); q.make();
  var n = q.getModuleCount(), m = 2, d = '';
  for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) if (q.isDark(r, c)) d += 'M' + (c + m) + ' ' + (r + m) + 'h1v1h-1z';
  var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + (n + 2*m) + ' ' + (n + 2*m) + '" shape-rendering="crispEdges">'
          + '<rect width="100%%" height="100%%" fill="#fff"/><path d="' + d + '" fill="#0B2547"/></svg>';
  document.querySelectorAll('.qr').forEach(function(e){ e.innerHTML = svg; });
})();
</script></body></html>""" % REPO


def header(title=TITLE):
    return f"""<header class="hdr">
  <div class="logo"><img src="assets/fast_logo.png" alt="FAST-NUCES"></div>
  <div class="ttl">
    <div class="kicker">{KICKER}</div>
    <h1>{title}</h1>
    <div class="authors">{AUTHORS}</div>
    <div class="affil">{AFFIL}<br>{EMAILS}</div>
  </div>
  <div class="qrwrap"><div class="qrbox qr"></div><div class="qrcap">Code, dataset<br>&amp; full paper</div></div>
</header>"""


KPIS = [
    ("97.8%", "of unsafe changes caught", "fixed full suite: 80.0%", False),
    ("2.2%", "unsafe changes approved", "fixed full suite: 20.0%", True),
    ("4.8%", "safe changes wrongly blocked", "fixed full suite: 61.9%", False),
    ("8.6×", "faster CI gate", "2.4 vs 20.5 min · 12 vs 1,559 tests", False),
    ("0", "unsafe approvals with ID-only tickets", "86% sent to a human instead", True),
]


def kpis(items=KPIS):
    out = []
    for big, lab, sub, hl in items:
        out.append(f'<div class="kpi{" hl" if hl else ""}"><div class="big">{big}</div>'
                   f'<div><div class="lab">{lab}</div><div class="sub">{sub}</div></div></div>')
    return '<div class="kpis">' + "".join(out) + "</div>"


def sec(n, title, body, cls=""):
    return f'<section class="sec {cls}"><h2><span class="n">{n:02d}</span>{title}</h2>{body}</section>'


PROBLEM = """<p>Configuration changes are a leading cause of network outages (one maintenance command took down Meta's
backbone in 2021). Configs now ship as <b>CI/CD pull requests</b>, but the gate does not know what a change is <i>meant</i> to do.</p>
<ul class="b">
<li><b>Fixed full suite:</b> every <i>intended</i> change looks like a failure; a change that silently does nothing passes.</li>
<li><b>Rule-based selection:</b> misses effects away from the changed device.</li>
<li><b>Static verifiers:</b> blind to intent and to MTU, timing and load faults.</li>
</ul>"""

GAP = """<ol class="g">
<li>No study compares an intent-aware agent with a fixed CI suite <b>on the same changes</b>.</li>
<li>The <b>false-approval rate</b> of an LLM deployment gate is not reported.</li>
<li>The effect of <b>ticket quality</b> (clear → ID only) is unknown.</li>
<li>Which <b>twin tier</b> (static vs. emulation) catches which fault is not reported.</li>
</ol>
"""

CONTRIB = """<ul class="b">
<li><b>NetVal gate:</b> twin finds the blast radius, LLM labels it, rules decide.</li>
<li><b>Two-tier twin:</b> static + emulation (MTU, failover, load).</li>
<li><b>Benchmark:</b> 120 changes × 3 tickets, oracle-labelled.</li>
<li><b>Paired evaluation:</b> 4 baselines, 6 ablations, significance tests.</li>
</ul>"""

DATA = """<div class="chips">
<div class="chip"><div class="k">120</div><div class="t">changes (62 unsafe, 58 safe)</div></div>
<div class="chip"><div class="k">3</div><div class="t">topologies: campus, DC, ISP</div></div>
<div class="chip"><div class="k">8</div><div class="t">change classes</div></div>
<div class="chip"><div class="k">28</div><div class="t">fault families</div></div>
<div class="chip"><div class="k">×3</div><div class="t">ticket versions each</div></div>
<div class="chip"><div class="k">87</div><div class="t">held-out test changes</div></div>
</div>
<div class="callout" style="margin-top:calc(5*var(--u))"><div class="small"><b>Same change, three tickets</b><br>
<b>clear:</b> “On leaf3 permit tcp/5432; only stage-a1 → db-a1 opens.”<br>
<b>vague:</b> “Let the staging server talk to the database.”<br>
<b>terse:</b> “fix ticket #8768”</div></div>
"""

METHOD_IDEAS = """<ul class="b">
<li><b>The twin finds what changed:</b> every flow, route, adjacency, path and load shift.</li>
<li><b>The LLM only labels</b> each difference <i>intended</i>, <i>unintended</i> or <i>unsure</i>; labels compile into typed tests, never free code.</li>
<li><b>Code decides:</b> missing intent becomes <span class="v-esc">ESCALATE</span>, never a guess.</li>
</ul>"""

DSL = """<div class="dsl"><span>reach</span><span>path</span><span>route</span><span>acl</span><span>adj</span><span>like</span>
<span class="t2">mtu</span><span class="t2">conv</span><span class="t2">load</span><span>inv ×4</span></div>
<p class="note">Typed test language. Blue: static Tier 1 (or both tiers); green: needs Tier-2 emulation.</p>"""

EXAMPLE = """<div class="trace">
<div class="step"><div class="who">Ticket</div><div>“open <b>tcp/5432</b> from stage-a1 to db-a1 only”</div></div>
<div class="step"><div class="who">Faulty diff</div><div><span class="mono">25 permit ip 10.101.2.0/24 any</span></div></div>
<div class="step"><div class="who">Twin</div><div>7 services now open stage-a1 → db-a1</div></div>
<div class="step"><div class="who">LLM labels</div><div>tcp/5432 <i>intended</i>; icmp, ssh, http, https, smtp, dns <i>unintended</i></div></div>
<div class="step"><div class="who">Verdict</div><div><span class="v-block">BLOCK</span> in 12 tests (51 s); the correct diff is approved.</div></div>
</div>"""

RESULTS_TABLE = """<table class="t">
<tr><th>Method</th><th>Detect.</th><th>False appr.</th><th>False block</th><th>Auto-appr.</th><th>Tests</th><th>Min</th></tr>
<tr><td>B1 Full suite</td><td>80.0</td><td>20.0</td><td>61.9</td><td>38.1</td><td>1,559</td><td>20.5</td></tr>
<tr><td>B2 Rule-based</td><td>60.0</td><td>40.0</td><td>54.8</td><td>45.2</td><td>120</td><td>1.8</td></tr>
<tr><td>B4 Static only</td><td>60.0</td><td>40.0</td><td>0.0</td><td>38.1</td><td>6</td><td>0.8</td></tr>
<tr><td>B3 One-shot LLM</td><td>97.8</td><td>2.2</td><td>50.0</td><td>50.0</td><td>28</td><td>3.7</td></tr>
<tr class="hl"><td>NetVal</td><td>97.8</td><td>2.2</td><td>4.8</td><td>92.9</td><td>12</td><td>2.4</td></tr>
<tr class="sub"><td>NetVal, 3-run mean</td><td>96.3±1.3</td><td>3.7±1.3</td><td>3.2±1.4</td><td>95.2±2.4</td><td>12</td><td>2.4</td></tr>
</table>
<p class="note">Rates in %, clear tickets, 87 unseen data-centre and ISP changes (prompts tuned on the campus network only).
vs. B1: McNemar (Holm) p ≤ 0.001 for detection and false blocks. B4 never blocks a safe change but escalates 60% of all changes.</p>"""

TICKETS_TEXT = """<p class="small">Vague tickets raise false approvals to 8.9%. With an <b>ID-only</b> ticket NetVal approves
<b>nothing</b> and escalates 86%; the one-shot LLM cannot abstain and lets <b>22%</b> of unsafe changes through.</p>"""

ABLATION = """<table class="t">
<tr><th>Variant</th><th>False appr.</th><th>False block</th><th>Auto-appr.</th><th>Min</th></tr>
<tr class="hl"><td>Full NetVal</td><td>2.2</td><td>4.8</td><td>92.9</td><td>2.4</td></tr>
<tr><td>− impact analyzer</td><td class="dn">4.4</td><td class="dn">47.6</td><td class="dn">47.6</td><td>6.0</td></tr>
<tr><td>− invariant guard</td><td class="dn">4.4</td><td class="up">2.4</td><td>95.2</td><td>2.3</td></tr>
<tr><td>Tier 1 only (static)</td><td>2.2</td><td>7.1</td><td class="dn">57.1</td><td class="up">0.9</td></tr>
<tr><td>Tier 2 only (emulation)</td><td>2.2</td><td>4.8</td><td>92.9</td><td class="dn">4.8</td></tr>
<tr><td>− feedback loop</td><td>2.2</td><td>4.8</td><td>92.9</td><td>2.4</td></tr>
<tr><td>Local 7B model†</td><td class="dn">31.2</td><td class="dn">25.0</td><td class="dn">68.8</td><td>4.3</td></tr>
</table>
<p class="note">The <b>impact analyzer</b> matters most. Without Tier 2, missing evidence turns into escalations, not risk.
The replan loop never fired with the hosted model. † Qwen2.5-7B on a 32-change subset.</p>"""

BASE = """<table class="t">
<tr><th>Base paper / method</th><th>Detect.</th><th>False appr.</th><th>Time</th></tr>
<tr><td>Aether (2026)*</td><td>94</td><td>n/r</td><td>6–7 min</td></tr>
<tr><td>CORNETTO (2026)*</td><td>n/a</td><td>&lt;6 – 34.2</td><td>n/r</td></tr>
<tr><td>One-shot LLM†</td><td>97.8</td><td>2.2 / 22.2‡</td><td>3.7 min</td></tr>
<tr><td>Verifier-only gate†</td><td>60.0</td><td>40.0</td><td>0.8 min</td></tr>
<tr class="hl"><td>NetVal</td><td>97.8</td><td>2.2 / 0.0‡</td><td>2.4 min</td></tr>
</table>
<p class="note">* as reported, on the paper's own data (CORNETTO: regression rate). † re-implemented on our data.
‡ clear / ID-only tickets.</p>"""

CONCL = """<p>A twin that computes <b>what changed</b> plus an LLM that judges only <b>whether the ticket explains it</b>
gives a gate that beats running every test, is 8.6× faster, costs ~<b>US$0.015/PR</b>, is stable (κ = 0.92) and
<b>fails safe</b> without intent.</p>"""

LIMITS = """<ul class="b small">
<li><b>Limits &amp; next:</b> own Python twin, synthetic changes, modelled CI time; next, real Batfish + containerlab and real tickets.</li>
</ul>"""


def fig(name, cap=""):
    c = f"<figcaption>{cap}</figcaption>" if cap else ""
    return f'<figure><img src="assets/{name}.svg">{c}</figure>'


# --------------------------------------------------------------------------- A3 poster
A3_CSS = """
@page { size: 420mm 297mm; margin: 0; }
.page { width: 420mm; height: 297mm; }
.hdr { padding: 5mm 10mm 6mm; gap: 7mm; }
.hdr .logo { width: 24mm; height: 24mm; }
.hdr .ttl { flex: 1; }
.hdr h1 { font-size: 21pt; margin: 1.2mm 0 1.4mm; }
.hdr .authors { font-size: 12pt; }
.hdr .affil { font-size: 8.4pt; margin-top: .6mm; line-height: 1.35; }
.hdr .qrbox { width: 23mm; height: 23mm; padding: 1.2mm; }
.kpis { grid-template-columns: repeat(5, 1fr); margin: 4mm 10mm 0; }
.body { flex: 1; display: grid; grid-template-columns: 0.94fr 1fr 1fr 0.94fr; grid-template-rows: auto 1fr;
        gap: 3.6mm 6.5mm; padding: 4mm 10mm 0; min-height: 0; }
.col { display: flex; flex-direction: column; gap: 3.6mm; min-height: 0; }
.c1 { grid-column: 1; grid-row: 1 / span 2; } .cm { grid-column: 2 / span 2; grid-row: 1; }
.c2 { grid-column: 2; grid-row: 2; } .c3 { grid-column: 3; grid-row: 2; } .c4 { grid-column: 4; grid-row: 1 / span 2; }
.ideas3 ul.b { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 5mm; margin-top: 3mm; }
.ideas3 ul.b li { margin: 0; font-size: 8.2pt; line-height: 1.3; }
.ftr { margin: 0 10mm; padding: 2.4mm 0 3.6mm; font-size: 7.4pt; }
"""


def a3():
    col1 = (sec(1, "The problem", PROBLEM) + sec(2, "Research gap", GAP) + sec(3, "Contributions", CONTRIB)
            + sec(4, "Dataset &amp; setup", DATA))
    method = sec(5, "Method: the NetVal gate", f'<figure class="arch">{ARCH}</figure>'
                 f'<div class="ideas3">{METHOD_IDEAS}</div>')
    col2 = sec(6, "Main results", RESULTS_TABLE) + sec(7, "vs. base papers", BASE)
    col3 = sec(8, "Ablation study", ABLATION) + sec(9, "Worked example: over-broad ACL", EXAMPLE)
    col4 = (sec(10, "Safer and more automated", fig("chart_tradeoff", "Clear tickets, 87 unseen changes. Only NetVal reaches "
                                                     "the top-left corner: low false approvals <i>and</i> high automation."))
            + sec(11, "Ticket quality", fig("chart_tickets") + TICKETS_TEXT)
            + sec(12, "Conclusion", CONCL + f'<div style="margin-top:2mm">{LIMITS}</div>'))
    body = (f'<div class="page">{header(TITLE_A3)}{kpis()}<main class="body">'
            f'<div class="col c1">{col1}</div><div class="col cm">{method}</div>'
            f'<div class="col c2">{col2}</div><div class="col c3">{col3}</div><div class="col c4">{col4}</div></main>'
            f'<footer class="ftr"><span>Phase 5 · Research poster · Computer Networks, FAST-NUCES Islamabad · Supervisor: Ms. Laraib Afzal</span>'
            f'<span>{REPO.replace("https://", "")}</span></footer></div>')
    return HEAD.format(title="NetVal A3 poster", css=A3_CSS) + body + QR_JS


# --------------------------------------------------------------------------- standee 30 x 60 in
ST_CSS = """
@page { size: 762mm 1524mm; margin: 0; }
:root { --u: 3.6pt; }
.page { width: 762mm; height: 1524mm; }
.hdr { padding: 20mm 34mm 22mm; gap: 14mm; }
.hdr .logo { width: 84mm; height: 84mm; }
.hdr .ttl { flex: 1; }
.hdr .kicker { font-size: 24pt; }
.hdr h1 { font-size: 68pt; margin: 4mm 0 6mm; line-height: 1.08; }
.hdr .authors { font-size: 36pt; }
.hdr .affil { font-size: 22pt; margin-top: 3mm; line-height: 1.35; }
.hdr .qrwrap { display: none; }
.wrap { flex: 1; display: flex; flex-direction: column; gap: 11mm; padding: 13mm 34mm 0; min-height: 0; }
.kpis { grid-template-columns: repeat(2, 1fr); gap: 8mm; }
.kpi { padding: 8mm 11mm; gap: 10mm; }
.kpi .big { font-size: 96pt; }
.kpi .lab { font-size: 36pt; }
.kpi .sub { font-size: 27pt; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 16mm; }
.sec > h2 { font-size: 42pt; }
.sec { font-size: 32pt; }
.sec p, .sec li { line-height: 1.32; }
figcaption { font-size: 25pt; color: var(--ink); }
.big-ideas { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10mm; }
.big-ideas li { font-size: 30pt; margin: 0; }
.sec table.t { font-size: 27pt; }
.sec table.t th, .sec table.t td { padding: 2.6mm 4mm; }
.ftr2 { background: var(--navy-deep); color: #fff; display: flex; align-items: center; gap: 16mm; padding: 14mm 34mm; margin-top: 13mm; position: relative; }
.ftr2::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 10pt; background: var(--accent); }
.ftr2 .qrbox { width: 88mm; height: 88mm; padding: 4mm; background: #fff; border-radius: 6mm; flex: none; }
.ftr2 .qrbox svg { width: 100%; height: 100%; display: block; }
.ftr2 .t1 { font-size: 36pt; font-weight: 700; }
.ftr2 .t2 { font-size: 24pt; color: #C9D6E8; margin-top: 3mm; line-height: 1.35; }
"""

ST_KPIS = [
    ("97.8%", "of unsafe changes caught", "fixed full suite: 80.0%", False),
    ("2.2%", "unsafe changes approved", "fixed full suite: 20.0%", True),
    ("8.6×", "faster, 128× fewer tests", "2.4 vs 20.5 min per change", False),
    ("0", "unsafe approvals on ID-only tickets", "86% sent to a human instead", True),
]

ST_PROBLEM = """<p>Configuration changes are a leading cause of network outages. Network CI/CD gates either run
<b>every test</b> (slow, and they block 62% of correct changes) or a few <b>fixed rules</b> (they miss side effects).
Neither knows what a change is <i>meant</i> to do.</p>"""

ST_GAP = """<ol class="g">
<li>No comparison with a fixed CI suite on the <b>same changes</b>.</li>
<li><b>False approvals</b> of an LLM gate are not measured.</li>
<li>Effect of <b>ticket quality</b> is unknown.</li>
</ol>"""

ST_IDEAS = """<ul class="b big-ideas">
<li><b>The twin finds what changed:</b> every flow, route, adjacency and load shift.</li>
<li><b>The LLM only labels</b> each difference: intended, unintended or unsure.</li>
<li><b>Plain code decides;</b> missing intent means <span class="v-esc">ESCALATE</span>, never a guess.</li>
</ul>"""

ST_RESULTS = """NetVal (orange) is the only method that is both safe and automated: it catches 97.8% of unsafe
changes and auto-approves 92.9% of correct ones; the fixed suite blocks 61.9% of correct changes."""
ST_TICKETS = """Vague tickets cost some accuracy. With an ID-only ticket NetVal approves nothing and asks a human;
the one-shot LLM lets 22% of unsafe changes through."""

ST_CONCL = """<p>A digital twin that computes <b>what changed</b>, plus an LLM that judges only <b>whether the ticket
explains it</b>, gives a CI gate that is safer and 8.6× faster than running every test, costs about
<b>US$0.015 per PR</b>, gives the same verdict across runs (κ = 0.92) and <b>fails safe</b> when intent is missing.</p>"""


ST_TABLE = RESULTS_TABLE.split('<p class="note">')[0]


def standee():
    body = f"""<div class="page">{header(TITLE_ST)}
<div class="wrap">
{kpis(ST_KPIS)}
<div class="two">{sec(1, "The problem", ST_PROBLEM)}{sec(2, "Research gap", ST_GAP)}</div>
{sec(3, "Method: the NetVal gate", f'<figure class="arch">{ARCH}</figure><div style="margin-top:8mm">{ST_IDEAS}</div>')}
<div class="two">
{sec(4, "Safer and more automated", f'<figure><img src="assets/chart_tradeoff.svg"><figcaption>{ST_RESULTS}</figcaption></figure>')}
{sec(5, "Ticket quality", f'<figure><img src="assets/chart_tickets.svg"><figcaption>{ST_TICKETS}</figcaption></figure>')}
</div>
{sec(6, "Main results: all methods, same 87 changes", ST_TABLE)}
{sec(7, "Conclusion", '<div class="two">' + ST_CONCL + '<div class="callout"><b>Limits:</b> our own Python twin, synthetic changes and modelled CI time. <b>Next:</b> real Batfish + containerlab, larger local models and real change tickets.</div></div>')}
</div>
<div class="ftr2"><div class="qrbox qr"></div><div><div class="t1">Scan for the code, the 120-change dataset and the full paper</div>
<div class="t2">{REPO.replace("https://", "")}<br>{EMAILS} &nbsp;·&nbsp; Supervisor: Ms. Laraib Afzal</div></div></div>
</div>"""
    return HEAD.format(title="NetVal standee", css=ST_CSS) + body + QR_JS


def render(html_name, pdf_name, png_name, w_mm, h_mm):
    html = os.path.join(HERE, html_name)
    pdf = os.path.join(HERE, pdf_name)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                    "--virtual-time-budget=20000", f"--print-to-pdf={pdf}", "file://" + html],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import pymupdf
    d = pymupdf.open(pdf)
    zoom = 2600 / max(d[0].rect.width, d[0].rect.height)
    d[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(os.path.join(HERE, png_name))
    print(pdf_name, "pages:", d.page_count, "size (mm): %.0f x %.0f" % (d[0].rect.width / 72 * 25.4, d[0].rect.height / 72 * 25.4))


if __name__ == "__main__":
    open(os.path.join(HERE, "poster_a3.html"), "w").write(a3())
    open(os.path.join(HERE, "standee.html"), "w").write(standee())
    render("poster_a3.html", "NetVal_Poster_A3.pdf", "preview_poster_a3.png", 420, 297)
    render("standee.html", "NetVal_Standee_2.5x5ft.pdf", "preview_standee.png", 762, 1524)
