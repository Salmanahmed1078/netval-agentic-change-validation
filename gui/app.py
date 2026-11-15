"""NetVal Studio - web GUI for the agentic change-validation prototype.

    .venv/bin/python gui/app.py      ->  http://127.0.0.1:5050
"""
from __future__ import annotations

import html
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, jsonify, request, send_from_directory, Response   # noqa: E402

from netval.experiment import Data, load as load_results                      # noqa: E402
from netval.agent import AgenticValidator, AgentConfig, render_impact         # noqa: E402
from netval.baselines import FullSuite, RuleBased, BatfishOnly                # noqa: E402
from netval.impact import structured_diff, unified_diff                       # noqa: E402
from netval.llm import get_llm                                                # noqa: E402
from netval.cases import CLASS_LABEL                                          # noqa: E402
from netval.topologies import NSF                                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__)
DATA = Data()

CSS = """
:root{--bg:#f4f6fa;--card:#fff;--ink:#1c2433;--mute:#667085;--line:#e3e7ef;--brand:#1f4e8c;--brand2:#2f6fd0;
--ok:#1a7f4b;--okbg:#e6f4ec;--bad:#b42318;--badbg:#fdecea;--warn:#a15c00;--warnbg:#fff4e0;--code:#0f172a}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Inter,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(90deg,#14325c,#1f4e8c);color:#fff;padding:14px 28px;display:flex;align-items:center;gap:28px}
header .logo{font-weight:700;font-size:18px;letter-spacing:.3px}header .logo small{font-weight:400;opacity:.75;margin-left:8px;font-size:12px}
header nav a{color:#dbe6f7;text-decoration:none;margin-right:18px;font-weight:500}header nav a.on,header nav a:hover{color:#fff}
main{padding:22px 28px;max-width:1500px;margin:auto}
.grid{display:grid;gap:16px}.grid>div{min-width:0}.g4{grid-template-columns:repeat(4,1fr)}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.card h3{margin:0 0 10px;font-size:14px;color:var(--mute);text-transform:uppercase;letter-spacing:.6px;font-weight:600}
.kpi{font-size:30px;font-weight:700}.kpi small{font-size:13px;color:var(--mute);font-weight:500;margin-left:6px}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--mute);font-weight:600;border-bottom:1px solid var(--line);padding:7px 8px;background:#fafbfd}
td{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}tr:hover td{background:#fafcff}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600}
.safe,.approve,.pass{background:var(--okbg);color:var(--ok)}.unsafe,.block,.fail{background:var(--badbg);color:var(--bad)}
.escalate,.error,.skipped{background:var(--warnbg);color:var(--warn)}
pre,code{font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
pre.diff{background:var(--code);color:#cbd5e1;padding:12px;border-radius:8px;max-height:330px;overflow:auto;margin:0;white-space:pre-wrap;word-break:break-word}
.add{color:#86efac}.del{color:#fca5a5}.hunk{color:#7dd3fc}
.tabs button{border:1px solid var(--line);background:#fff;padding:5px 12px;border-radius:8px;margin-right:6px;cursor:pointer;font-weight:600;color:var(--mute)}
.tabs button.on{background:var(--brand);color:#fff;border-color:var(--brand)}
.ticket{background:#f8fafc;border:1px solid var(--line);border-radius:8px;padding:10px 12px;min-height:64px}
.btn{background:var(--brand2);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-weight:600;cursor:pointer}
.btn:disabled{opacity:.5}select{padding:7px;border-radius:8px;border:1px solid var(--line)}
.steps{display:flex;gap:8px;margin:6px 0 12px}.step{flex:1;border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:#fbfcfe;font-size:12px}
.step b{display:block;font-size:12.5px}.step.done{border-color:#9ec5fe;background:#eef5ff}.step.cur{border-color:var(--brand2);box-shadow:0 0 0 2px #cfe0ff}
.verdict{font-size:22px;font-weight:800;padding:10px 16px;border-radius:10px;display:inline-block}
.muted{color:var(--mute)}a{color:var(--brand2)}.small{font-size:12px}
.fig{width:100%;border:1px solid var(--line);border-radius:10px;background:#fff}
"""


