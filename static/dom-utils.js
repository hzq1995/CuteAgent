// dom-utils.js — DOM 工具函数
// 依赖：markdown.js（escapeHtml, renderMarkdown）
// 依赖全局变量：messageList（由 index.html 内联脚本声明）

function renderAnswer(target) {
  if (!target) return;
  if (target.dataset.raw === undefined) {
    target.dataset.raw = target.textContent;
  }
  target.innerHTML = renderMarkdown(target.dataset.raw);
}

const CHAT_BOTTOM_THRESHOLD = 80;
let chatAutoFollow = true;
let chatScrollTrackingReady = false;
let pendingChatScrollFrame = 0;

function chatScroller() {
  return document.getElementById("chat-scroll");
}

function isChatNearBottom(scroller = chatScroller()) {
  if (!scroller) return true;
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= CHAT_BOTTOM_THRESHOLD;
}

function initChatScrollTracking() {
  if (chatScrollTrackingReady) return;
  const scroller = document.getElementById("chat-scroll");
  if (!scroller) return;
  chatScrollTrackingReady = true;

  scroller.addEventListener("scroll", () => {
    chatAutoFollow = isChatNearBottom(scroller);
  });

  document.addEventListener(
    "toggle",
    (event) => {
      if (!scroller.contains(event.target)) return;
      chatAutoFollow = isChatNearBottom(scroller);
    },
    true
  );
}

function scrollToBottom(options = {}) {
  const scroller = chatScroller();
  if (!scroller) return;
  initChatScrollTracking();
  if (!options.force && !chatAutoFollow && !isChatNearBottom(scroller)) return;

  const applyScroll = () => {
    pendingChatScrollFrame = 0;
    scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    chatAutoFollow = true;
  };

  if (options.force && pendingChatScrollFrame) {
    cancelAnimationFrame(pendingChatScrollFrame);
    pendingChatScrollFrame = 0;
  }
  if (options.force) {
    applyScroll();
  }
  if (!pendingChatScrollFrame) {
    pendingChatScrollFrame = requestAnimationFrame(applyScroll);
  }
}

function setStatus(messageId, value) {
  const status = document.querySelector(`[data-message-id="${messageId}"] .assistant-status-row .message-status`);
  if (status) {
    status.textContent = value;
    status.className = `message-status ${value}`;
  }
}

function replaceAssistantId(oldId, newId) {
  if (!oldId || !newId || oldId === newId) return;
  const article = document.querySelector(`[data-message-id="${oldId}"]`);
  const parts = document.getElementById(`parts-${oldId}`);
  if (article) {
    article.dataset.messageId = newId;
  }
  if (parts) {
    parts.id = `parts-${newId}`;
  }
}

function markAssistantFailed(messageId, message) {
  setStatus(messageId, "failed");
  const target = document.querySelector(`[data-message-id="${messageId}"] .assistant-body`);
  if (!target) return;
  target.querySelector(".waiting")?.remove();
  const error = document.createElement("p");
  error.className = "error";
  error.textContent = message || "发送失败";
  target.appendChild(error);
  scrollToBottom();
}

function collapseReasoning(messageId) {
  document.querySelectorAll(`[data-message-id="${messageId}"] .reasoning`).forEach((details) => {
    details.open = false;
  });
}

function assistantParts(messageId) {
  return document.getElementById(`parts-${messageId}`);
}

function lastPart(messageId, type) {
  const parts = assistantParts(messageId);
  const last = parts?.lastElementChild;
  return last?.dataset.partType === type ? last : null;
}

function ensureMessageList(prompt) {
  if (messageList) return messageList;
  const scroller = document.getElementById("chat-scroll");
  const anchor = document.getElementById("scroll-anchor");
  if (!scroller) return null;

  document.querySelector(".empty-state")?.remove();

  const header = document.createElement("div");
  header.className = "chat-header";
  header.innerHTML = `
    <p class="kicker">CuteHarness</p>
    <h1>${escapeHtml((prompt || "New conversation").slice(0, 36))}</h1>
  `;

  messageList = document.createElement("div");
  messageList.className = "message-list";
  messageList.id = "message-list";

  scroller.insertBefore(header, anchor);
  scroller.insertBefore(messageList, anchor);
  return messageList;
}

function appendUserMessage(content, attachments = []) {
  const list = ensureMessageList(content);
  if (!list) return null;
  const article = document.createElement("article");
  article.className = "message user-message";
  const stack = document.createElement("div");
  stack.className = "user-message-stack";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = content || "[上传文件]";
  stack.appendChild(bubble);

  if (attachments.length) {
    const attachmentList = document.createElement("div");
    attachmentList.className = "upload-attachments";
    attachments.forEach((attachment) => {
      if (attachment.preview_url) {
        const link = document.createElement("a");
        link.className = "upload-image-link";
        link.href = attachment.preview_url;
        link.target = "_blank";
        link.rel = "noopener";
        const image = document.createElement("img");
        image.src = attachment.preview_url;
        image.alt = attachment.name;
        link.appendChild(image);
        attachmentList.appendChild(link);
        return;
      }

      const file = document.createElement("div");
      file.className = "upload-download upload-download-static";
      file.innerHTML = `
        <span class="upload-file-name">${escapeHtml(attachment.name)}</span>
        <span class="upload-file-meta">${escapeHtml(attachment.mime_type)} · ${attachment.size_bytes} bytes</span>
      `;
      attachmentList.appendChild(file);
    });
    stack.appendChild(attachmentList);
  }

  article.appendChild(stack);
  list.appendChild(article);
  scrollToBottom({ force: true });
  return article;
}

function appendAssistantPlaceholder(messageId) {
  const list = ensureMessageList("");
  if (!list) return null;
  const article = document.createElement("article");
  article.className = "message assistant-message assistant-placeholder";
  article.dataset.messageId = messageId;
  const avatarIndex = Math.floor(Math.random() * 4) + 1;
  article.innerHTML = `
    <div class="assistant-avatar"><img src="/static/avatar/${avatarIndex}.png" alt="AI"></div>
    <div class="assistant-body">
      <div class="assistant-status-row">
        <span class="message-status queued">queued</span>
      </div>
      <div class="assistant-parts" id="parts-${messageId}">
        <div class="answer markdown-body waiting" data-part-type="answer">等待响应...</div>
      </div>
    </div>
  `;
  list.appendChild(article);
  scrollToBottom({ force: true });
  return article;
}
