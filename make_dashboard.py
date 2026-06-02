"""
make_dashboard.py
-----------------
Turns the most recent commodity_news_*.md report into a polished,
self-contained HTML dashboard you can open in any web browser.

Usage:
    python make_dashboard.py

It looks in the ./commodity_reports folder, finds the newest report,
and writes ./commodity_reports/commodity_dashboard.html, then opens it.

No extra libraries needed.
"""

import os
import re
import glob
import html
import webbrowser
from datetime import datetime

# ---- locate the newest report -------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(SCRIPT_DIR, "commodity_reports")


def find_latest_report():
    pattern = os.path.join(REPORTS_DIR, "commodity_news_*.md")
    files = glob.glob(pattern)
    if not files:
        return None
    # newest by modified time
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


# ---- tiny markdown -> HTML converter (no dependencies) ------------------------

def inline(text):
    """Apply inline markdown: escaping, links, bold, italic, code."""
    text = html.escape(text)
    # links [label](url)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)',
                  r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    # bold **text**
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    # italic *text*
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)
    # inline code `code`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def md_to_html(md):
    """Convert a subset of markdown to HTML blocks."""
    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank line
        if stripped == "":
            i += 1
            continue

        # horizontal rule
        if re.match(r'^-{3,}$', stripped):
            out.append('<hr/>')
            i += 1
            continue

        # headings
        m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if m:
            level = len(m.group(1))
            content = inline(m.group(2))
            out.append(f'<h{level}>{content}</h{level}>')
            i += 1
            continue

        # unordered list
        if re.match(r'^[-*]\s+', stripped):
            items = []
            while i < n and re.match(r'^[-*]\s+', lines[i].strip()):
                item = re.sub(r'^[-*]\s+', '', lines[i].strip())
                items.append(f'<li>{inline(item)}</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue

        # ordered list
        if re.match(r'^\d+\.\s+', stripped):
            items = []
            while i < n and re.match(r'^\d+\.\s+', lines[i].strip()):
                item = re.sub(r'^\d+\.\s+', '', lines[i].strip())
                items.append(f'<li>{inline(item)}</li>')
                i += 1
            out.append('<ol>' + ''.join(items) + '</ol>')
            continue

        # paragraph (gather consecutive non-blank, non-special lines)
        para = []
        while i < n and lines[i].strip() != "" and not re.match(
                r'^(#{1,6}\s|[-*]\s|\d+\.\s|-{3,}$)', lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        out.append('<p>' + inline(' '.join(para)) + '</p>')

    return '\n'.join(out)


# ---- build the page -----------------------------------------------------------

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Commodity Briefing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #1a1714;
    --ink-soft: #4a443d;
    --paper: #f4efe6;
    --paper-2: #fbf8f1;
    --line: #d9d0bf;
    --gold: #9a6b24;
    --up: #2f6b3f;
    --down: #a33a2b;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background:
      radial-gradient(circle at 15% 10%, rgba(154,107,36,0.06), transparent 40%),
      var(--paper);
    color: var(--ink);
    font-family: 'Newsreader', Georgia, serif;
    font-size: 18px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }}
  .sheet {{
    max-width: 860px;
    margin: 0 auto;
    padding: 56px 28px 80px;
  }}
  /* Masthead */
  .masthead {{
    border-bottom: 3px double var(--ink);
    padding-bottom: 18px;
    margin-bottom: 8px;
  }}
  .kicker {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--gold);
    margin: 0 0 10px;
  }}
  .title {{
    font-family: 'Fraunces', serif;
    font-weight: 900;
    font-size: clamp(34px, 7vw, 58px);
    line-height: 1.02;
    letter-spacing: -0.01em;
    margin: 0;
  }}
  .dateline {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    color: var(--ink-soft);
    margin-top: 14px;
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
    border-top: 1px solid var(--line);
    padding-top: 12px;
  }}
  .dateline .dot {{ color: var(--gold); }}
  /* Content */
  .content {{ margin-top: 30px; }}
  .content h2 {{
    font-family: 'Fraunces', serif;
    font-weight: 600;
    font-size: 15px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--gold);
    margin: 0 0 14px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--line);
  }}
  /* section wrapper: each h2 starts a card */
  .section {{
    background: var(--paper-2);
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 24px 26px;
    margin: 0 0 22px;
    box-shadow: 0 1px 0 rgba(0,0,0,0.02);
  }}
  .content h3 {{
    font-family: 'Fraunces', serif;
    font-weight: 600;
    font-size: 21px;
    margin: 18px 0 8px;
  }}
  .content p {{ margin: 0 0 14px; color: var(--ink); }}
  .content ul, .content ol {{ margin: 0 0 8px; padding-left: 22px; }}
  .content li {{ margin-bottom: 10px; }}
  .content li strong {{ color: var(--ink); }}
  .content a {{ color: var(--gold); text-decoration: underline; text-underline-offset: 2px; }}
  .content code {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85em;
    background: rgba(154,107,36,0.10);
    padding: 1px 5px;
    border-radius: 3px;
  }}
  .content hr {{
    border: none;
    border-top: 1px solid var(--line);
    margin: 26px 0;
  }}
  .content > p:last-child {{
    font-style: italic;
    color: var(--ink-soft);
    font-size: 15px;
  }}
  /* footer */
  .foot {{
    margin-top: 40px;
    border-top: 3px double var(--ink);
    padding-top: 14px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-soft);
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
  }}
  /* gentle load animation */
  .sheet > * {{ animation: rise 0.6s ease both; }}
  .content {{ animation-delay: 0.08s; }}
  @keyframes rise {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to {{ opacity: 1; transform: translateY(0); }}
  }}
  @media (max-width: 560px) {{
    body {{ font-size: 17px; }}
    .sheet {{ padding: 36px 18px 60px; }}
    .section {{ padding: 18px 18px; }}
  }}
