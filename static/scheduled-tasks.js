(() => {
  const type = document.querySelector("#schedule-type");
  const value = document.querySelector("#schedule-value");
  const help = document.querySelector("#schedule-help");
  if (!type || !value || !help) return;

  const hints = {
    once: ["datetime-local", "例如 2026-12-01 09:30", "填写未来的执行日期和时间。"],
    interval: ["text", "例如 30s、5m、2h、1d", "支持秒、分钟、小时和天。"],
    cron: ["text", "例如 0 9 * * 1-5", "五段式 Cron：分钟 小时 日期 月份 星期。"],
    daily: ["time", "例如 09:00", "兼容旧格式：每天在这个时间执行。"],
    interval_minutes: ["number", "例如 30", "兼容旧格式：间隔分钟数。"],
  };

  function updateScheduleHint() {
    const config = hints[type.value] || hints.cron;
    value.type = config[0];
    value.placeholder = config[1];
    help.textContent = config[2];
    value.min = type.value === "interval_minutes" ? "1" : "";
  }

  type.addEventListener("change", updateScheduleHint);
  updateScheduleHint();
})();
