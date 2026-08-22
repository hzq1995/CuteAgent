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

function splitCollapsedTableLine(line) {
  // 把被压成一行的表格拆开：先按 | | 边界切段，找到 |---| 分隔段确定列数，
  // 再把所有单元格按列数重新分组，避免空单元格（| |）被误当成行边界。
  const segments = line
    .replace(/\|\s+(?=\|)/g, "|\n")
    .split("\n")
    .map((segment) => segment.trim())
    .filter(Boolean);
  const dividerIndex = segments.findIndex(isTableDivider);
  if (dividerIndex < 1) return line; // 分隔段前面必须有表头
  const columnCount = parseTableRow(segments[dividerIndex]).length;
  const cells = segments
    .filter((_, index) => index !== dividerIndex)
    .flatMap((segment) => parseTableRow(segment) || []);
  if (!cells.length || cells.length % columnCount !== 0) return line; // 列数对不齐时保守放弃
  const rows = [];
  for (let i = 0; i < cells.length; i += columnCount) {
    rows.push(`| ${cells.slice(i, i + columnCount).join(" | ")} |`);
  }
  rows.splice(1, 0, segments[dividerIndex]); // 表头行之后插回分隔行
  return rows.join("\n");
}

function normalizeMarkdownTables(source) {
  return source
    .split("\n")
    .map((line) =>
      // 同一行里既有表格分隔符（|---|）又有被压扁的行边界（| |）才尝试修复
      /\|\s*:?-+:?\s*\|/.test(line) && /\|\s+\|/.test(line) ? splitCollapsedTableLine(line) : line,
    )
    .join("\n");
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
  if (!head) return null;

  // 流式输出时，最后一行经常还没有接收完整。忽略这类暂不完整的行，
  // 保留已经确认的表格结构，避免表格和普通段落之间来回切换。
  const parsedBody = rows.slice(2).map(parseTableRow);
  const completedBody = parsedBody.slice(0, -1);
  if (completedBody.some((row) => !row || row.length !== head.length)) return null;
  const body = parsedBody.filter((row) => row && row.length === head.length);

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

    // 表格的最后一行在流式传输中可能还没有收到结尾的 `|`，
    // 仍把它留在表格缓冲区里，避免暂时降级成表格后面的普通段落。
    if (parseTableRow(line) || (table.length >= 2 && line.trim().startsWith("|"))) {
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