def page(title, body, active=""):
    nav = "".join(f'<a href="{u}" class="{"on" if active == k else ""}">{k}</a>' for k, u in
                  [("Dashboard", "/"), ("Change cases", "/cases"), ("Results", "/results"), ("CI gate", "/ci")])
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{title} - NetVal Studio</title><style>{CSS}</style></head>"
            f"<body><header><div class=logo>NetVal Studio<small>Agentic network change validation · digital twin</small></div>"
            f"<nav>{nav}</nav></header><main>{body}</main></body></html>")


def pill(x):
    return f'<span class="pill {x}">{x}</span>'


# ------------------------------------------------------------------------------- topology svg
POS = {
    "campus": {"inet": (430, 30), "edge1": (430, 110), "core1": (300, 180), "core2": (560, 180), "srv1": (430, 260),
               "dist1": (220, 300), "dist2": (640, 300), "acc1": (120, 390), "acc2": (300, 390), "acc3": (640, 390)},
    "datacenter": {"wan": (430, 25), "border1": (330, 95), "border2": (530, 95), "spine1": (300, 185), "spine2": (560, 185),
                   "oob": (430, 185), **{f"leaf{k}": (95 + (k - 1) * 135, 300) for k in range(1, 7)}},
    "isp": {"SEA": (70, 70), "PAO": (60, 210), "SDG": (130, 330), "SLC": (220, 160), "BOU": (330, 200), "HOU": (420, 360),
            "LNK": (430, 140), "CHM": (560, 120), "PIT": (700, 150), "ATL": (640, 330), "AAR": (600, 60), "ITH": (760, 70),
            "PRN": (800, 220), "CPK": (780, 300), "cust1": (40, 20), "cust2": (620, 410), "cust3": (850, 270),
            "peer1": (750, 15), "peer2": (25, 280), "noc-sw": (250, 300)},
}


def topo_svg(c, highlight=()):
    s = DATA.bases[c["topology"]]
    pos = dict(POS[c["topology"]])
    hosts_by_dev = {}
    for h in s.hosts.values():
        hosts_by_dev.setdefault(h.attach[0], []).append(h.name)
    out = ['<svg viewBox="0 0 900 470" width="100%" style="background:#fbfcfe;border-radius:10px;border:1px solid #e3e7ef">']
    for a, ia, b, ib in s.links:
        if a in pos and b in pos:
            bw = s.devices[a].interfaces[ia].bandwidth
            w = 1 + min(4, bw / 25000)
            hl = a in highlight and b in highlight
            out.append(f'<line x1="{pos[a][0]}" y1="{pos[a][1]}" x2="{pos[b][0]}" y2="{pos[b][1]}" stroke="{"#d92d20" if hl else "#98a2b3"}" stroke-width="{w + (2 if hl else 0)}"/>')
    for d, hs in hosts_by_dev.items():
        if d not in pos:
            continue
        x, y = pos[d]
        for k, hn in enumerate(hs):
            hx, hy = x - 40 + k * 44 - (len(hs) - 2) * 10, y + 52
            col = "#d92d20" if hn in highlight else "#475467"
            out.append(f'<line x1="{x}" y1="{y}" x2="{hx}" y2="{hy}" stroke="#d0d5dd" stroke-dasharray="3 2"/>')
            out.append(f'<rect x="{hx-20}" y="{hy-8}" width="40" height="16" rx="4" fill="#fff" stroke="{col}"/>'
                       f'<text x="{hx}" y="{hy+4}" font-size="8" text-anchor="middle" fill="{col}">{html.escape(hn[:9])}</text>')
    for d, (x, y) in pos.items():
        if d not in s.devices:
            continue
        kind = s.devices[d].kind
        ext = d in s.policy["invariants"]["no_leak"]["externals"]
        fill = "#d92d20" if d in highlight else ("#7a5af8" if ext else ("#0e9384" if kind == "switch" else "#1f4e8c"))
        if kind == "switch":
            out.append(f'<rect x="{x-24}" y="{y-13}" width="48" height="26" rx="5" fill="{fill}"/>')
        else:
            out.append(f'<circle cx="{x}" cy="{y}" r="19" fill="{fill}"/>')
        out.append(f'<text x="{x}" y="{y+4}" font-size="9.5" font-weight="700" text-anchor="middle" fill="#fff">{html.escape(d[:7])}</text>')
    out.append('<g font-size="10" fill="#475467"><circle cx="20" cy="452" r="6" fill="#1f4e8c"/><text x="30" y="456">router</text>'
               '<rect x="80" y="446" width="14" height="12" rx="3" fill="#0e9384"/><text x="100" y="456">L2 switch</text>'
               '<circle cx="170" cy="452" r="6" fill="#7a5af8"/><text x="180" y="456">external AS</text>'
               '<circle cx="260" cy="452" r="6" fill="#d92d20"/><text x="270" y="456">changed by this PR</text></g></svg>')
    return "".join(out)


