// stream.js — SSE 流式连接管理
// 依赖：dom-utils.js（assistantParts, appendAssistantPlaceholder, setStatus, markAssistantFailed, collapseReasoning, scrollToBottom）
// 依赖：message-parts.js（appendReasoningDelta, appendAnswerDelta, appendToolCall, appendToolMessage）
// 依赖：composer.js（setComposerBusy）
// 依赖全局变量：currentSource（由 index.html 内联脚本声明）

function startConversationStream(conversationId, options = {}) {
  if (!conversationId) return;
  if (currentSource) {
    currentSource.close();
  }

  const reasoningOffset = options.reasoningOffset || 0;
  const answerOffset = options.answerOffset || 0;
  const toolCount = options.toolCount || 0;
  const source = new EventSource(
    `/conversations/${conversationId}/stream?reasoning_offset=${reasoningOffset}&answer_offset=${answerOffset}&tool_count=${toolCount}`
  );
  currentSource = source;

  source.addEventListener("assistant", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    if (!assistantParts(payload.message_id)) {
      appendAssistantPlaceholder(payload.message_id);
    }
  });

  source.addEventListener("status", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    setStatus(payload.message_id, payload.status);
  });

  source.addEventListener("reasoning", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendReasoningDelta(payload.message_id, payload.delta);
  });

  source.addEventListener("answer", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendAnswerDelta(payload.message_id, payload.delta);
  });

  source.addEventListener("tool_call", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendToolCall(payload.message_id, payload.message);
  });

  source.addEventListener("tool_call_result", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    appendToolMessage(payload.message_id, payload.message);
  });

  source.addEventListener("context_tokens", (event) => {
    if (!event.data) return;
    const payload = JSON.parse(event.data);
    const target = document.getElementById("context-token-estimate");
    if (!target || typeof payload.estimated_tokens !== "number") return;
    target.textContent = `上下文：约 ${payload.estimated_tokens.toLocaleString("zh-CN")} tokens`;
  });

  source.addEventListener("error", (event) => {
    if (!event.data) return;
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
