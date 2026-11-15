"""Screenshot helpers: headless Chrome for GUI pages, and terminal-style captures of real command output."""
import html, os, subprocess, sys, time
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def page(url, out, w=1500, h=1100, wait_ms=120000):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={w},{h}",
                    f"--virtual-time-budget={wait_ms}", "--run-all-compositor-stages-before-draw",
                    f"--screenshot={os.path.abspath(out)}", url], check=True, capture_output=True, timeout=600)


def terminal(cmd, out, title=None, w=1300, max_lines=60, cwd=ROOT, shown_cmd=None, text=None):
    if text is None:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=3600)
        text = (r.stdout + r.stderr)
    lines = text.rstrip().splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines // 2] + [f"... ({len(lines) - max_lines} lines omitted) ..."] + lines[-max_lines // 2:]
    body = html.escape("\n".join(lines))
    prompt = html.escape(shown_cmd or cmd)
    h = 90 + 17 * (len(lines) + 2)
    doc = f"""<html><body style="margin:0;background:#e9edf3;font-family:Menlo,monospace">
<div style="margin:14px;border-radius:10px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.25)">
<div style="background:#2d3139;padding:9px 12px;display:flex;align-items:center;gap:7px">
<span style="width:12px;height:12px;border-radius:6px;background:#ff5f57;display:inline-block"></span>
<span style="width:12px;height:12px;border-radius:6px;background:#febc2e;display:inline-block"></span>
<span style="width:12px;height:12px;border-radius:6px;background:#28c840;display:inline-block"></span>
<span style="color:#aab2bf;font-size:12px;margin-left:12px">{html.escape(title or 'zsh — CN_Phase3')}</span></div>
<pre style="margin:0;background:#1b1e24;color:#d7dae0;padding:14px 16px;font-size:12.5px;line-height:17px;white-space:pre-wrap">
<span style="color:#7ee787">student@MacBook-M4</span> <span style="color:#79c0ff">CN_Phase3</span> % {prompt}
{body}</pre></div></body></html>"""
    tmp = os.path.join(ROOT, "screenshots", "_term.html")
    open(tmp, "w").write(doc)
    page("file://" + tmp, out, w=w, h=h, wait_ms=1000)
    return text


if __name__ == "__main__":
    terminal(sys.argv[1], sys.argv[2])