def diff_html(d):
    out = []
    for l in d.splitlines():
        cls = "add" if l.startswith("+") and not l.startswith("+++") else "del" if l.startswith("-") and not l.startswith("---") \
            else "hunk" if l.startswith("@@") else ""
        out.append(f'<span class="{cls}">{html.escape(l)}</span>')
    return "\n".join(out)


# ------------------------------------------------------------------------------- pages
@app.route("/")
def home():
    cs = DATA.cases
    n_uns = sum(c["label"] == "unsafe" for c in cs)
    tops = {t: sum(c["topology"] == t for c in cs) for t in DATA.bases}
    cls_rows = "".join(f"<tr><td>{CLASS_LABEL[k]}</td><td>{sum(c['cls']==k and c['label']=='safe' for c in cs)}</td>"
                       f"<td>{sum(c['cls']==k and c['label']=='unsafe' for c in cs)}</td></tr>" for k in CLASS_LABEL)
    figs = [f for f in ["fig_main_rates.png", "fig_time_vs_detection.png", "fig_intent_quality.png"]
            if os.path.exists(os.path.join(ROOT, "figures", f))]
    figs_html = "".join(f'<div class=card><img class=fig src="/figures/{f}"></div>' for f in figs)
    body = f"""
<div class="grid g4">
 <div class=card><h3>Change cases</h3><div class=kpi>{len(cs)}<small>8 classes</small></div></div>
 <div class=card><h3>Unsafe / safe</h3><div class=kpi>{n_uns} / {len(cs)-n_uns}<small>oracle labels</small></div></div>
 <div class=card><h3>Topologies</h3><div class=kpi>3<small>{', '.join(f'{k} {v}' for k,v in tops.items())}</small></div></div>
 <div class=card><h3>Ticket versions</h3><div class=kpi>{len(cs)*3}<small>clear · vague · terse</small></div></div>
</div><br>
<div class="grid g2">
 <div class=card><h3>Pipeline</h3>
 <div class=steps><div class="step done"><b>1 Intent analyzer</b>LLM reads the ticket</div><div class="step done"><b>2 Impact analyzer</b>Tier-1 differential</div>
 <div class="step done"><b>3 Test planner</b>LLM labels + typed tests</div><div class="step done"><b>4 Executor</b>Tier 1 static · Tier 2 emulation</div>
 <div class="step done"><b>5 Evidence judge</b>deterministic gate</div></div>
 <p class=muted>The LLM never decides alone: the judge blocks on any failed test or invariant, approves only with full coverage and no
 tool error, and otherwise escalates to a human (max 3 replans).</p></div>
 <div class=card><h3>Dataset by change class</h3><table><tr><th>Class</th><th>safe</th><th>unsafe</th></tr>{cls_rows}</table></div>
</div><br><div class="grid g3">{figs_html}</div>"""
    return page("Dashboard", body, "Dashboard")


@app.route("/cases")
def cases():
    rows = "".join(f"<tr><td><a href='/case/{c['id']}'>{c['id']}</a></td><td>{c['topology']}</td><td>{CLASS_LABEL[c['cls']]}</td>"
                   f"<td>{c['variant']}</td><td>{pill(c['label'])}</td><td class=small>{html.escape(c['tickets']['vague'])}</td></tr>"
                   for c in DATA.cases)
    return page("Cases", f"<div class=card><h3>{len(DATA.cases)} labelled change cases</h3><table><tr><th>ID</th><th>Topology</th>"
                         f"<th>Class</th><th>Variant</th><th>Oracle</th><th>Vague ticket</th></tr>{rows}</table></div>", "Change cases")


