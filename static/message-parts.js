// message-parts.js — 流式内容追加函数
// 依赖：markdown.js（escapeHtml, renderAnswer via dom-utils.js）
// 依赖：dom-utils.js（assistantParts, lastPart, renderAnswer, collapseReasoning, scheduleScrollToBottom）

const pendingAnswerRenders = new Map();
const agentProgressTimers = new Map();
const AGENT_PROGRESS_BLOCK_COUNT = 12;
const AGENT_PROGRESS_STEP_MS = 1000;

function renderAgentProgress(progress) {
  const startedAt = Number(progress.dataset.startedAt) || Date.now();
  progress.dataset.startedAt = String(startedAt);
  const elapsed = Math.max(0, Date.now() - startedAt);
  const activeCount = Math.min(
    AGENT_PROGRESS_BLOCK_COUNT,
    Math.max(1, Math.floor(elapsed / AGENT_PROGRESS_STEP_MS) + 1)
  );
  const blocks = progress.querySelector(".agent-progress-blocks");
  if (!blocks) return;

  while (blocks.children.length < AGENT_PROGRESS_BLOCK_COUNT) {
    blocks.appendChild(document.createElement("i"));
  }
  Array.from(blocks.children).forEach((block, index) => {
    block.classList.toggle("active", index < activeCount);
  });
  progress.classList.toggle("full", activeCount >= AGENT_PROGRESS_BLOCK_COUNT);
}

function startAgentProgress(messageId, progress) {
  if (!progress) return;
  renderAgentProgress(progress);
  if (agentProgressTimers.has(messageId)) return;
  const timer = window.setInterval(() => {
    if (!progress.isConnected) {
      window.clearInterval(timer);
      agentProgressTimers.delete(messageId);
      return;
    }
    renderAgentProgress(progress);
  }, AGENT_PROGRESS_STEP_MS);
  agentProgressTimers.set(messageId, timer);
}

function ensureAgentProgress(messageId) {
  const parts = assistantParts(messageId);
  if (!parts) return null;
  let progress = parts.querySelector(".agent-progress");
  if (!progress) {
    progress = document.createElement("div");
    progress.className = "agent-progress waiting";
    progress.dataset.partType = "progress";
    progress.setAttribute("role", "status");
    progress.setAttribute("aria-label", "Processing");
    progress.innerHTML =
      '<span class="agent-progress-label">Processing</span>' +
      '<span class="agent-progress-blocks" aria-hidden="true"></span>';
    parts.appendChild(progress);
  }
  startAgentProgress(messageId, progress);
  return progress;
}

function resetAgentProgress(messageId) {
  const timer = agentProgressTimers.get(messageId);
  if (timer) {
    window.clearInterval(timer);
    agentProgressTimers.delete(messageId);
  }
  const parts = assistantParts(messageId);
  parts?.querySelector(".agent-progress")?.remove();
  return ensureAgentProgress(messageId);
}

function removeAgentProgress(messageId, { animate = true } = {}) {
  const timer = agentProgressTimers.get(messageId);
  if (timer) {
    window.clearInterval(timer);
    agentProgressTimers.delete(messageId);
  }
  const selector =
    '[data-message-id="' +
    messageId +
    '"] .agent-progress, [data-message-id="' +
    messageId +
    '"] .waiting';
  const targets = document.querySelectorAll(selector);
  targets.forEach((target) => {
    if (animate && target.classList.contains("agent-progress")) {
      target.classList.add("agent-progress-fading");
      window.setTimeout(() => target.remove(), 240);
    } else {
      target.remove();
    }
  });
}

function scheduleAnswerRender(messageId, item) {
  if (pendingAnswerRenders.has(messageId)) return;
  const frame = requestAnimationFrame(() => {
    pendingAnswerRenders.delete(messageId);
    renderAnswer(item);
    scheduleScrollToBottom();
    finishReasoningParts(messageId);
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

function flushPendingAnswerRenders() {
  for (const frame of pendingAnswerRenders.values()) {
    cancelAnimationFrame(frame);
  }
  pendingAnswerRenders.clear();

  const messageIds = new Set();
  document.querySelectorAll(".assistant-parts .answer").forEach((item) => {
    if (item.dataset.raw === undefined || item.dataset.raw === item.dataset.renderedRaw) return;
    renderAnswer(item);
    const messageId = item.closest("[data-message-id]")?.dataset.messageId;
    if (messageId) messageIds.add(messageId);
  });

  messageIds.forEach((messageId) => {
    finishReasoningParts(messageId);
    collapseReasoning(messageId);
  });
  if (messageIds.size) scheduleScrollToBottom();
}

// Chrome 会暂停后台标签页的 requestAnimationFrame；切回时先把期间积累的
// raw answer 全部补刷，避免思考/工具仍在更新但正常回答暂时不可见。
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") flushPendingAnswerRenders();
});

function appendReasoningDelta(messageId, delta) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  removeAgentProgress(messageId);
  let item = lastPart(messageId, "reasoning");
  if (!item) {
    item = document.createElement("details");
    item.className = "reasoning thinking";
    item.open = true;
    item.dataset.partType = "reasoning";
    item.dataset.thinkingStart = String(Date.now());
    item.innerHTML = '<summary>Thinking<span class="think-dots"><i></i><i></i><i></i></span></summary><pre></pre>';
    parts.appendChild(item);
  }
  const pre = item.querySelector("pre");
  // 记录追加前是否贴近底部：用户主动往上翻看时（>48px）不强制跟随
  const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 48;
  const chunk = document.createElement("span");
  chunk.className = "delta-chunk";
  chunk.textContent = delta;
  pre.appendChild(chunk);
  if (nearBottom) {
    pre.scrollTop = pre.scrollHeight;
  }
  scheduleScrollToBottom();
}

