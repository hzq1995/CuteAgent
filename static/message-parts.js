// message-parts.js — 流式内容追加函数
// 依赖：markdown.js（escapeHtml, renderAnswer via dom-utils.js）
// 依赖：dom-utils.js（assistantParts, lastPart, renderAnswer, collapseReasoning, scheduleScrollToBottom）

function appendReasoningDelta(messageId, delta) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  parts.querySelector(".waiting")?.remove();
  let item = lastPart(messageId, "reasoning");
  if (!item) {
    item = document.createElement("details");
    item.className = "reasoning";
    item.open = true;
    item.dataset.partType = "reasoning";
    item.innerHTML = "<summary>Thinking</summary><pre></pre>";
    parts.appendChild(item);
  }
  item.querySelector("pre").textContent += delta;
  scheduleScrollToBottom();
}

function formatToolValue(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value ?? {}, null, 2).replace(/\\u([\dA-Fa-f]{4})/g, (_, code) =>
      String.fromCharCode(parseInt(code, 16))
    );
  } catch {
    return String(value ?? "");
  }
}

function findToolCallCard(parts, toolCallId) {
  if (!toolCallId) return null;
  return Array.from(parts.querySelectorAll(".tool-call-message")).find(
    (item) => item.dataset.toolCallId === toolCallId
  );
}

function updateToolCallStatus(parts, toolCallId, status) {
  const card = findToolCallCard(parts, toolCallId);
  const target = card?.querySelector(".message-status");
  if (!target) return;
  target.textContent = status || "succeeded";
  target.className = `message-status ${status || "succeeded"}`;
}

function findToolResultCard(parts, toolCallId) {
  if (!toolCallId) return null;
  return Array.from(parts.querySelectorAll(".tool-result-message")).find(
    (item) => item.dataset.toolCallId === toolCallId
  );
}

function appendToolCall(messageId, message) {
  const parts = assistantParts(messageId);
  if (!parts || !message) return;
  if (findToolCallCard(parts, message.tool_call_id)) return;

  parts.querySelector(".waiting")?.remove();
  const article = document.createElement("article");
  article.className = "message tool-message inline-tool-message tool-call-message";
  article.dataset.partType = "tool-call";
  article.dataset.toolCallId = message.tool_call_id || "";
  article.innerHTML = `
    <div class="tool-card">
      <div class="tool-card-header">
        <span>工具调用 · ${escapeHtml(message.name || "tool")}</span>
        <span class="message-status running">调用中</span>
      </div>
      <details>
        <summary>调用参数</summary>
        <pre>${escapeHtml(formatToolValue(message.arguments))}</pre>
      </details>
    </div>
  `;
  parts.appendChild(article);
  scheduleScrollToBottom();
}

function appendAnswerDelta(messageId, delta) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  parts.querySelector(".waiting")?.remove();
  let item = lastPart(messageId, "answer");
  if (!item) {
    item = document.createElement("div");
    item.className = "answer markdown-body";
    item.dataset.partType = "answer";
    item.dataset.raw = "";
    parts.appendChild(item);
  }
  if (item.dataset.raw === undefined) {
    item.dataset.raw = item.textContent || "";
  }
  item.dataset.raw += delta;
  renderAnswer(item);
  scheduleScrollToBottom();
  collapseReasoning(messageId);
}

function appendToolMessage(messageId, message) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  parts.querySelector(".waiting")?.remove();
  updateToolCallStatus(parts, message.tool_call_id, message.status);
  if (findToolResultCard(parts, message.tool_call_id)) return;
  const article = document.createElement("article");
  article.className = "message tool-message inline-tool-message tool-result-message";
  article.dataset.partType = "tool-result";
  article.dataset.toolCallId = message.tool_call_id || "";
  const transfer = transferredFileFromToolResult(message.result);
  article.innerHTML = `
    <div class="tool-card">
      <div class="tool-card-header">
        <span>工具返回 · ${escapeHtml(message.name || "tool")}</span>
        <span class="message-status ${escapeHtml(message.status || "")}">${escapeHtml(message.status || "")}</span>
      </div>
      ${transfer ? transferredFileHtml(transfer.file) : ""}
      <details>
        <summary>查看返回结果</summary>
        <pre>${escapeHtml(formatToolValue(message.result))}</pre>
      </details>
    </div>
  `;
  parts.appendChild(article);
  scheduleScrollToBottom();
}

function transferredFileFromToolResult(result) {
  if (!result || result.ok !== true || !result.result) return null;
  return result.result.type === "transferred_file" ? result.result : null;
}

function transferredFileHtml(file) {
  if (!file) return "";
  const name = escapeHtml(file.name || "file");
  const url = escapeHtml(file.url || "#");
  const mime = escapeHtml(file.mime_type || "application/octet-stream");
  const size = escapeHtml(formatFileSize(file.size_bytes || 0));
  if (file.is_image) {
    return `
      <div class="transferred-file">
        <a class="transferred-image-link" href="${url}" target="_blank" rel="noopener">
          <img src="${url}" alt="${name}">
        </a>
      </div>
    `;
  }
  return `
    <div class="transferred-file">
      <a class="transferred-download" href="${url}" download>
        <span class="transferred-file-name">${name}</span>
        <span class="transferred-file-meta">${mime} · ${size}</span>
      </a>
    </div>
  `;
}

function formatFileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} bytes`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
