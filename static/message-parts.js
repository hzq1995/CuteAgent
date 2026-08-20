// message-parts.js — 流式内容追加函数
// 依赖：markdown.js（escapeHtml, renderAnswer via dom-utils.js）
// 依赖：dom-utils.js（assistantParts, lastPart, renderAnswer, collapseReasoning, scheduleScrollToBottom）

const pendingAnswerRenders = new Map();

function scheduleAnswerRender(messageId, item) {
  if (pendingAnswerRenders.has(messageId)) return;
  const frame = requestAnimationFrame(() => {
    pendingAnswerRenders.delete(messageId);
    renderAnswer(item);
    scheduleScrollToBottom();
    collapseReasoning(messageId);
  });
  pendingAnswerRenders.set(messageId, frame);
}

function flushAnswerRender(messageId) {
  const frame = pendingAnswerRenders.get(messageId);
  if (frame) {
    cancelAnimationFrame(frame);
    pendingAnswerRenders.delete(messageId);
  }
  const item = lastPart(messageId, "answer");
  if (item) renderAnswer(item);
}

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
  scheduleAnswerRender(messageId, item);
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
  const transfer = message.transfer || transferredFileFromToolResult(message.result);
  const resultText = message.result_preview ?? formatToolValue(message.result);
  const resultUrl = message.result_url || "";
  const resultSize = Number(message.result_size_chars) || 0;
  article.innerHTML = `
    <div class="tool-card">
      <div class="tool-card-header">
        <span>工具返回 · ${escapeHtml(message.name || "tool")}</span>
        <span class="message-status ${escapeHtml(message.status || "")}">${escapeHtml(message.status || "")}</span>
      </div>
      ${transfer ? transferHtml(transfer) : ""}
      <details>
        <summary>查看返回结果${resultSize ? ` <span class="tool-result-size">${resultSize.toLocaleString("zh-CN")} 字符</span>` : ""}</summary>
        <pre class="tool-result-content">${escapeHtml(resultText)}</pre>
        ${resultUrl ? `<button class="tool-result-load" type="button" data-tool-result-url="${escapeHtml(resultUrl)}">加载完整结果</button>` : ""}
      </details>
    </div>
  `;
  parts.appendChild(article);
  scheduleScrollToBottom();
}

function syncAssistantSnapshot(messageId, message) {
  const parts = assistantParts(messageId);
  if (!parts || !message) return;

  parts.replaceChildren();
  for (const toolCall of message.tool_calls || []) {
    const toolCallId = toolCall.id || "";
    if (!findToolCallCard(parts, toolCallId)) {
      appendToolCall(messageId, {
        tool_call_id: toolCallId,
        name: toolCall.function?.name || "tool",
        arguments: formatToolValue(toolCall.function?.arguments || "{}"),
      });
    }
  }

  for (const part of message.parts || []) {
    if (part.type === "reasoning") {
      const item = document.createElement("details");
      item.className = "reasoning";
      item.open = message.status === "queued" || message.status === "running";
      item.dataset.partType = "reasoning";
      item.innerHTML = "<summary>Thinking</summary><pre></pre>";
      item.querySelector("pre").textContent = part.content || "";
      parts.appendChild(item);
      continue;
    }

    if (part.type === "answer") {
      const item = document.createElement("div");
      item.className = "answer markdown-body";
      item.dataset.partType = "answer";
      item.dataset.raw = part.content || "";
      parts.appendChild(item);
      renderAnswer(item);
      continue;
    }

    if (part.type === "tool") {
      appendToolMessage(messageId, {
        tool_call_id: part.tool_call_id || part.tool_message_id,
        name: part.name,
        status: part.status,
        result: part.result,
        result_preview: part.result_preview,
        result_size_chars: part.result_size_chars,
        result_url: part.result_url,
        transfer: part.transfer,
      });
    }
  }

  for (const toolMessage of message.tool_messages || []) {
    appendToolMessage(messageId, toolMessage);
  }

  if (!parts.querySelector(".answer") && message.content) {
    const item = document.createElement("div");
    item.className = "answer markdown-body";
    item.dataset.partType = "answer";
    item.dataset.raw = message.content;
    parts.appendChild(item);
    renderAnswer(item);
  }

  parts.querySelectorAll(".answer").forEach(renderAnswer);
  scheduleScrollToBottom();
}

function transferredFileFromToolResult(result) {
  if (!result || result.ok !== true || !result.result) return null;
  return result.result.type === "transferred_file" ? result.result : null;
}

function transferHtml(transfer) {
  if (!transfer) return "";
  if (transfer.type === "user_question") return questionCardHtml(transfer);
  if (transfer.type === "transferred_file") return transferredFileHtml(transfer.file);
  return "";
}

function questionCardHtml(transfer) {
  const options = Array.isArray(transfer.options) ? transfer.options : [];
  const buttons = options
    .map(
      (option) => `
      <button type="button" class="question-option" data-option="${escapeHtml(option)}">${escapeHtml(option)}</button>`
    )
    .join("");
  const hint = transfer.allow_custom === false ? "请从上方选项中选择" : "点击选项，或直接输入你的回答";
  return `
    <div class="user-question-card">
      <div class="user-question-text">${escapeHtml(transfer.question || "")}</div>
      ${buttons ? `<div class="user-question-options">${buttons}</div>` : ""}
      <div class="user-question-hint">${escapeHtml(hint)}</div>
    </div>
  `;
}

document.addEventListener("click", (event) => {
  const optionButton = event.target.closest(".question-option");
  if (!optionButton || !textarea) return;
  textarea.value = optionButton.dataset.option || optionButton.textContent || "";
  if (typeof autoResizeTextarea === "function") autoResizeTextarea();
  if (!textarea.disabled) textarea.focus();
  scheduleScrollToBottom();
});

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