// 思考结束：去掉动画状态，改名为"已深度思考"（有起点时间则附上用时）
function finishReasoningParts(messageId) {
  document.querySelectorAll(`[data-message-id="${messageId}"] .reasoning.thinking`).forEach((details) => {
    details.classList.remove("thinking");
    const summary = details.querySelector("summary");
    if (!summary) return;
    let label = "已深度思考";
    const start = Number(details.dataset.thinkingStart);
    if (start) {
      const sec = Math.max(1, Math.round((Date.now() - start) / 1000));
      label += sec < 60 ? ` · ${sec}s` : ` · ${Math.floor(sec / 60)}m${sec % 60}s`;
    }
    summary.textContent = label;
  });
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

  const progress = parts.querySelector(".agent-progress");
  progress?.remove();
  parts.querySelector(".waiting")?.remove();
  finishReasoningParts(messageId);
  const article = document.createElement("article");
  article.className = "message tool-message inline-tool-message tool-call-message";
  article.dataset.partType = "tool-call";
  article.dataset.toolCallId = message.tool_call_id || "";
  article.innerHTML = `
    <div class="tool-card">
      <div class="tool-card-header">
        <span>${escapeHtml(message.name || "tool")}</span>
        <span class="message-status running">调用中</span>
      </div>
      <details>
        <summary>调用参数</summary>
        <pre>${escapeHtml(formatToolValue(message.arguments))}</pre>
      </details>
    </div>
  `;
  parts.appendChild(article);
  if (progress) parts.appendChild(progress);
  scheduleScrollToBottom();
}

function appendAnswerDelta(messageId, delta) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  removeAgentProgress(messageId);
  finishReasoningParts(messageId);
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

function appendToolMessage(messageId, message, showProgress = false) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  const progress = parts.querySelector(".agent-progress");
  progress?.remove();
  parts.querySelector(".waiting")?.remove();
  finishReasoningParts(messageId);
  updateToolCallStatus(parts, message.tool_call_id, message.status);
  if (findToolResultCard(parts, message.tool_call_id)) {
    if (showProgress) resetAgentProgress(messageId);
    else if (progress) parts.appendChild(progress);
    return;
  }
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
        <span>${escapeHtml(message.name || "tool")}</span>
        <span class="message-status ${escapeHtml(message.status || "")}">${escapeHtml(message.status || "")}</span>
      </div>
      ${transfer ? transferHtml(transfer) : ""}
      <details>
        <summary>查看结果${resultSize ? ` <span class="tool-result-size">${resultSize.toLocaleString("zh-CN")} 字符</span>` : ""}</summary>
        <pre class="tool-result-content">${escapeHtml(resultText)}</pre>
        ${resultUrl ? `<button class="tool-result-load" type="button" data-tool-result-url="${escapeHtml(resultUrl)}">加载完整结果</button>` : ""}
      </details>
    </div>
  `;
  parts.appendChild(article);
  if (showProgress) resetAgentProgress(messageId);
  else if (progress) parts.appendChild(progress);
  scheduleScrollToBottom();
}

function parseToolArgumentsRaw(raw) {
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

// 快照回放后：若任务仍在进行且最后一部分是思考块，恢复"思考中"动画状态
function markActiveReasoning(parts, message) {
  if (message.status !== "queued" && message.status !== "running") return;
  const last = parts.lastElementChild;
  if (!last || last.dataset.partType !== "reasoning") return;
  last.classList.add("thinking");
  last.dataset.thinkingStart = String(Date.now());
  const summary = last.querySelector("summary");
  if (summary) summary.innerHTML = 'Thinking<span class="think-dots"><i></i><i></i><i></i></span>';
}

function syncAssistantSnapshot(messageId, message) {
  const parts = assistantParts(messageId);
  if (!parts || !message) return;

  removeAgentProgress(messageId, { animate: false });
  parts.replaceChildren();
  if (Array.isArray(message.render_parts)) {
    // 合并后的线性序列：tool_call / reasoning / tool / answer 按时间顺序排列
    for (const part of message.render_parts) {
      if (part.type === "tool_call") {
        const call = part.tool_call || {};
        appendToolCall(messageId, {
          tool_call_id: call.id || "",
          name: call.function?.name || "tool",
          arguments: parseToolArgumentsRaw(call.function?.arguments),
        });
        continue;
      }
      if (part.type === "reasoning") {
        const item = document.createElement("details");
        item.className = "reasoning";
        item.open = message.status === "queued" || message.status === "running";
        item.dataset.partType = "reasoning";
        item.innerHTML = "<summary>已深度思考</summary><pre></pre>";
        const pre = item.querySelector("pre");
        pre.textContent = part.content || "";
        // 快照回放时任务仍在跑则定位到最新内容，已结束的保持从头展示
        if (item.open) pre.scrollTop = pre.scrollHeight;
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
  } else {
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
        item.innerHTML = "<summary>已深度思考</summary><pre></pre>";
        const pre = item.querySelector("pre");
        pre.textContent = part.content || "";
        // 快照回放时任务仍在跑则定位到最新内容，已结束的保持从头展示
        if (item.open) pre.scrollTop = pre.scrollHeight;
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
  // 所有内容追加完毕后再判定：仅当任务进行中且最后一个部分是思考块时恢复动画
  markActiveReasoning(parts, message);
  const isActive = message.status === "queued" || message.status === "running";
  const lastType = parts.lastElementChild?.dataset.partType;
  if (isActive && lastType !== "reasoning" && lastType !== "answer") {
    ensureAgentProgress(messageId);
  }
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
