// composer.js - input, attachments, form submit, and error handling

let attachedFiles = [];

function setComposerBusy(isBusy) {
  if (!textarea || !button || !form) return;
  textarea.disabled = isBusy;
  if (typeof fileInput !== "undefined" && fileInput) fileInput.disabled = isBusy;
  if (typeof fileButton !== "undefined" && fileButton) fileButton.disabled = isBusy;
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
    button.textContent = originalButtonText;
  }
  textarea.placeholder = isBusy ? "等待响应中..." : "发送消息给 CuteHarness";
  form.classList.toggle("disabled", isBusy);
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
    if (currentSource) {
      currentSource.close();
      currentSource = null;
    }
    setComposerBusy(false);
    if (textarea) textarea.focus();
  } catch (error) {
    showSubmitError(error.message);
    if (button) button.disabled = false;
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
    remove.textContent = "x";
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
  Array.from(files || []).forEach((file) => {
    attachedFiles.push({
      file,
      previewUrl: file.type && file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
    });
  });
  if (fileInput) fileInput.value = "";
  renderAttachedFiles();
}

function initComposer() {
  if (!textarea || !form) return;

  textarea.addEventListener("input", autoResizeTextarea);

  if (fileButton && fileInput) {
    fileButton.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => addFiles(fileInput.files));
  }

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
