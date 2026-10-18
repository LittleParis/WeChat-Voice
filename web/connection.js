"use strict";

(() => {
  let current = null, checkedPath = "", locations = [], scanSequence = 0, scanning = false;
  let pollTimer = null, handledJob = null, refreshPending = false;

  function expand(open) {
    $("source-setup").hidden = !open;
    $("configure-source").setAttribute("aria-expanded", String(open));
    $("configure-source").textContent = open ? "收起设置" : current?.configured ? "数据源设置" : "连接本机微信";
  }

  function feedback(message, error = false) {
    $("source-feedback").textContent = message;
    $("source-feedback").classList.toggle("error", error);
  }

  function controls() {
    const busy = state.connecting || scanning;
    for (const id of ["source-path", "inspect-source", "detect-source", "source-location"]) $(id).disabled = busy;
    $("source-account").disabled = busy || !checkedPath;
    $("connect-source").disabled = busy || !checkedPath || !$("source-account").value;
    $("connect-source").textContent = state.connecting ? "正在处理…" : "连接并读取语音";
    $("refresh-source").disabled = busy || state.jobRunning;
    setExportButton();
  }

  function renderStatus(value) {
    current = value;
    const job = value.job;
    state.connecting = job?.state === "running";
    $("source-label").textContent = value.configured ? "已连接本机微信" : "本机微信数据";
    $("source-detail").textContent = value.configured
      ? `${value.account} · 更新于 ${new Date(value.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}`
      : "选择聊天数据目录，即可读取原始语音。";
    $("refresh-source").hidden = !value.configured;
    $("source-detail").title = value.source || "";
    const progress = $("connection-progress");
    progress.hidden = !job;
    if (job) {
      progress.classList.toggle("error", job.state === "error");
      $("connection-message").textContent = job.message + (job.stage === "decrypting" && job.total ? ` ${job.completed} / ${job.total}` : "");
      $("cancel-connection").hidden = !state.connecting;
      $("cancel-connection").disabled = false;
      const steps = ["checking", "key", "decrypting", "indexing"];
      const index = steps.indexOf(["waiting_exit", "waiting_start", "capturing"].includes(job.stage) ? "key" : job.stage);
      for (const node of document.querySelectorAll("[data-step]")) {
        const position = steps.indexOf(node.dataset.step);
        node.classList.toggle("current", state.connecting && position === index);
        node.classList.toggle("complete", job.state === "done" || (index >= 0 && position < index));
        if (state.connecting && position === index) node.setAttribute("aria-current", "step");
        else node.removeAttribute("aria-current");
      }
      if (job.stage === "decrypting" && job.total) {
        $("connection-meter").max = job.total;
        $("connection-meter").value = job.completed;
      } else if (state.connecting) $("connection-meter").removeAttribute("value");
      else { $("connection-meter").max = 1; $("connection-meter").value = job.state === "done" ? 1 : 0; }
    }
    controls();
    expand(!$("source-setup").hidden);
  }

  function acceptDirectory(result) {
    checkedPath = result.path;
    $("source-path").value = result.path;
    $("source-account").replaceChildren(new Option("请选择微信账号", ""));
    for (const account of result.accounts) $("source-account").add(new Option(account.name, account.id));
    if (result.accounts.length === 1) $("source-account").value = result.accounts[0].id;
    else if (result.accounts.some((item) => item.id === current?.account_id)) $("source-account").value = current.account_id;
    feedback(`找到 ${result.accounts.length} 个账号。${result.accounts.length > 1 ? "请选择要读取的账号。" : "点击“连接并读取语音”继续。"}`);
  }

  async function inspect() {
    const sequence = ++scanSequence;
    scanning = true; checkedPath = ""; controls(); feedback("正在检查目录…");
    try {
      const result = await api("/api/connection/inspect", { path: $("source-path").value });
      if (sequence === scanSequence) acceptDirectory(result);
    } catch (error) { if (sequence === scanSequence) feedback(error.message, true); }
    finally { if (sequence === scanSequence) { scanning = false; controls(); } }
  }

  async function detect() {
    const sequence = ++scanSequence;
    scanning = true; checkedPath = ""; controls(); feedback("正在检查常用保存位置…");
    $("detected-locations").hidden = true;
    try {
      const result = await api("/api/connection/detect", {});
      if (sequence !== scanSequence) return;
      locations = result.locations;
      if (!locations.length) { feedback("未在常用位置找到数据。请在电脑版微信设置中查看保存位置，填写完整目录后检查。", true); return; }
      $("source-location").replaceChildren(new Option("请选择数据目录", ""));
      locations.forEach((location, index) => $("source-location").add(new Option(location.path, String(index))));
      if (locations.length === 1) acceptDirectory(locations[0]);
      else {
        $("detected-locations").hidden = false;
        $("source-account").replaceChildren(new Option("先选择数据目录", ""));
        feedback(`找到 ${locations.length} 个数据目录，请先选择。`);
      }
    } catch (error) { if (sequence === scanSequence) feedback(error.message, true); }
    finally { if (sequence === scanSequence) { scanning = false; controls(); } }
  }

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const result = await api("/api/connection");
      renderStatus(result);
      const job = result.job;
      if (job?.state === "running") pollTimer = setTimeout(poll, 900);
      else if (refreshPending && job?.state === "done" && handledJob !== job.id) {
        handledJob = job.id; refreshPending = false;
        $("close-player").click();
        ++state.request; ++state.senderRequest;
        state.chat = null; state.rows = []; state.total = 0;
        $("date-since").value = ""; $("date-until").value = "";
        await init();
        notify(job.message);
      } else if (job?.state === "error" || job?.state === "cancelled") refreshPending = false;
    } catch (error) {
      $("connection-message").textContent = `连接进度暂时无法读取，正在重试。${error.message}`;
      pollTimer = setTimeout(poll, 2500);
    }
  }

  async function connect(path, account) {
    if (state.jobRunning) { feedback("请先完成或取消正在进行的导出。", true); return; }
    expand(true);
    state.connecting = true; controls();
    try {
      const result = await api("/api/connection/start", { path, account });
      refreshPending = true;
      renderStatus(result);
      poll();
    } catch (error) { state.connecting = false; controls(); feedback(error.message, true); }
  }

  $("configure-source").addEventListener("click", () => {
    const opening = $("source-setup").hidden;
    expand(opening);
    if (opening && !$("source-path").value && !state.connecting) detect();
  });
  $("close-setup").addEventListener("click", () => expand(false));
  $("detect-source").addEventListener("click", detect);
  $("inspect-source").addEventListener("click", inspect);
  $("source-location").addEventListener("change", () => {
    const value = $("source-location").value;
    checkedPath = "";
    if (value !== "") acceptDirectory(locations[Number(value)]);
    controls();
  });
  $("source-path").addEventListener("input", () => {
    ++scanSequence; checkedPath = "";
    $("source-account").replaceChildren(new Option("目录已更改，请重新检查", ""));
    $("detected-locations").hidden = true;
    feedback("目录已更改，请点击“检查目录”。"); controls();
  });
  $("source-account").addEventListener("change", controls);
  $("source-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!checkedPath || !$("source-account").value || state.connecting) return;
    connect(checkedPath, $("source-account").value);
  });
  $("refresh-source").addEventListener("click", () => connect(current.source, current.account_id));
  $("cancel-connection").addEventListener("click", async () => {
    $("cancel-connection").disabled = true;
    try { renderStatus(await api("/api/connection/cancel", {})); }
    catch (error) { feedback(error.message, true); $("cancel-connection").disabled = false; }
  });

  async function setup() {
    try {
      const result = await api("/api/connection");
      renderStatus(result);
      if (result.configured) {
        $("source-path").value = result.source;
        await inspect();
      }
      if (result.job?.state === "running") { expand(true); refreshPending = true; poll(); }
      else if (!result.configured) { expand(true); detect(); }
    } catch (error) {
      $("source-detail").textContent = "连接状态读取失败，请刷新页面。";
      feedback(error.message, true);
    }
  }
  setup();
})();
