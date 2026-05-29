// markdown.js — Markdown 渲染引擎（纯函数，无外部依赖）

function escapeHtml(value) {
  value = String(value ?? "");
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function normalizeMarkdownTables(source) {
  return source.replace(/\|\s+(?=\|)/g, "|\n");
}

function parseTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
  return trimmed.slice(1, -1).split("|").map((cell) => cell.trim());
}

function isTableDivider(line) {
  const cells = parseTableRow(line);
  return Boolean(cells && cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell)));
}

function renderTable(rows) {
  if (rows.length < 2 || !isTableDivider(rows[1])) return null;
  const head = parseTableRow(rows[0]);
  const body = rows.slice(2).map(parseTableRow).filter(Boolean);
  if (!head || !body.length || body.some((row) => row.length !== head.length)) return null;

  return [
    "<div class=\"table-scroll\"><table>",
    `<thead><tr>${head.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead>`,
    `<tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody>`,
    "</table></div>",
  ].join("");
}

function renderMarkdown(source) {
  const lines = normalizeMarkdownTables(source.replace(/\r\n/g, "\n")).split("\n");
  const html = [];
  let paragraph = [];
  let list = [];
  let table = [];
  let inCode = false;
  let codeLines = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }

  function flushList() {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  }

  function flushTable() {
    if (!table.length) return;
    const tableHtml = renderTable(table);
    if (tableHtml) {
      html.push(tableHtml);
    } else {
      table.forEach((line) => paragraph.push(line.trim()));
    }
    table = [];
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        flushTable();
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      flushTable();
      flushParagraph();
      flushList();
      continue;
    }

    if (parseTableRow(line)) {
      flushParagraph();
      flushList();
      table.push(line);
      continue;
    }

    flushTable();

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      html.push(`<h${heading[1].length}>${renderInlineMarkdown(heading[2])}</h${heading[1].length}>`);
      continue;
    }

    const listItem = line.match(/^\s*[-*]\s+(.+)$/);
    if (listItem) {
      flushParagraph();
      list.push(listItem[1]);
      continue;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  flushTable();
  flushParagraph();
  flushList();
  return html.join("");
}