</style>
</head>
<body>
  <main class="sheet">
    <header class="masthead">
      <p class="kicker">Daily Commodity Intelligence</p>
      <h1 class="title">Market Briefing</h1>
      <div class="dateline">
        <span>{report_date}</span>
        <span class="dot">&bull;</span>
        <span>Auto-generated</span>
        <span class="dot">&bull;</span>
        <span>Built {built_at}</span>
      </div>
    </header>
    <section class="content">
      {body}
    </section>
    <footer class="foot">
      <span>Commodity News Agent</span>
      <span>Informational only &mdash; not investment advice</span>
    </footer>
  </main>
</body>
</html>
"""


def wrap_sections(body_html):
    """Wrap everything from each <h2> up to the next <h2> in a .section card."""
    parts = re.split(r'(?=<h2>)', body_html)
    wrapped = []
    for part in parts:
        if part.strip() == "":
            continue
        if part.startswith('<h2>'):
            wrapped.append(f'<div class="section">{part}</div>')
        else:
            wrapped.append(part)  # intro text before first h2
    return '\n'.join(wrapped)


def extract_date(path, md):
    # try a date in the filename first
    m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(path))
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
            return d.strftime("%A, %B %-d, %Y") if os.name != "nt" else d.strftime("%A, %B %#d, %Y")
        except ValueError:
            pass
    return datetime.now().strftime("%A, %B %d, %Y")


def main():
    latest = find_latest_report()
    if not latest:
        print("No reports found in 'commodity_reports'. Run the news agent first.")
        return

    with open(latest, "r", encoding="utf-8") as f:
        md = f.read()

    # don't double-print the top-level title; the masthead handles the headline
    md = re.sub(r'^\s*#\s+.*$', '', md, count=1, flags=re.MULTILINE)

    body = wrap_sections(md_to_html(md))
    report_date = extract_date(latest, md)
    built_at = datetime.now().strftime("%I:%M %p").lstrip("0")

    page = PAGE_TEMPLATE.format(body=body, report_date=report_date, built_at=built_at)

    out_path = os.path.join(REPORTS_DIR, "commodity_dashboard.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Dashboard built from: {os.path.basename(latest)}")
    print(f"Saved to: {out_path}")
    try:
        # works reliably on Windows, macOS and Linux
        import pathlib
        webbrowser.open(pathlib.Path(out_path).as_uri())
        print("Opening it in your browser...")
    except Exception:
        print("Open the file above in your browser to view it.")


if __name__ == "__main__":
    main()