@app.route("/case/<cid>")
def case(cid):
    c = DATA.by_id[cid]
    pre_t, post_t = DATA.twins(c)
    sd = structured_diff(DATA.bases[c["topology"]], post_t.s)
    hl = [x["device"].replace("host:", "") for x in sd]
    tickets = json.dumps(c["tickets"])
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in c["oracle_reasons"][:6]) or "<li>no violation: intent achieved, nothing else changed</li>"
    body = f"""
<div class="grid g2">
 <div class=card><h3>Change case {cid} · {c['topology']} · {CLASS_LABEL[c['cls']]}</h3>
  <div class=tabs id=tabs><button class=on data-k=clear>clear</button><button data-k=vague>vague</button><button data-k=terse>terse</button></div><br>
  <div class=ticket id=ticket>{html.escape(c['tickets']['clear'])}</div><br>
  <h3>Pull-request diff</h3><pre class=diff>{diff_html(c['diff'])}</pre>
  <p class=small>Ground truth (exhaustive oracle): {pill(c['label'])} <span class=muted>variant: {c['variant']}</span></p><ul class=small>{reasons}</ul>
 </div>
 <div class=card><h3>Network digital twin</h3>{topo_svg(c, hl)}
  <br><br><div style="display:flex;gap:10px;align-items:center">
   <select id=model><option value=local>Local LLM (Qwen2.5-7B, Ollama)</option><option value=hosted>Hosted LLM (Claude Sonnet 5.5)</option></select>
   <button class=btn id=run>Validate this pull request</button><span id=status class=muted></span></div>
 </div>
</div><br>
<div class=card id=outcard style="display:none"><h3>Agent run</h3>
 <div class=steps id=steps></div><div id=out></div></div>
<script>
const T={tickets};let K='clear';
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('on'));b.classList.add('on');K=b.dataset.k;document.getElementById('ticket').textContent=T[K]}});
const esc=s=>String(s).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
document.getElementById('run').onclick=async()=>{{
 const b=document.getElementById('run');b.disabled=true;document.getElementById('status').textContent='running intent analyzer, impact analysis, planner, twin tests...';
 const r=await fetch('/api/validate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{case:'{cid}',intent:K,model:document.getElementById('model').value}})}});
 const d=await r.json();b.disabled=false;document.getElementById('status').textContent='done in '+d.wall+' s';
 document.getElementById('outcard').style.display='block';
 const names=['1 Intent analyzer','2 Impact analyzer','3 Test planner','4 Executor (twin)','5 Evidence judge'];
 document.getElementById('steps').innerHTML=names.map(n=>'<div class="step done"><b>'+n+'</b></div>').join('');
 let h='<div class="grid g2"><div><h3>Intent analysis (LLM)</h3><pre class=diff>'+esc(JSON.stringify(d.intent,null,1))+'</pre>';
 h+='<h3 style="margin-top:12px">Impact entries (Tier-1 differential)</h3><pre class=diff>'+esc(d.impact)+'</pre>';
 h+='<h3 style="margin-top:12px">Planner labels (LLM)</h3><pre class=diff>'+esc(Object.keys(d.labels).sort((a,b)=>parseInt(a.slice(1))-parseInt(b.slice(1))).map(k=>k+': '+d.labels[k]).join('   '))+'</pre></div><div>';
 h+='<h3>Verdict</h3><span class="verdict '+d.verdict+'">'+d.verdict.toUpperCase()+'</span><p>'+esc(d.reason)+'</p>';
 h+='<p class=small>tests '+d.n_tests+' · coverage '+Math.round(d.coverage*100)+'% · replans '+d.replans+' · LLM '+d.llm_s+' s · modelled CI time '+d.ci_s+' s · tokens '+d.tokens+'</p>';
 h+='<h3>Baselines on the same change</h3><table><tr><th>Method</th><th>Verdict</th><th>Tests</th><th>CI time</th></tr>'+d.baselines.map(x=>'<tr><td>'+x.name+'</td><td><span class="pill '+x.verdict+'">'+x.verdict+'</span></td><td>'+x.tests+'</td><td>'+x.ci+' s</td></tr>').join('')+'</table>';
 h+='<p class=small>Oracle label: <span class="pill {c['label']}">{c['label']}</span></p></div></div>';
 h+='<h3 style="margin-top:14px">Executed test plan</h3><table><tr><th>#</th><th>Test (typed DSL)</th><th>Tier</th><th>Status</th><th>Evidence</th></tr>'+
   d.results.map((x,i)=>'<tr><td>'+(i+1)+'</td><td><code>'+esc(JSON.stringify(x.test))+'</code></td><td>'+(x.tier||'-')+'</td><td><span class="pill '+x.status+'">'+x.status+'</span></td><td class=small>'+esc(x.detail)+'</td></tr>').join('')+'</table>';
 document.getElementById('out').innerHTML=h;}};
const q=new URLSearchParams(location.search);
if(q.get('intent')){{document.querySelector('#tabs button[data-k='+q.get('intent')+']').click();}}
if(q.get('model')){{document.getElementById('model').value=q.get('model');}}
if(q.get('autorun')){{document.getElementById('run').click();}}
</script>"""
    return page(cid, body, "Change cases")


