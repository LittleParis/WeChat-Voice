"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="voice-token"]').content;
const state = { chats: [], chat: null, kind: "all", offset: 0, total: 0, limit: 50,
  rows: [], request: 0, senderRequest: 0, audioRequest: 0, currentVoice: null,
  preparing: null, job: null, jobRunning: false, connecting: false, noticeTimer: null };
const playIcon = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5a.8.8 0 0 1 1.2-.7l10 6.5a.8.8 0 0 1 0 1.4l-10 6.5a.8.8 0 0 1-1.2-.7z"/></svg>';
const pauseIcon = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 4h3.5v12H5zm6.5 0H15v12h-3.5z"/></svg>';
const audio = $("audio");

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, payload) {
  const options = payload === undefined ? {} : { method: "POST", headers: {
    "Content-Type": "application/json", "X-Voice-Token": token }, body: JSON.stringify(payload) };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作没有完成，请重试。");
  return result;
}

function notify(message, error = false, permanent = false) {
  clearTimeout(state.noticeTimer);
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = false;
  if (!permanent) state.noticeTimer = setTimeout(() => { $("notice").hidden = true; }, error ? 9000 : 5000);
}

function filters() {
  return { chat: state.chat?.id || "", sender: $("sender-select").value,
    since: $("date-since").value, until: $("date-until").value, sort: $("sort-select").value };
}

function renderChats() {
  const search = $("chat-search").value.trim().toLocaleLowerCase();
  const chats = state.chats.filter((chat) => (state.kind === "all" || chat.kind === state.kind)
    && chat.name.toLocaleLowerCase().includes(search));
  const container = $("chat-list");
  container.replaceChildren();
  if (!chats.length) {
    container.append(element("p", "loading-copy", "没有找到会话，试试其他名称或会话类型。"));
    return;
  }
  for (const chat of chats) {
    const button = element("button", `chat-row${state.chat?.id === chat.id ? " selected" : ""}`);
    button.dataset.chat = chat.id;
    button.setAttribute("aria-current", state.chat?.id === chat.id ? "true" : "false");
    button.setAttribute("aria-label", `${chat.name}，${chat.count} 条语音`);
    const avatar = element("span", "chat-avatar", chat.kind === "group" ? "群" : Array.from(chat.name)[0]);
    avatar.setAttribute("aria-hidden", "true");
    const content = element("span", "chat-label");
    const title = element("span", "chat-name", chat.name);
    title.title = chat.name;
    content.append(title, element("span", "chat-caption", `${chat.kind === "group" ? "群聊" : "联系人"} · ${chat.latest_date}`));
    button.append(avatar, content, element("span", "chat-count", chat.count.toLocaleString("zh-CN")));
    container.append(button);
  }
}

async function selectChat(id) {
  state.chat = state.chats.find((chat) => chat.id === id);
  if (!state.chat) return;
  state.offset = 0;
  try { sessionStorage.setItem("voice-chat", id); } catch (_) {}
  $("workspace").classList.remove("show-chats");
  $("chat-title").textContent = state.chat.name;
  $("chat-kind").textContent = `语音档案 / ${state.chat.kind === "group" ? "群聊" : "联系人"}`;
  $("chat-description").textContent = `${state.chat.count.toLocaleString("zh-CN")} 条原始语音 · 按发送时间排列`;
  $("sender-select").replaceChildren(new Option("全部发送人", ""));
  $("sender-select").disabled = true;
  renderChats();
  const sequence = ++state.senderRequest;
  const senderTask = api(`/api/chats/${id}/senders`).then((senders) => {
    if (sequence !== state.senderRequest || state.chat.id !== id) return;
    const repeated = new Map();
    for (const sender of senders) repeated.set(sender.name, (repeated.get(sender.name) || 0) + 1);
    for (const sender of senders) {
      const distinction = repeated.get(sender.name) > 1 ? ` · ${sender.id.slice(-5)}` : "";
      $("sender-select").add(new Option(`${sender.name}${distinction}（${sender.count}）`, sender.id));
    }
    $("sender-select").disabled = false;
    $("chat-description").textContent = `${state.chat.count.toLocaleString("zh-CN")} 条原始语音 · ${senders.length} 位发送人`;
  }).catch((error) => { if (sequence === state.senderRequest) notify(error.message, true); });
  await Promise.all([senderTask, loadVoices()]);
}

