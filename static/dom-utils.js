// dom-utils.js — DOM 工具函数
// 依赖：markdown.js（escapeHtml, renderMarkdown）
// 依赖全局变量：messageList（由 index.html 内联脚本声明）

function renderAnswer(target) {
  if (!target) return;
  if (target.dataset.raw === undefined) {
    target.dataset.raw = target.textContent;
  }
  const raw = target.dataset.raw;
  if (target.dataset.renderedRaw === raw) return;

  patchAnswerDom(target, renderMarkdown(raw));
  target.dataset.renderedRaw = raw;
}

function patchAnswerDom(target, html) {
  const template = document.createElement("template");
  template.innerHTML = html;

  const currentScroller = chatScroller();
  const shouldPreserveScroll = currentScroller && !isChatNearBottom(currentScroller);
  const scrollTop = currentScroller?.scrollTop || 0;

  const nextChildren = Array.from(template.content.children);
  for (let index = 0; index < nextChildren.length; index += 1) {
    const nextChild = nextChildren[index];
    const currentChild = target.children[index];

    if (!currentChild || !canReuseAnswerBlock(currentChild, nextChild)) {
      target.insertBefore(nextChild, currentChild || null);
      continue;
    }

    if (nextChild.classList.contains("table-scroll")) {
      patchTableBlock(currentChild, nextChild);
    } else if (currentChild.outerHTML !== nextChild.outerHTML) {
      // 普通段落/列表只替换发生变化的块，不触碰已经完成的表格节点。
      currentChild.replaceWith(nextChild);
    }
  }

  while (target.children.length > nextChildren.length) {
    target.lastElementChild.remove();
  }

  if (shouldPreserveScroll && currentScroller) {
    currentScroller.scrollTop = scrollTop;
  }
}

function canReuseAnswerBlock(current, next) {
  return current.nodeName === next.nodeName && current.className === next.className;
}

function patchTableBlock(currentBlock, nextBlock) {
  const currentTable = currentBlock.querySelector("table");
  const nextTable = nextBlock.querySelector("table");
  if (!currentTable || !nextTable) {
    currentBlock.replaceWith(nextBlock);
    return;
  }

  patchTableSection(currentTable.tHead, nextTable.tHead);
  patchTableSection(currentTable.tBodies[0], nextTable.tBodies[0]);
}

function patchTableSection(currentSection, nextSection) {
  if (!currentSection || !nextSection) return;

  const nextRows = Array.from(nextSection.rows);
  for (let index = 0; index < nextRows.length; index += 1) {
    const nextRow = nextRows[index];
    const currentRow = currentSection.rows[index];
    if (!currentRow) {
      currentSection.appendChild(nextRow);
      continue;
    }
    if (currentRow.cells.length !== nextRow.cells.length) {
      currentRow.replaceWith(nextRow);
      continue;
    }

    for (let cellIndex = 0; cellIndex < nextRow.cells.length; cellIndex += 1) {
      const currentCell = currentRow.cells[cellIndex];
      const nextCell = nextRow.cells[cellIndex];
      if (currentCell.innerHTML !== nextCell.innerHTML) {
        currentCell.innerHTML = nextCell.innerHTML;
      }
    }
  }

  while (currentSection.rows.length > nextRows.length) {
    currentSection.lastElementChild.remove();
  }
}

const CHAT_BOTTOM_THRESHOLD = 80;
let chatAutoFollow = true;
let chatScrollTrackingReady = false;
let pendingChatScrollFrame = 0;

function chatScroller() {
  return document.getElementById("chat-scroll");
}

function chatMaxScrollTop(scroller) {
  return Math.max(0, scroller.scrollHeight - scroller.clientHeight);
}

function isChatNearBottom(scroller = chatScroller()) {
  if (!scroller) return true;
  return chatMaxScrollTop(scroller) - scroller.scrollTop <= CHAT_BOTTOM_THRESHOLD;
}

function cancelChatScrollAnimation() {
  if (pendingChatScrollFrame) {
    cancelAnimationFrame(pendingChatScrollFrame);
    pendingChatScrollFrame = 0;
  }
}

function scheduleScrollToBottom(options = {}) {
  if (options.force || options.instant) {
    scrollToBottom(options);
    return;
  }
  if (pendingChatScrollFrame) return;

  // 流式内容可能在很短时间内增加很多高度。按帧合并更新后直接定位，
  // 避免使用固定步长动画导致滚动位置持续落后于回答内容。
  pendingChatScrollFrame = requestAnimationFrame(() => {
    pendingChatScrollFrame = 0;
    scrollToBottom(options);
  });
}

function initChatScrollTracking() {
  if (chatScrollTrackingReady) return;
  const scroller = document.getElementById("chat-scroll");
  if (!scroller) return;
  chatScrollTrackingReady = true;

  scroller.addEventListener("scroll", () => {
    chatAutoFollow = isChatNearBottom(scroller);
  });

  ["wheel", "touchstart", "pointerdown"].forEach((eventName) => {
    scroller.addEventListener(
      eventName,
      () => {
        cancelChatScrollAnimation();
        chatAutoFollow = isChatNearBottom(scroller);
      },
      { passive: true }
    );
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
  if (options.force) {
    // 新消息提交后重新开启自动跟随，即使用户此前停留在历史位置。
    chatAutoFollow = true;
  }
  if (!options.force && !chatAutoFollow && !isChatNearBottom(scroller)) return;

  // force/instant 调用可能需要取消尚未执行的普通按帧请求，避免旧请求
  // 在新内容渲染前再次执行并覆盖用户刚刚的滚动位置。
  cancelChatScrollAnimation();
  scroller.scrollTop = chatMaxScrollTop(scroller);
  chatAutoFollow = true;
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
  ensureContextTokenEstimate();
  return messageList;
}

function ensureContextTokenEstimate() {
  if (document.getElementById("context-token-estimate")) return;
  const composerWrap = document.querySelector(".composer-wrap");
  const composer = composerWrap?.querySelector(".composer");
  if (!composerWrap || !composer) return;

  const estimate = document.createElement("p");
  estimate.className = "context-token-estimate";
  estimate.id = "context-token-estimate";
  estimate.textContent = "上下文：计算中…";
  composer.insertAdjacentElement("afterend", estimate);
  composerWrap.classList.add("has-context-token-estimate");
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
