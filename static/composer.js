// composer.js - input, attachments, form submit, and error handling

let attachedFiles = [];
const MAX_IMAGE_ATTACHMENTS = 8;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

// 忙碌状态计时器（纯前端展示，刷新后从 0 重新计时）
let busyTimerInterval = null;
let busyTimerStart = 0;

function busyIndicatorElements() {
  const indicator = document.querySelector(".composer-busy");
  const timerText = indicator ? indicator.querySelector(".composer-busy-timer") : null;
  return { indicator, timerText };
}

function formatBusyDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${pad(minutes)}:${pad(rest)}`;
}

function updateBusyTimer() {
  const { timerText } = busyIndicatorElements();
  if (!timerText) return;
  timerText.textContent = formatBusyDuration((Date.now() - busyTimerStart) / 1000);
}

function startBusyIndicator() {
  const { indicator } = busyIndicatorElements();
  if (indicator) indicator.hidden = false;
  busyTimerStart = Date.now();
  updateBusyTimer();
  if (!busyTimerInterval) {
    busyTimerInterval = window.setInterval(updateBusyTimer, 1000);
  }
}

function stopBusyIndicator() {
  if (busyTimerInterval) {
    window.clearInterval(busyTimerInterval);
    busyTimerInterval = null;
  }
  busyTimerStart = 0;
  const { indicator } = busyIndicatorElements();
  if (indicator) indicator.hidden = true;
}

function setComposerBusy(isBusy) {
  if (!textarea || !button || !form) return;
  textarea.disabled = isBusy;
  if (typeof fileInput !== "undefined" && fileInput) fileInput.disabled = isBusy;
  if (typeof fileButton !== "undefined" && fileButton) fileButton.disabled = isBusy;
  if (typeof uploadButton !== "undefined" && uploadButton) uploadButton.disabled = isBusy;
  if (isBusy) {
    const canStop = Boolean(activeConversationId());
    button.type = canStop ? "button" : "submit";
    button.disabled = !canStop;
    button.classList.toggle("stop-button", canStop);
    button.textContent = canStop ? "\u505c\u6b62" : "\u53d1\u9001\u4e2d";
  } else {
    button.type = "submit";
    button.disabled = false;
    button.classList.remove("stop-button");
    // 页面在任务进行中打开时，服务端的初始文字是“停止”；空闲时必须固定恢复为“发送”。
    button.textContent = "发送";
  }
  textarea.placeholder = isBusy ? "等待响应中..." : "发送消息给 CuteHarness";
  form.classList.toggle("disabled", isBusy);
  form.classList.toggle("busy", isBusy);
  if (isBusy) {
    startBusyIndicator();
  } else {
    stopBusyIndicator();
  }
  updateCompressionButtonState();
}

function updateCompressionButtonState() {
  if (typeof compressButton === "undefined" || !compressButton || !form) return;
  const compressed = form.dataset.toolsCompressed === "true";
  const hasConversation = Boolean(activeConversationId());
  compressButton.disabled = !hasConversation || Boolean(textarea?.disabled);
  compressButton.textContent = compressed ? "再次压缩" : "压缩对话";
}

function setComposerMenuOpen(isOpen) {
  if (!composerMenu || !fileButton) return;
  composerMenu.hidden = !isOpen;
  fileButton.setAttribute("aria-expanded", isOpen ? "true" : "false");
}

function showComposerNotice(message) {
  if (!form) return;
  document.querySelectorAll(".composer-notice").forEach((item) => item.remove());
  const notice = document.createElement("p");
  notice.className = "composer-notice";
  notice.textContent = message;
  form.insertAdjacentElement("afterend", notice);
}

function clearSubmitError() {
  document.querySelectorAll(".composer-error").forEach((item) => item.remove());
}

function setActiveConversationId(conversationId) {
  if (form && conversationId) {
    form.dataset.conversationId = conversationId;
    if (textarea?.disabled && button) {
      button.type = "button";
      button.disabled = false;
      button.classList.add("stop-button");
      button.textContent = "\u505c\u6b62";
    }
    updateCompressionButtonState();
  }
}

function activeConversationId() {
  return form?.dataset.conversationId || "";
}

function showSubmitError(message) {
  if (!form) return;
  clearSubmitError();
  const error = document.createElement("p");
  error.className = "composer-error error";
  error.textContent = message || "发送失败";
  form.insertAdjacentElement("afterend", error);
}

function detailToMessage(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
        const message = item.msg || item.message || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      })
      .join("; ");
  }
  return detail.message || detail.msg || JSON.stringify(detail);
}

async function responseErrorMessage(response) {
  const status = `${response.status} ${response.statusText || ""}`.trim();
  if (response.status === 413) {
    return `上传文件太大，服务器 Nginx 拒绝了请求。请调高 Nginx 的 client_max_body_size 后重试。(${status})`;
  }

  const payload = await response.clone().json().catch(() => null);
  if (payload) {
    const message = payload.error || detailToMessage(payload.detail) || detailToMessage(payload.message);
    if (message) return `${message} (${status})`;
  }

  const text = (await response.text().catch(() => "")).trim();
  if (text) return `${text} (${status})`;
  return `Send failed (${status})`;
}

async function stopCurrentConversation() {
  const conversationId = activeConversationId();
  if (!conversationId || !window.fetch) return;

  clearSubmitError();
  if (button) button.disabled = true;

  try {
    const response = await fetch(`/conversations/${conversationId}/cancel`, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response));
    }

    const payload = await response.json().catch(() => ({}));
    if (payload.message_id) {
      setStatus(payload.message_id, payload.status || "cancelled");
      collapseReasoning(payload.message_id);
      document.querySelector(`[data-message-id="${payload.message_id}"] .waiting`)?.remove();
    }
    stopConversationStream();
    setComposerBusy(false);
    if (textarea) textarea.focus();
  } catch (error) {
    showSubmitError(error.message);
    if (button) button.disabled = false;
  }
}

async function compressCurrentConversation() {
  const conversationId = activeConversationId();
  if (!conversationId || !compressButton || compressButton.disabled) return;

  clearSubmitError();
  setComposerMenuOpen(false);
  compressButton.disabled = true;
  compressButton.textContent = "压缩中…";
  try {
    const response = await fetch(`/conversations/${conversationId}/compress`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (!response.ok) throw new Error(await responseErrorMessage(response));
    const payload = await response.json();
    form.dataset.toolsCompressed = "true";
    updateCompressionButtonState();
    setContextTokenEstimate(payload.estimated_tokens);
    showComposerNotice("已压缩当前历史工具调用；之后新产生的工具调用会保留，直到再次压缩");
  } catch (error) {
    updateCompressionButtonState();
    showSubmitError(error.message);
  }
}

function autoResizeTextarea() {
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px";
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function fileSummary(file) {
  return {
    name: file.name,
    mime_type: file.type || "application/octet-stream",
    size_bytes: file.size,
    preview_url: file.type && file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
  };
}

function releaseFileSummaries(summaries) {
  summaries.forEach((item) => {
    if (item.preview_url) URL.revokeObjectURL(item.preview_url);
  });
}

function clearAttachedFiles() {
  attachedFiles.forEach((item) => {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  });
  attachedFiles = [];
  if (fileInput) fileInput.value = "";
  renderAttachedFiles();
}

function renderAttachedFiles() {
  if (!selectedFiles) return;
  selectedFiles.innerHTML = "";
  selectedFiles.classList.toggle("has-files", attachedFiles.length > 0);

  attachedFiles.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "selected-file";

    if (item.previewUrl) {
      row.classList.add("selected-image");
      const image = document.createElement("img");
      image.className = "selected-file-preview";
      image.src = item.previewUrl;
      image.alt = item.file.name;
      row.appendChild(image);
    } else {
      const icon = document.createElement("span");
      icon.className = "selected-file-icon";
      icon.textContent = "FILE";
      row.appendChild(icon);
    }

    const meta = document.createElement("div");
    meta.className = "selected-file-meta";
    const name = document.createElement("span");
    name.className = "selected-file-name";
    name.textContent = item.file.name;
    const details = document.createElement("span");
    details.className = "selected-file-details";
    details.textContent = `${item.file.type || "application/octet-stream"} · ${formatFileSize(item.file.size)}`;
    meta.append(name, details);
    row.appendChild(meta);

    const remove = document.createElement("button");
    remove.className = "selected-file-remove";
    remove.type = "button";
    remove.title = "Remove file";
    remove.setAttribute("aria-label", `Remove ${item.file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      const [removed] = attachedFiles.splice(index, 1);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      renderAttachedFiles();
    });
    row.appendChild(remove);

    selectedFiles.appendChild(row);
  });
}

