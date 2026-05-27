// composer.js — 输入框交互、表单提交与错误处理
// 依赖：dom-utils.js（appendUserMessage, appendAssistantPlaceholder, replaceAssistantId, setStatus, markAssistantFailed）
// 依赖：stream.js（startConversationStream）
// 依赖全局变量：form, textarea, button, originalButtonText（由 index.html 内联脚本声明）

function setComposerBusy(isBusy) {
  if (!textarea || !button || !form) return;
  textarea.disabled = isBusy;
  button.disabled = isBusy;
  button.textContent = isBusy ? "发送中" : originalButtonText;
  textarea.placeholder = isBusy ? "等待响应中..." : "发送消息给 CuteHarness";
  form.classList.toggle("disabled", isBusy);
}

function clearSubmitError() {
  document.querySelectorAll(".composer-error").forEach((item) => item.remove());
}

function showSubmitError(message) {
  if (!form) return;
  clearSubmitError();
  const error = document.createElement("p");
  error.className = "composer-error error";
  error.textContent = message || "发送失败";
  form.insertAdjacentElement("afterend", error);
}

function autoResizeTextarea() {
  if (!textarea) return;
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px";
}

function initComposer() {
  if (!textarea || !form) return;

  textarea.addEventListener("input", autoResizeTextarea);

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!textarea.disabled && textarea.value.trim()) {
        form.requestSubmit();
      }
    }
  });

  form.addEventListener("submit", async (event) => {
    if (!window.fetch || !window.EventSource) return;
    event.preventDefault();

    const prompt = textarea.value.trim();
    if (!prompt || textarea.disabled) return;

    clearSubmitError();
    const formData = new FormData(form);
    formData.set("prompt", prompt);
    const submitUrl = form.getAttribute("action");
    const pendingId = `pending-${Date.now()}`;

    textarea.value = "";
    autoResizeTextarea();
    setComposerBusy(true);
    appendUserMessage(prompt);
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
      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(payload.error || "Send failed");
      }

      replaceAssistantId(pendingId, payload.assistant_message.id);
      setStatus(payload.assistant_message.id, payload.assistant_message.status || "queued");
      form.setAttribute("action", `${payload.conversation_url}/messages`);

      if (window.location.pathname !== payload.conversation_url) {
        history.pushState({}, "", payload.conversation_url);
      }

      startConversationStream(payload.conversation_id);
    } catch (error) {
      markAssistantFailed(pendingId, error.message);
      showSubmitError(error.message);
      textarea.value = prompt;
      setComposerBusy(false);
    }
  });
}