@app.post("/api/validate")
def api_validate():
    q = request.get_json()
    c = DATA.by_id[q["case"]]
    pre_t, post_t = DATA.twins(c)
    pre = DATA.bases[c["topology"]]
    t0 = time.time()
    v = AgenticValidator(get_llm(q.get("model", "local"))).validate(pre, post_t.s, c["tickets"][q["intent"]], seed=1,
                                                                    tpre=pre_t, tpost=post_t)
    intent = next((t["response"] for t in v.trace if t["step"] == "intent_analyzer"), {})
    imp = next((t["impact"] for t in v.trace if t["step"] == "impact_analyzer"), "")
    labels = [t["response"].get("labels") for t in v.trace if t["step"].startswith("test_planner") and isinstance(t["response"], dict)]
    base = []
    for M in (FullSuite(), RuleBased(), BatfishOnly()):
        b = M.validate(pre, post_t.s, tpre=pre_t, tpost=post_t)
        base.append({"name": M.name, "verdict": b.verdict, "tests": len(b.tests), "ci": round(b.tool_seconds)})
    return jsonify({"verdict": v.verdict, "reason": v.reason, "n_tests": len(v.tests), "coverage": v.coverage,
                    "replans": v.replans, "llm_s": round(v.llm_seconds, 1), "ci_s": round(v.ci_seconds), "tokens": v.tokens_in + v.tokens_out,
                    "intent": intent, "impact": imp, "labels": labels[-1] if labels else {},
                    "results": [{"test": r["test"], "tier": r.get("tier"), "status": r["status"], "detail": str(r.get("detail"))[:140]}
                                for r in v.results],
                    "baselines": base, "wall": round(time.time() - t0, 1)})


@app.route("/results")
def results():
    figs = sorted(f for f in os.listdir(os.path.join(ROOT, "figures")) if f.endswith(".png")) if os.path.isdir(os.path.join(ROOT, "figures")) else []
    tbl = ""
    p = os.path.join(ROOT, "results", "summary_table.html")
    if os.path.exists(p):
        tbl = open(p).read()
    body = f"<div class=card><h3>Headline results (test split, clear tickets)</h3>{tbl or '<p class=muted>run analyze.py first</p>'}</div><br>" + \
           "<div class='grid g2'>" + "".join(f"<div class=card><h3>{f[4:-4].replace('_',' ')}</h3><img class=fig src='/figures/{f}'></div>" for f in figs) + "</div>"
    return page("Results", body, "Results")


@app.route("/ci")
def ci():
    p = os.path.join(ROOT, "ci", "demo", "comment.md")
    md = open(p).read() if os.path.exists(p) else "run ci demo first"
    rows = []
    for l in md.splitlines():
        if l.startswith("|") and not l.startswith("|---"):
            cells = [x.strip() for x in l.strip("|").split("|")]
            tag = "th" if cells[0] == "#" else "td"
            cells = [f'<span class="pill {x.lower()}">{x}</span>' if x in ("PASS", "FAIL", "ERROR", "SKIP") else html.escape(x).replace("`", "") for x in cells]
            rows.append("<tr>" + "".join(f"<{tag}>{x}</{tag}>" for x in cells) + "</tr>")
    head = [l for l in md.splitlines() if not l.startswith("|")][:8]
    hh = "".join(f"<p>{html.escape(l.lstrip('#> ').replace('**',''))}</p>" for l in head if l.strip() and not l.startswith("<"))
    body = (f"<div class=card style='max-width:1100px'><h3>Pull request #42 · github-actions bot commented</h3>{hh}"
            f"<table>{''.join(rows)}</table></div>")
    return page("CI gate", body, "CI gate")


@app.route("/figures/<path:f>")
def figures(f):
    return send_from_directory(os.path.join(ROOT, "figures"), f)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