function addFiles(files) {
  const incoming = Array.from(files || []);
  const currentImageCount = attachedFiles.filter((item) => item.file.type?.startsWith("image/")).length;
  let imageCount = currentImageCount;
  const accepted = [];

  incoming.forEach((file) => {
    const isImage = file.type?.startsWith("image/");
    if (isImage && file.size > MAX_IMAGE_BYTES) {
      showSubmitError(`图片 ${file.name || "未命名图片"} 超过 20 MB 限制`);
      return;
    }
    if (isImage && imageCount >= MAX_IMAGE_ATTACHMENTS) {
      showSubmitError(`最多同时添加 ${MAX_IMAGE_ATTACHMENTS} 张图片`);
      return;
    }
    if (isImage) imageCount += 1;
    accepted.push(file);
  });

  accepted.forEach((file) => {
    attachedFiles.push({
      file,
      previewUrl: file.type && file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
    });
  });
  if (fileInput) fileInput.value = "";
  renderAttachedFiles();
}

function clipboardImageFiles(event) {
  const items = Array.from(event.clipboardData?.items || []);
  return items
    .filter((item) => item.kind === "file" && item.type?.startsWith("image/"))
    .map((item, index) => {
      const file = item.getAsFile();
      if (!file) return null;
      if (file.name) return file;
      const extension = file.type.split("/")[1] || "png";
      return new File([file], `pasted-image-${Date.now()}-${index}.${extension}`, {
        type: file.type || "image/png",
        lastModified: Date.now(),
      });
    })
    .filter(Boolean);
}

