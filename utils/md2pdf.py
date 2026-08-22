#!/usr/bin/env python
"""Markdown 转 PDF（中文友好）。用法见 skills/技术-06-Markdown转PDF-md2pdf文档导出.md"""
import os
import sys

import markdown
from weasyprint import HTML

CSS = """
@page { size: A4; margin: 1.6cm 1.4cm; }
* { box-sizing: border-box; }
body { font-family: 'WenQuanYi Zen Hei', 'Droid Sans Fallback', sans-serif;
       font-size: 10.5pt; line-height: 1.65; color: #263238; }
h1 { font-size: 17pt; color: #1a237e; border-bottom: 2.5pt solid #1a237e;
     padding-bottom: 6pt; margin: 0 0 10pt 0; }
h2 { font-size: 13.5pt; color: #1a237e; margin: 18pt 0 8pt 0;
     border-left: 4pt solid #3949ab; padding-left: 7pt; }
h3 { font-size: 11.5pt; color: #283593; margin: 13pt 0 6pt 0; }
p { margin: 5pt 0; }
strong { color: #b71c1c; }
blockquote { margin: 8pt 0; padding: 7pt 12pt; background: #eef2ff;
             border-left: 4pt solid #5c6bc0; color: #37474f; }
blockquote p { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 9pt 0; font-size: 9.5pt; }
th { background: #3949ab; color: white; padding: 5pt 7pt; text-align: left; }
td { border-bottom: 0.6pt solid #c5cae9; padding: 4.5pt 7pt; }
tr:nth-child(even) td { background: #f5f6ff; }
li { margin: 3pt 0; }
img { max-width: 100%; margin: 6pt 0; }
hr { border: none; border-top: 0.8pt solid #c5cae9; margin: 14pt 0; }
em { color: #546e7a; }
code { background: #eceff1; padding: 0 3pt; border-radius: 2pt; font-size: 9.5pt; }
"""


def md2pdf(md_file: str, out_pdf: str = None) -> str:
    """把 Markdown 文件转成 PDF。图片按 md 文件所在目录的相对路径解析。"""
    md_file = os.path.abspath(md_file)
    if out_pdf is None:
        out_pdf = os.path.splitext(md_file)[0] + ".pdf"
    out_pdf = os.path.abspath(out_pdf)

    body = open(md_file, encoding="utf-8").read()
    html_body = markdown.markdown(body, extensions=["tables", "fenced_code"])
    html = (
        '<html><head><meta charset="utf-8">'
        f"<style>{CSS}</style></head><body>{html_body}</body></html>"
    )
    # base_url 设为 md 所在目录，使 md 里的相对路径图片能正常嵌入
    HTML(string=html, base_url=os.path.dirname(md_file)).write_pdf(out_pdf)
    return out_pdf


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libffi.so.7 "
              "python utils/md2pdf.py 输入.md [输出.pdf]")
        sys.exit(1)
    print("PDF saved:", md2pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
