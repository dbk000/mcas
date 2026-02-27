#!/usr/bin/env python3
"""Convert EQ_NVDA_MACRO briefing markdown to styled PDF."""
import markdown
from weasyprint import HTML

MD_PATH = "EQ_NVDA_MACRO_BRIEFING_FOR_ALAN.md"
PDF_PATH = "EQ_NVDA_MACRO_BRIEFING_FOR_ALAN.pdf"

CSS = """
@page {
    size: A4;
    margin: 2cm 2.5cm;
    @bottom-center { content: counter(page) " / " counter(pages); font-size: 9px; color: #888; }
}
body {
    font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #1a1a1a;
}
h1 {
    font-size: 22pt;
    color: #0d1117;
    border-bottom: 3px solid #f59e0b;
    padding-bottom: 8px;
    margin-top: 0;
}
h2 {
    font-size: 14pt;
    color: #1f2937;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 4px;
    margin-top: 28px;
    page-break-after: avoid;
}
h3 {
    font-size: 12pt;
    color: #374151;
    margin-top: 18px;
    page-break-after: avoid;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
th {
    background-color: #1f2937;
    color: white;
    padding: 6px 10px;
    text-align: left;
    font-weight: 600;
}
td {
    padding: 5px 10px;
    border-bottom: 1px solid #e5e7eb;
}
tr:nth-child(even) td {
    background-color: #f9fafb;
}
strong {
    color: #111827;
}
hr {
    border: none;
    border-top: 2px solid #f59e0b;
    margin: 24px 0;
}
code {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 9pt;
    background: #f3f4f6;
    padding: 1px 4px;
    border-radius: 3px;
}
blockquote {
    border-left: 4px solid #f59e0b;
    margin: 12px 0;
    padding: 8px 16px;
    background: #fffbeb;
    font-style: italic;
}
em {
    color: #6b7280;
}
.subtitle {
    color: #6b7280;
    font-size: 10pt;
    margin-top: -10px;
}
"""

with open(MD_PATH, "r") as f:
    md_text = f.read()

html_body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code"],
)

html_doc = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{CSS}</style></head>
<body>{html_body}</body>
</html>"""

HTML(string=html_doc).write_pdf(PDF_PATH)
print(f"PDF written to {PDF_PATH}")