function initComposer() {
  if (!textarea || !form) return;

  // 浏览器从 bfcache 恢复页面时会保留禁用的输入框，但不会保留 SSE 连接。
  // 重新订阅后，已完成的会话会立即收到 done 事件并恢复发送按钮。
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !textarea.disabled) return;

    const conversationId = activeConversationId();
    if (conversationId) startConversationStream(conversationId);
  });

  textarea.addEventListener("input", autoResizeTextarea);
  textarea.addEventListener("paste", (event) => {
    const images = clipboardImageFiles(event);
    if (!images.length) return;
    event.preventDefault();
    addFiles(images);
  });

  if (fileButton && composerMenu) {
    fileButton.addEventListener("click", () => {
      if (fileButton.disabled) return;
      setComposerMenuOpen(composerMenu.hidden);
    });
  }

  if (uploadButton && fileInput) {
    uploadButton.addEventListener("click", () => {
      setComposerMenuOpen(false);
      fileInput.click();
    });
    fileInput.addEventListener("change", () => addFiles(fileInput.files));
  }

  compressButton?.addEventListener("click", compressCurrentConversation);

  document.addEventListener("click", (event) => {
    if (!composerMenu?.hidden && !event.target.closest(".composer-menu-wrap")) {
      setComposerMenuOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setComposerMenuOpen(false);
  });

  updateCompressionButtonState();

  button?.addEventListener("click", (event) => {
    if (!textarea.disabled) return;
    event.preventDefault();
    stopCurrentConversation();
  });

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!textarea.disabled && (textarea.value.trim() || attachedFiles.length)) {
        form.requestSubmit();
      }
    }
  });

  form.addEventListener("submit", async (event) => {
    if (!window.fetch || !window.EventSource) return;
    event.preventDefault();

    const prompt = textarea.value.trim();
    if ((!prompt && !attachedFiles.length) || textarea.disabled) return;

    clearSubmitError();
    const submitUrl = form.getAttribute("action");
    const pendingId = `pending-${Date.now()}`;
    const optimisticFiles = attachedFiles.map((item) => fileSummary(item.file));
    const formData = new FormData();
    formData.set("prompt", prompt);
    attachedFiles.forEach((item) => formData.append("files", item.file, item.file.name));

    textarea.value = "";
    autoResizeTextarea();
    setComposerBusy(true);
    appendUserMessage(prompt, optimisticFiles);
    appendAssistantPlaceholder(pendingId);

    try {
      const response = await fetch(submitUrl, {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }

      const payload = await response.json().catch(() => ({}));

      releaseFileSummaries(optimisticFiles);
      clearAttachedFiles();
      replaceAssistantId(pendingId, payload.assistant_message.id);
      setStatus(payload.assistant_message.id, payload.assistant_message.status || "queued");
      setContextTokenEstimate(payload.estimated_tokens);
      form.setAttribute("action", `${payload.conversation_url}/messages`);
      setActiveConversationId(payload.conversation_id);

      if (window.location.pathname !== payload.conversation_url) {
        history.pushState({}, "", payload.conversation_url);
      }

      startConversationStream(payload.conversation_id);
    } catch (error) {
      releaseFileSummaries(optimisticFiles);
      markAssistantFailed(pendingId, error.message);
      showSubmitError(error.message);
      textarea.value = prompt;
      setComposerBusy(false);
    }
  });
}