function dateHeading(date) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long", timeZone: "Asia/Shanghai" })
    .format(new Date(`${date}T12:00:00+08:00`));
}

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "时长待确认";
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} 秒`;
}

function renderRows() {
  const container = $("voice-list");
  container.replaceChildren();
  if (!state.rows.length) {
    const empty = element("div", "empty");
    empty.append(element("strong", "", "这个范围里没有语音"), element("p", "", "调整发送人或日期，或者清除筛选重新查看。"));
    const reset = element("button", "text-button", "清除筛选");
    reset.addEventListener("click", resetFilters);
    empty.append(reset);
    container.append(empty);
    return;
  }
  let previousDate = "";
  for (const row of state.rows) {
    if (row.date !== previousDate) {
      container.append(element("div", "date-heading", dateHeading(row.date)));
      previousDate = row.date;
    }
    const item = element("article", "voice-row");
    item.dataset.voice = row.id;
    const play = element("button", "row-play");
    play.dataset.play = row.id;
    play.setAttribute("aria-label", `试听 ${row.sender} ${row.date} ${row.time} 的语音`);
    play.innerHTML = playIcon;
    const person = element("div", "voice-info");
    person.append(element("div", "voice-person", row.sender), element("p", "voice-time", row.time));
    const download = element("button", "text-button row-download", "下载");
    download.dataset.download = row.id;
    download.setAttribute("aria-label", `下载 ${row.sender} ${row.date} ${row.time} 的 MP3`);
    item.append(play, person, element("span", "voice-duration", duration(row.seconds)), download);
    container.append(item);
  }
  updatePlayingButtons();
}

function setExportButton() {
  $("export-button").disabled = state.jobRunning || state.connecting || !state.total || !state.chat;
  $("mobile-package-button").disabled = $("export-button").disabled;
  $("export-button").textContent = state.jobRunning ? "正在导出…" : `导出当前 ${state.total.toLocaleString("zh-CN")} 条`;
}

async function loadVoices() {
  if (!state.chat) return;
  const sequence = ++state.request;
  const query = filters();
  $("export-button").disabled = true;
  $("mobile-package-button").disabled = true;
  $("result-count").textContent = "正在筛选语音…";
  const skeleton = element("div", "list-skeleton");
  skeleton.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 4; i++) skeleton.append(element("i"));
  $("voice-list").replaceChildren(skeleton);
  try {
    const response = await api(`/api/voices?${new URLSearchParams({ ...query, offset: state.offset, limit: state.limit })}`);
    if (sequence !== state.request) return;
    state.rows = response.items;
    state.total = response.total;
    $("result-count").textContent = `${state.total.toLocaleString("zh-CN")} 条语音 · ${query.sort === "asc" ? "从旧到新" : "从新到旧"}`;
    const page = Math.floor(state.offset / state.limit) + 1;
    const pages = Math.max(1, Math.ceil(state.total / state.limit));
    $("page-info").textContent = `第 ${page} / ${pages} 页${state.total ? ` · ${state.offset + 1}–${Math.min(state.offset + state.limit, state.total)} 条` : ""}`;
    $("previous-page").disabled = state.offset === 0;
    $("next-page").disabled = state.offset + state.limit >= state.total;
    renderRows();
    $("voice-list").scrollTop = 0;
    setExportButton();
  } catch (error) {
    if (sequence !== state.request) return;
    state.total = 0;
    state.rows = [];
    $("result-count").textContent = "筛选未完成";
    $("voice-list").replaceChildren(element("div", "empty", error.message));
    $("page-info").textContent = "";
    $("previous-page").disabled = $("next-page").disabled = true;
    setExportButton();
  }
}

function resetFilters() {
  $("sender-select").value = "";
  $("date-since").value = "";
  $("date-until").value = "";
  state.offset = 0;
  loadVoices();
}

function updatePlayingButtons() {
  for (const button of document.querySelectorAll("[data-play]")) {
    const selected = state.currentVoice?.id === button.dataset.play;
    const playing = selected && !audio.paused;
    button.innerHTML = playing ? pauseIcon : playIcon;
    button.setAttribute("aria-pressed", playing ? "true" : "false");
    button.disabled = state.preparing === button.dataset.play;
    button.closest(".voice-row").classList.toggle("is-playing", playing);
  }
}

async function playVoice(id) {
  const row = state.rows.find((item) => item.id === id);
  if (!row) return;
  if (state.currentVoice?.id === id) {
    if (audio.paused) audio.play().catch(() => notify("请点击下方播放器的播放按钮。"));
    else audio.pause();
    return;
  }
  const sequence = ++state.audioRequest;
  audio.pause();
  state.preparing = id;
  updatePlayingButtons();
  notify("正在准备这条语音…", false, true);
  try {
    const prepared = await api("/api/prepare", { id });
    if (sequence !== state.audioRequest) return;
    state.currentVoice = row;
    audio.src = prepared.url;
    $("player").hidden = false;
    $("player-title").textContent = `${state.chats.find((chat) => chat.id === row.chat_id)?.name || "语音"} · ${row.sender}`;
    $("player-detail").textContent = `${row.date} ${row.time} · ${duration(row.seconds)}`;
    $("notice").hidden = true;
    try { await audio.play(); } catch (_) { notify("音频已准备好，请点击下方播放器播放。"); }
  } catch (error) {
    if (sequence === state.audioRequest) notify(error.message, true);
  } finally {
    if (sequence === state.audioRequest) state.preparing = null;
    updatePlayingButtons();
  }
}

async function downloadVoice(id, button) {
  button.disabled = true;
  button.textContent = "准备中";
  try {
    const prepared = await api("/api/prepare", { id });
    const response = await fetch(prepared.url);
    if (!response.ok) throw new Error("音频下载未完成，请重新尝试。");
    const objectUrl = URL.createObjectURL(await response.blob());
    const row = state.rows.find((item) => item.id === id);
    const link = element("a");
    link.href = objectUrl;
    const sender = (row?.sender || "语音").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 40);
    link.download = row ? `${row.date}_${row.time.replaceAll(":", "")}_${sender}_${id.slice(0,6)}.mp3` : `语音_${id.slice(0,8)}.mp3`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
    notify("已开始下载 MP3。");
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; button.textContent = "下载"; }
}

async function startExport(mode = "files") {
  if (state.jobRunning || !state.total) return;
  state.jobRunning = true;
  setExportButton();
  $("export-status").hidden = false;
  $("export-status-title").textContent = "正在准备导出…";
  $("export-status-detail").textContent = "导出范围与当前发送人、日期筛选一致。";
  $("open-folder").hidden = $("dismiss-export").hidden = true;
  $("cancel-export").hidden = false;
  $("cancel-export").disabled = true;
  $("export-progress").value = 0;
  try {
    state.job = await api("/api/export", { ...filters(), mode });
    $("cancel-export").disabled = false;
    pollExport();
  } catch (error) {
    state.jobRunning = false;
    $("export-status-title").textContent = "导出未开始";
    $("export-status-detail").textContent = error.message;
    $("cancel-export").hidden = true;
    $("dismiss-export").hidden = false;
    setExportButton();
  }
}

async function pollExport() {
  try {
    const job = await api(`/api/jobs/${state.job.id}`);
    state.job = job;
    $("export-progress").max = job.total || 1;
    $("export-progress").value = job.completed;
    if (["done", "cancelled", "error"].includes(job.state)) {
      state.jobRunning = false;
      $("export-status-title").textContent = job.state === "done" ? (job.mode === "mobile" ? `手机数据包已生成 · ${job.exported} 条语音` : `已导出 ${job.exported} 条语音`) : job.state === "cancelled" ? (job.mode === "mobile" ? "已取消，未生成数据包" : `已取消，保留 ${job.exported || 0} 条结果`) : "导出未完成";
      $("export-status-detail").textContent = job.state === "error" ? job.error : `${job.mode === "mobile" && job.state === "done" ? "将文件夹内的 voicepack.zip 文件传到手机，在语音档案 App 中导入。" : ""}${job.failed ? `${job.failed} 条失败，详情见导出清单。` : ""}保存位置：${job.output}`;
      $("cancel-export").hidden = true;
      $("open-folder").hidden = job.state === "error";
      $("dismiss-export").hidden = false;
      setExportButton();
    } else {
      $("export-status-title").textContent = job.state === "queued" ? "等待导出…" : `正在导出 ${job.completed} / ${job.total} 条`;
      $("export-status-detail").textContent = job.mode === "mobile" ? "按当前筛选范围打包 MP3、会话、发送人和时间。" : "按当前筛选范围保存 MP3、原始语音与时间清单。";
      setTimeout(pollExport, 1000);
    }
  } catch (error) {
    $("export-status-detail").textContent = `进度暂时无法读取：${error.message}，正在重试。`;
    setTimeout(pollExport, 2500);
  }
}

$("chat-search").addEventListener("input", renderChats);
document.querySelectorAll("[data-kind]").forEach((button) => button.addEventListener("click", () => {
  state.kind = button.dataset.kind;
  document.querySelectorAll("[data-kind]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", item === button ? "true" : "false"); });
  renderChats();
}));
$("chat-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-chat]");
  if (button) selectChat(button.dataset.chat);
});
$("filter-form").addEventListener("submit", (event) => event.preventDefault());
for (const id of ["sender-select", "date-since", "date-until", "sort-select"]) {
  $(id).addEventListener("change", () => { state.offset = 0; loadVoices(); });
}
$("reset-filters").addEventListener("click", resetFilters);
$("previous-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadVoices(); });
$("next-page").addEventListener("click", () => { state.offset += state.limit; loadVoices(); });
$("voice-list").addEventListener("click", (event) => {
  const play = event.target.closest("[data-play]");
  const download = event.target.closest("[data-download]");
  if (play) playVoice(play.dataset.play);
  if (download) downloadVoice(download.dataset.download, download);
});
for (const name of ["play", "pause", "ended"]) audio.addEventListener(name, updatePlayingButtons);
audio.addEventListener("error", () => { if (audio.getAttribute("src")) notify("播放器无法打开这条音频，请重新试听或下载后检查。", true); });
$("close-player").addEventListener("click", () => {
  ++state.audioRequest;
  state.preparing = null;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  state.currentVoice = null;
  $("player").hidden = true;
  updatePlayingButtons();
});
$("show-chats").addEventListener("click", () => $("workspace").classList.add("show-chats"));
$("back-to-voices").addEventListener("click", () => $("workspace").classList.remove("show-chats"));
$("export-button").addEventListener("click", () => startExport());
$("mobile-package-button").addEventListener("click", () => startExport("mobile"));
$("cancel-export").addEventListener("click", async () => {
  if (!state.job) return;
  try { await api("/api/cancel", { id: state.job.id }); $("cancel-export").disabled = true; $("export-status-detail").textContent = "正在完成当前文件并停止…"; }
  catch (error) { notify(error.message, true); }
});
$("open-folder").addEventListener("click", () => api("/api/open-folder", { id: state.job.id }).catch((error) => notify(error.message, true)));
$("dismiss-export").addEventListener("click", () => { $("export-status").hidden = true; });

async function init() {
  try {
    const overview = await api("/api/overview");
    state.chats = overview.chats;
    $("library-summary").textContent = `${overview.voice_count.toLocaleString("zh-CN")} 条语音 · ${overview.group_count} 个群聊 · ${overview.chat_count - overview.group_count} 位联系人`;
    let remembered;
    try { remembered = sessionStorage.getItem("voice-chat"); } catch (_) {}
    const first = state.chats.find((chat) => chat.id === remembered) || state.chats.find((chat) => chat.kind === "group") || state.chats[0];
    renderChats();
    if (first) await selectChat(first.id);
    else {
      $("chat-title").textContent = "还没有可浏览的语音";
      $("chat-description").textContent = "连接本机微信，准备语音数据后即可浏览。";
      $("voice-list").replaceChildren(element("div", "empty", "点击上方“连接本机微信”，选择聊天数据目录并读取语音。"));
      $("result-count").textContent = "0 条语音";
    }
  } catch (error) {
    $("chat-title").textContent = "暂时无法打开语音档案";
    $("chat-description").textContent = error.message;
    $("voice-list").replaceChildren(element("div", "empty", "确认本地服务仍在运行，然后刷新页面。"));
    $("chat-list").replaceChildren(element("p", "loading-copy", "本地服务连接失败"));
    $("result-count").textContent = "未连接";
  }
}
init();
