// stream.js — SSE 流式连接管理
// 依赖：dom-utils.js（assistantParts, appendAssistantPlaceholder, setStatus, markAssistantFailed, collapseReasoning, scrollToBottom）
// 依赖：message-parts.js（syncAssistantSnapshot, appendReasoningDelta, appendAnswerDelta, appendToolCall, appendToolMessage）
// 依赖：composer.js（setComposerBusy）
// 依赖全局变量：currentSource（由 index.html 内联脚本声明）

let streamProgress = null;
let streamReconnectTimer = 0;

function codePointLength(value) {
  return Array.from(value || "").length;
}

function streamOptions(conversationId, options) {
  const hasOffsets = ["reasoningOffset", "answerOffset", "toolCount"].some(
    (key) => Object.prototype.hasOwnProperty.call(options, key)
  );
  if (!hasOffsets && streamProgress?.conversationId === conversationId) {
    return streamProgress;
  }

  return {
    conversationId,
    messageId: "",
    reasoningOffset: Number(options.reasoningOffset) || 0,
    answerOffset: Number(options.answerOffset) || 0,
    toolCount: Number(options.toolCount) || 0,
  };
}

function scheduleStreamReconnect(conversationId) {
  if (streamReconnectTimer || !streamProgress || streamProgress.conversationId !== conversationId) {
    return;
  }
  streamReconnectTimer = window.setTimeout(() => {
    streamReconnectTimer = 0;
    if (!currentSource && streamProgress?.conversationId === conversationId) {
      startConversationStream(conversationId);
    }
  }, 250);
}

function startConversationStream(conversationId, options = {}) {
  if (!conversationId) return;
  if (streamReconnectTimer) {
    window.clearTimeout(streamReconnectTimer);
    streamReconnectTimer = 0;
  }
  if (currentSource) {
    currentSource.close();
  }

  streamProgress = streamOptions(conversationId, options);
  const source = new EventSource(
    `/conversations/${conversationId}/stream?reasoning_offset=${streamProgress.reasoningOffset}&answer_offset=${streamProgress.answerOffset}&tool_count=${streamProgress.toolCount}`
  );
  currentSource = source;

  source.addEventListener("assistant", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    if (payload.message_id !== streamProgress.messageId) {
      const isInitialAssistant = !streamProgress.messageId;
      streamProgress.messageId = payload.message_id;
      if (!isInitialAssistant) {
        streamProgress.reasoningOffset = 0;
        streamProgress.answerOffset = 0;
        streamProgress.toolCount = 0;
      }
    }
    if (!assistantParts(payload.message_id)) {
      appendAssistantPlaceholder(payload.message_id);
    }
  });

  source.addEventListener("status", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    setStatus(payload.message_id, payload.status);
  });

  source.addEventListener("snapshot", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    syncAssistantSnapshot(payload.message_id, payload.message);
    const message = payload.message || {};
    streamProgress.reasoningOffset = codePointLength(message.reasoning_content);
    streamProgress.answerOffset = codePointLength(message.content);
    streamProgress.toolCount = Array.isArray(message.tool_messages) ? message.tool_messages.length : 0;
  });

  source.addEventListener("reasoning", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendReasoningDelta(payload.message_id, payload.delta);
    streamProgress.reasoningOffset += codePointLength(payload.delta);
  });

  source.addEventListener("answer", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendAnswerDelta(payload.message_id, payload.delta);
    streamProgress.answerOffset += codePointLength(payload.delta);
  });

  source.addEventListener("tool_call", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendToolCall(payload.message_id, payload.message);
  });

  source.addEventListener("tool_call_result", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendToolMessage(payload.message_id, payload.message);
    streamProgress.toolCount += 1;
  });

  source.addEventListener("context_tokens", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    const target = document.getElementById("context-token-estimate");
    if (!target || typeof payload.estimated_tokens !== "number") return;
    target.textContent = `上下文：约 ${payload.estimated_tokens.toLocaleString("zh-CN")} tokens`;
  });

  source.addEventListener("error", (event) => {
    if (currentSource !== source) return;
    // Native EventSource errors have no data. Close the old URL before retrying
    // with the locally consumed offsets; otherwise the browser retries from 0
    // and the already-rendered answer is appended a second time.
    if (!event.data) {
      source.close();
      currentSource = null;
      scheduleStreamReconnect(conversationId);
      return;
    }
    const payload = JSON.parse(event.data);
    markAssistantFailed(payload.message_id, payload.error);
    source.close();
    if (currentSource === source) {
      currentSource = null;
    }
    setComposerBusy(false);
    if (typeof textarea !== "undefined" && textarea) textarea.focus();
  });

  source.addEventListener("done", (event) => {
    if (currentSource !== source) return;
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    source.close();
    if (currentSource === source) {
      currentSource = null;
    }
    if (payload.message_id && payload.status) {
      setStatus(payload.message_id, payload.status);
    }
    collapseReasoning(payload.message_id);
    document.querySelector(`[data-message-id="${payload.message_id}"] .waiting`)?.remove();
    setComposerBusy(false);
    if (typeof textarea !== "undefined" && textarea) textarea.focus();
  });
}
