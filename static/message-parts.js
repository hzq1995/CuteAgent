// message-parts.js — 流式内容追加函数
// 依赖：markdown.js（escapeHtml, renderAnswer via dom-utils.js）
// 依赖：dom-utils.js（assistantParts, lastPart, renderAnswer, collapseReasoning, scrollToBottom）

const answerRevealFrames = new WeakMap();

function revealStepSize(pendingLength) {
  if (pendingLength > 600) return 3;
  if (pendingLength > 280) return 2;
  if (pendingLength > 100) return 1;
  return 1;
}

function startAnswerReveal(item) {
  if (answerRevealFrames.has(item)) return;
  item.classList.add("answer-revealing");

  const tick = () => {
    const visible = item.dataset.raw || "";
    const target = item.dataset.rawTarget || visible;
    const pendingLength = target.length - visible.length;

    if (pendingLength <= 0) {
      item.classList.remove("answer-revealing");
      answerRevealFrames.delete(item);
      renderAnswer(item);
      return;
    }

    const nextLength = visible.length + Math.min(revealStepSize(pendingLength), pendingLength);
    item.dataset.raw = target.slice(0, nextLength);
    renderAnswer(item);
    scrollToBottom();
    answerRevealFrames.set(item, requestAnimationFrame(tick));
  };

  answerRevealFrames.set(item, requestAnimationFrame(tick));
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
    item.dataset.rawTarget = "";
    parts.appendChild(item);
  }
  if (item.dataset.raw === undefined) {
    item.dataset.raw = item.textContent || "";
  }
  item.dataset.rawTarget = (item.dataset.rawTarget || item.dataset.raw || "") + delta;
  startAnswerReveal(item);
  collapseReasoning(messageId);
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
