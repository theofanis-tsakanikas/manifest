"""Render a note with mermaid diagrams to a print-ready HTML page, for Chrome to print.

    python scripts/notes_to_pdf.py notes/ARCHITECTURE.md mermaid.min.js out.html
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
      --no-pdf-header-footer --virtual-time-budget=20000 \
      --print-to-pdf=notes/ARCHITECTURE.pdf file://$PWD/out.html

**Two things here were learned by looking at the output rather than by reasoning about it.**

Mermaid reads *HTML* inside a node label, never markdown, so `**bold**` arrives on the page as
four asterisks. It is converted before escaping — the browser turns `&lt;b&gt;` back into a
literal `<b>` in `textContent`, which is what mermaid parses.

And a tall top-to-bottom flowchart scaled to fit a page is unreadable: twelve ranks deep at A4
width leaves type at four points. The fix is the diagram's aspect ratio, not the font size —
`rankSpacing` compressed and the chart kept nearer square. The first attempt at this was
rendered, looked at, and thrown away twice.
"""

import html
import pathlib
import re
import sys

import markdown

source = pathlib.Path(sys.argv[1])
mermaid_js = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
out = pathlib.Path(sys.argv[3])

text = source.read_text(encoding="utf-8")

# Pull the mermaid fences out before markdown touches them, so the code stays verbatim.
blocks = []


def stash(match):
    blocks.append(match.group(1))
    return f"\n@@MERMAID{len(blocks) - 1}@@\n"


text = re.sub(r"```mermaid\n(.*?)```", stash, text, flags=re.DOTALL)

body = markdown.markdown(text, extensions=["tables", "fenced_code", "codehilite", "attr_list"])
for index, block in enumerate(blocks):
    # Mermaid reads HTML inside a label, not markdown, so `**bold**` arrives as four asterisks
    # on the page. Converted before escaping: the browser turns `&lt;b&gt;` back into literal
    # `<b>` in textContent, which is exactly what mermaid then parses.
    block = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", block)
    body = body.replace(
        f"<p>@@MERMAID{index}@@</p>",
        f'<div class="mermaid">{html.escape(block)}</div>',
    )

page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Manifest — architecture</title>
<style>
  @page {{ size: A4; margin: 13mm 13mm; }}
  html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
         font-size: 9.8pt; line-height: 1.45; color: #1a1a1a; max-width: 100%; }}
  h1 {{ font-size: 19pt; margin: 0 0 3pt; letter-spacing: -0.4pt; }}
  h2 {{ font-size: 12.5pt; margin: 15pt 0 6pt; padding-top: 6pt;
        border-top: 1.5px solid #d8d8d8; page-break-after: avoid; }}
  h1 + p em, h1 + p {{ color: #666; }}
  p, li {{ orphans: 3; widows: 3; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 8.8pt;
           page-break-inside: avoid; }}
  th {{ text-align: left; background: #f2f4f7; }}
  th, td {{ border: 1px solid #d5d9e0; padding: 4pt 7pt; vertical-align: top; }}
  code {{ font-family: "SF Mono", Menlo, monospace; font-size: 9pt;
          background: #f4f4f6; padding: 1px 4px; border-radius: 3px; }}
  pre {{ background: #f7f7f9; border: 1px solid #e3e3e8; border-radius: 4px;
         padding: 8pt 10pt; font-size: 8.5pt; overflow-x: auto; page-break-inside: avoid; }}
  pre code {{ background: none; padding: 0; }}
  hr {{ display: none; }}
  blockquote {{ margin: 0; padding-left: 12pt; border-left: 3px solid #c8c8c8; color: #444; }}
  .mermaid {{ text-align: center; margin: 9pt 0; page-break-inside: avoid; }}
  .mermaid svg {{ max-width: 100%; max-height: 168mm; height: auto; }}
  strong {{ font-weight: 650; }}
</style>
<script>{mermaid_js}</script>
</head><body>
{body}
<script>
  mermaid.initialize({{ startOnLoad: false, theme: "base", flowchart: {{ htmlLabels: true, curve: "basis", rankSpacing: 26, nodeSpacing: 26, padding: 6 }},
    themeVariables: {{ fontFamily: "-apple-system, Helvetica Neue, Arial, sans-serif", fontSize: "15px" }} }});
  mermaid.run().then(() => {{ document.title = "ready:" + document.title; }});
</script>
</body></html>"""
out.write_text(page, encoding="utf-8")
print(f"wrote {out} ({len(page) // 1024} KB), {len(blocks)} diagram(s)")
