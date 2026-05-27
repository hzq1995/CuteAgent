// message-parts.js — 流式内容追加函数
// 依赖：markdown.js（escapeHtml, renderAnswer via dom-utils.js）
// 依赖：dom-utils.js（assistantParts, lastPart, renderAnswer, collapseReasoning, scrollToBottom）

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
  scrollToBottom();
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
  item.dataset.raw = (item.dataset.raw || item.textContent) + delta;
  renderAnswer(item);
  collapseReasoning(messageId);
  scrollToBottom();
}

function appendToolMessage(messageId, message) {
  const parts = assistantParts(messageId);
  if (!parts) return;
  parts.querySelector(".waiting")?.remove();
  const article = document.createElement("article");
  article.className = "message tool-message inline-tool-message";
  article.dataset.partType = "tool";
  article.innerHTML = `
    <div class="tool-card">
      <div class="tool-card-header">
        <span>${escapeHtml(message.name || "tool")}</span>
        <span class="message-status ${escapeHtml(message.status || "")}">${escapeHtml(message.status || "")}</span>
      </div>
      <details>
        <summary>查看工具参数和结果</summary>
        <pre>${escapeHtml(JSON.stringify({ arguments: message.arguments, result: message.result }, null, 2).replace(/\\u([\dA-Fa-f]{4})/g, (_, c) => String.fromCharCode(parseInt(c, 16))))}</pre>
      </details>
    </div>
  `;
  parts.appendChild(article);
  scrollToBottom();
}
