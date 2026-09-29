// hindsight 테스트 콘솔. 프레임워크 없이 fetch + DOM만 쓴다.
// 서버에서 온 문자열은 전부 textContent로만 넣는다 (innerHTML 금지).
"use strict";

const $ = (sel) => document.querySelector(sel);
const STEP_MS = { hourly: 3600e3, daily: 86400e3 };

const state = {
  token: null,
  email: null,
  seriesList: [],
  series: null,
  summary: null,
  methods: [],
  models: [],
  selectedModelId: null,
  runs: [],
  selectedRun: null,
  pollTimer: null,
};

// ------------------------------------------------------------------ utils
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const pad = (n) => String(n).padStart(2, "0");
function fmtTs(iso, { year = false } = {}) {
  if (!iso) return "-";
  const d = new Date(iso);
  const date = `${year ? d.getUTCFullYear() + "-" : ""}${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  return `${date} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
const toIso = (ms) => new Date(ms).toISOString().replace(".000Z", "Z");
const num = (v, d = 3) => (v === null || v === undefined ? "-" : Number(v).toFixed(d));
const qs = (obj) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else p.append(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : "";
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* 저장소가 막혀 있어도 동작은 한다 */ }
  return null;
}

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), kind === "error" ? 5000 : 2500);
}

function errText(res) {
  const d = res.data;
  if (d && typeof d === "object") {
    if (Array.isArray(d.detail)) return d.detail.map((e) => `${(e.loc || []).join(".")}: ${e.msg}`).join(" / ");
    return `${d.code ? `[${d.code}] ` : ""}${d.detail ?? JSON.stringify(d)}`;
  }
  return `HTTP ${res.status}`;
}

async function withBusy(button, fn) {
  if (button) button.disabled = true;
  try { return await fn(); } finally { if (button) button.disabled = false; }
}

// ------------------------------------------------------------------ API + 로그
const logEntries = [];

async function api(method, path, body, opts = {}) {
  const token = "token" in opts ? opts.token : state.token;
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (opts.form) payload = new URLSearchParams(body);
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let res, data = null;
  const started = performance.now();
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (e) {
    addLog({ method, path, status: 0, req: body, res: String(e), ms: 0 });
    return { ok: false, status: 0, data: { detail: `서버에 연결할 수 없습니다: ${e}` } };
  }
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  addLog({ method, path, status: res.status, req: body, res: data, ms: Math.round(performance.now() - started) });

  if (res.status === 401 && token && token === state.token && !opts.allow401) logout("세션이 만료됐습니다. 다시 로그인하세요.");
  return { ok: res.ok, status: res.status, data };
}

function redact(body) {
  if (!body || typeof body !== "object") return body;
  return "password" in body ? { ...body, password: "••••••" } : body;
}

function addLog(entry) {
  entry.time = new Date();
  entry.req = redact(entry.req);
  logEntries.unshift(entry);
  if (logEntries.length > 300) logEntries.pop();
  renderLog();
}

function renderLog() {
  const onlyErr = $("#log-errors-only").checked;
  const list = $("#log-list");
  list.replaceChildren(
    ...logEntries
      .filter((e) => !onlyErr || e.status === 0 || e.status >= 400)
      .slice(0, 150)
      .map((e) => {
        const ok = e.status > 0 && e.status < 400;
        const code = e.res && typeof e.res === "object" && e.res.code ? e.res.code : null;
        const detail = [];
        if (e.req !== undefined) detail.push(`요청:\n${JSON.stringify(e.req, null, 2)}`);
        detail.push(`응답:\n${typeof e.res === "string" ? e.res : JSON.stringify(e.res, null, 2)}`);
        return h("li", {},
          h("details", {},
            h("summary", {},
              h("span", { class: "muted" }, e.time.toLocaleTimeString()),
              h("span", { class: `st ${ok ? "ok" : "err"}` }, `${ok ? "✓" : "✗"} ${e.status || "ERR"}`),
              h("span", {}, `${e.method} ${e.path}`),
              code && h("span", { class: "code" }, code),
              h("span", { class: "muted" }, `${e.ms}ms`)),
            h("pre", {}, detail.join("\n\n"))));
      }),
  );
}

// ------------------------------------------------------------------ 인증
function setSession(token, email) {
  state.token = token;
  state.email = email;
  storage("hindsight.token", token);
  storage("hindsight.email", email);
}

function logout(message) {
  setSession(null, null);
  clearTimeout(state.pollTimer);
  showAuth();
  if (message) toast(message, "error");
}

function showAuth() {
  $("#auth-view").hidden = false;
  $("#app-view").hidden = true;
  $("#logout-btn").hidden = true;
  $("#whoami").textContent = "";
}

async function showApp() {
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  $("#logout-btn").hidden = false;
  $("#whoami").textContent = state.email || "";
  await loadMethods();
  await loadSeriesList();
  const last = Number(storage("hindsight.series"));
  if (last && state.seriesList.some((s) => s.id === last)) await selectSeries(last);
}

async function login(email, password) {
  const res = await api("POST", "/auth/token", { username: email, password }, { form: true, token: null });
  if (!res.ok) return res;
  setSession(res.data.access_token, email);
  return res;
}

$("#auth-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const mode = ev.submitter?.dataset.mode || "login";
  const f = new FormData(ev.target);
  const email = f.get("email").trim();
  const password = f.get("password");
  const msg = $("#auth-msg");
  msg.className = "msg";
  msg.textContent = "";
  await withBusy(ev.submitter, async () => {
    if (mode === "signup") {
      const r = await api("POST", "/auth/signup", { email, password }, { token: null });
      if (!r.ok && r.status !== 409) { msg.className = "msg error"; msg.textContent = errText(r); return; }
    }
    const r = await login(email, password);
    if (!r.ok) { msg.className = "msg error"; msg.textContent = errText(r); return; }
    await showApp();
  });
});
$("#logout-btn").addEventListener("click", () => logout());

// ------------------------------------------------------------------ 시리즈
async function loadMethods() {
  const r = await api("GET", "/methods");
  if (!r.ok) return;
  state.methods = r.data;
  const sel = $("#method-select");
  sel.replaceChildren(...r.data.map((m) => h("option", { value: m.name }, m.name)));
  onMethodChange();
}

async function loadSeriesList() {
  const r = await api("GET", "/series");
  if (!r.ok) return;
  state.seriesList = r.data;
  renderSeriesList();
}

function renderSeriesList() {
  const ul = $("#series-list");
  if (!state.seriesList.length) {
    ul.replaceChildren(h("li", { class: "muted small" }, "아직 없습니다."));
    return;
  }
  ul.replaceChildren(...state.seriesList.map((s) =>
    h("li", {},
      h("button", {
        "aria-current": state.series?.id === s.id ? "true" : "false",
        onclick: () => selectSeries(s.id),
      }, h("span", {}, s.name), h("span", { class: "muted small" }, `${s.source} · ${s.frequency}`)))));
}

async function selectSeries(id) {
  const r = await api("GET", `/series/${id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  state.series = r.data;
  state.selectedModelId = null;
  state.selectedRun = null;
  storage("hindsight.series", String(id));
  renderSeriesList();
  $("#empty-hint").hidden = true;
  $("#series-view").hidden = false;
  $("#model-panel").hidden = true;
  $("#run-detail").hidden = true;
  onMethodChange();
  await refreshAll();
}

async function refreshAll() {
  await Promise.all([loadSummary(), loadModels(), loadRuns(), loadLeaderboard(), loadIngestRuns()]);
  await drawChart();
}

async function loadSummary() {
  const s = state.series;
  const r = await api("GET", `/series/${s.id}/summary`);
  if (!r.ok) return;
  state.summary = r.data;
  $("#s-title").textContent = s.name;
  const cfg = Object.keys(s.source_config || {}).length ? ` · ${JSON.stringify(s.source_config)}` : "";
  $("#s-meta").textContent = `#${s.id} · ${s.frequency} · 소스 ${s.source}${cfg}`;
  $("#ingest-btn").hidden = s.source === "manual";
  $("#sample-btn").hidden = s.source !== "manual";
  const stat = (k, v) => h("div", { class: "stat" }, h("div", { class: "v" }, v), h("div", { class: "k" }, k));
  $("#s-stats").replaceChildren(
    stat("관측치", r.data.count.toLocaleString()),
    stat("처음 (UTC)", fmtTs(r.data.first_ts, { year: true })),
    stat("마지막 (UTC)", fmtTs(r.data.last_ts, { year: true })),
  );
}

async function loadIngestRuns() {
  const r = await api("GET", `/series/${state.series.id}/ingest-runs`);
  if (!r.ok) return;
  table($("#ingest-table"), ["시작", "상태", "가져옴", "새로 저장", "오류"], r.data, (x) => [
    fmtTs(x.started_at), statusBadge(x.status), cellNum(x.fetched), cellNum(x.inserted), x.error || "",
  ], "수집 기록이 없습니다.");
}

$("#series-form").addEventListener("change", (ev) => {
  if (ev.target.name !== "source") return;
  for (const el of ev.currentTarget.querySelectorAll("[data-for]")) el.hidden = el.dataset.for !== ev.target.value;
});

$("#series-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const source = f.get("source");
  let source_config = {};
  if (source === "open_meteo") source_config = { latitude: Number(f.get("latitude")), longitude: Number(f.get("longitude")) };
  if (source === "synthetic") source_config = { backfill: Number(f.get("backfill")) };
  await withBusy(ev.submitter, async () => {
    const r = await api("POST", "/series", { name: f.get("name").trim(), frequency: f.get("frequency"), source, source_config });
    if (!r.ok) { toast(errText(r), "error"); return; }
    ev.target.reset();
    ev.target.querySelector("[name=source]").dispatchEvent(new Event("change", { bubbles: true }));
    await loadSeriesList();
    await selectSeries(r.data.id);
    if (source !== "manual") toast("만들었습니다. '수집 실행'을 눌러 데이터를 가져오세요.");
  });
});

$("#ingest-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("POST", `/series/${state.series.id}/ingest`);
  if (r.ok) toast(`수집 완료: ${r.data.fetched}개 가져옴, ${r.data.inserted}개 새로 저장`);
  else toast(errText(r), "error");
  await refreshAll();
}));

$("#score-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const r = await api("POST", `/series/${state.series.id}/score`);
  if (r.ok) toast(`새로 채점 ${r.data.newly_scored}개 · 대기 ${r.data.awaiting_actual} · 실제값 없음 ${r.data.missing_actual}`);
  else toast(errText(r), "error");
  await Promise.all([loadLeaderboard(), state.selectedRun ? openRun(state.selectedRun.id) : null]);
}));

$("#sample-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const step = STEP_MS[state.series.frequency];
  const end = Math.floor(Date.now() / step) * step - step;
  const points = Array.from({ length: 200 }, (_, i) => {
    const t = end - (199 - i) * step;
    return { ts: toIso(t), value: +(10 + 5 * Math.sin((2 * Math.PI * t) / (24 * 3600e3)) + (Math.random() - 0.5)).toFixed(3) };
  });
  const r = await api("POST", `/series/${state.series.id}/observations`, { points });
  if (r.ok) toast(`저장 ${r.data.inserted}개 · 중복 ${r.data.duplicates}개`);
  else toast(errText(r), "error");
  await refreshAll();
}));

$("#delete-series-btn").addEventListener("click", async () => {
  const s = state.series;
  if (!confirm(`'${s.name}' 시리즈와 그 안의 관측치·모델·예측을 모두 지웁니다. 계속할까요?`)) return;
  const r = await api("DELETE", `/series/${s.id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  state.series = null;
  storage("hindsight.series", null);
  $("#series-view").hidden = true;
  $("#empty-hint").hidden = false;
  await loadSeriesList();
});

// ------------------------------------------------------------------ 모델
function onMethodChange() {
  const m = state.methods.find((x) => x.name === $("#method-select").value);
  if (!m) return;
  const freq = state.series?.frequency || "hourly";
  $("#params-input").value = JSON.stringify(m.default_params[freq] || {});
  const help = Object.entries(m.params_help).map(([k, v]) => `${k}: ${v}`).join(" · ");
  $("#method-desc").textContent = `${m.description}${help ? ` — ${help}` : ""}`;
}
$("#method-select").addEventListener("change", onMethodChange);

function cutoffAt(ratio) {
  const s = state.summary;
  if (!s?.first_ts) return "";
  const step = STEP_MS[state.series.frequency];
  const a = Date.parse(s.first_ts), b = Date.parse(s.last_ts);
  const idx = Math.round(((b - a) / step) * ratio);
  return toIso(a + idx * step);
}
for (const btn of document.querySelectorAll("[data-cutoff]")) {
  btn.addEventListener("click", () => {
    $("#cutoff-input").value = btn.dataset.cutoff ? cutoffAt(Number(btn.dataset.cutoff)) : "";
  });
}

$("#model-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  let params;
  try { params = JSON.parse(f.get("params") || "{}"); } catch { toast("파라미터가 올바른 JSON이 아닙니다", "error"); return; }
  const body = { series_id: state.series.id, name: f.get("name").trim(), method: f.get("method"), params };
  if (f.get("train_cutoff").trim()) body.train_cutoff = f.get("train_cutoff").trim();
  await withBusy(ev.submitter, async () => {
    const r = await api("POST", "/models", body);
    if (!r.ok) { toast(errText(r), "error"); return; }
    toast(`접수됨 (202). 모델 #${r.data.id} 상태: ${r.data.status}`);
    ev.target.querySelector("[name=name]").value = "";
    state.selectedModelId = r.data.id;
    await loadModels();
  });
});

async function loadModels() {
  const r = await api("GET", `/models${qs({ series_id: state.series.id })}`);
  if (!r.ok) return;
  state.models = r.data;
  renderModels();
  clearTimeout(state.pollTimer);
  // 백그라운드 학습이 끝날 때까지 상태를 다시 물어본다 (202 패턴의 클라이언트 쪽)
  if (r.data.some((m) => m.status === "queued" || m.status === "training")) {
    state.pollTimer = setTimeout(loadModels, 1500);
  }
}

function statusBadge(status) {
  const kind = { done: "good", success: "good", scored: "good", failed: "critical", missing_actual: "warning",
    queued: "busy", training: "busy", running: "busy" }[status] || "";
  const label = { done: "완료", failed: "실패", queued: "대기", training: "학습 중", success: "성공", running: "실행 중",
    scored: "채점됨", awaiting_actual: "정답 대기", missing_actual: "실제값 없음" }[status] || status;
  return h("span", { class: `badge ${kind}`, title: status }, label);
}
const cellNum = (v) => ({ num: true, v });

function table(el, headers, rows, rowFn, emptyText = "없음", rowProps) {
  const thead = h("thead", {}, h("tr", {}, headers.map((x) =>
    typeof x === "object" ? h("th", { class: "num" }, x.num) : h("th", {}, x))));
  const body = rows.length
    ? rows.map((row) => h("tr", rowProps ? rowProps(row) : {}, rowFn(row).map((c) =>
      c && typeof c === "object" && "num" in c && !(c instanceof Node)
        ? h("td", { class: "num" }, c.v === null || c.v === undefined ? "-" : c.v)
        : h("td", {}, c))))
    : [h("tr", { class: "empty-row" }, h("td", { colspan: headers.length }, emptyText))];
  el.replaceChildren(thead, h("tbody", {}, body));
}

function renderModels() {
  table($("#models-table"), ["#", "이름", "방법", "상태", "train_cutoff", { num: "학습 포인트" }, "state", ""], state.models, (m) => [
    String(m.id),
    m.name,
    m.method,
    statusBadge(m.status),
    fmtTs(m.train_cutoff),
    cellNum(m.n_train?.toLocaleString()),
    m.error ? h("span", { class: "small", style: "color:var(--critical-ink)" }, m.error)
      : h("span", { class: "small muted" }, m.state ? JSON.stringify(roundObj(m.state)) : ""),
    h("div", { class: "actions" },
      m.status === "failed" && h("button", { class: "secondary small", onclick: (e) => { e.stopPropagation(); retryModel(m.id); } }, "재시도"),
      h("button", { class: "ghost small danger", onclick: (e) => { e.stopPropagation(); deleteModel(m); } }, "삭제")),
  ], "모델이 없습니다. 위에서 학습을 요청하세요.", (m) => ({
    class: "selectable",
    "aria-selected": m.id === state.selectedModelId ? "true" : "false",
    onclick: () => selectModel(m.id),
  }));
  if (state.selectedModelId) renderModelPanel();
}

function roundObj(o) {
  return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, typeof v === "number" ? +v.toFixed(4) : v]));
}

function selectModel(id) {
  state.selectedModelId = id;
  renderModels();
  loadRuns();
  drawChart();
}

function renderModelPanel() {
  const m = state.models.find((x) => x.id === state.selectedModelId);
  const panel = $("#model-panel");
  if (!m) { panel.hidden = true; return; }
  panel.hidden = false;
  $("#mp-title").textContent = `선택한 모델: #${m.id} ${m.name} (${m.method}) — ${m.status === "done" ? "예측 가능" : `상태 ${m.status}, 예측 불가`}`;
  const bt = $("#backtest-form");
  if (bt.dataset.model !== String(m.id)) {
    bt.dataset.model = String(m.id);
    bt.start.value = m.train_cutoff;
    bt.end.value = state.summary?.last_ts || "";
    const daily = state.series.frequency === "daily";
    bt.every.value = daily ? 1 : 6;
    bt.horizon.value = daily ? 7 : 24;
    $("#forecast-form").horizon.value = daily ? 7 : 24;
  }
}

async function retryModel(id) {
  const r = await api("POST", `/models/${id}/retry`);
  if (!r.ok) toast(errText(r), "error");
  await loadModels();
}

async function deleteModel(m) {
  if (!confirm(`모델 #${m.id} ${m.name}을(를) 삭제할까요?`)) return;
  const r = await api("DELETE", `/models/${m.id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  if (state.selectedModelId === m.id) state.selectedModelId = null;
  await loadModels();
  renderModelPanel();
}

$("#backtest-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = { start: f.start.value.trim(), end: f.end.value.trim(), every: Number(f.every.value), horizon: Number(f.horizon.value) };
  await withBusy(ev.submitter, async () => {
    const r = await api("POST", `/models/${state.selectedModelId}/backtest`, body);
    if (!r.ok) { toast(errText(r), "error"); return; }
    toast(`origin ${r.data.origins}개 · 새로 ${r.data.created} · 기존 ${r.data.skipped_existing} · 채점 ${r.data.scored}`);
    await Promise.all([loadRuns(), loadLeaderboard()]);
  });
});

$("#forecast-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = { model_id: state.selectedModelId, horizon: Number(f.horizon.value) };
  if (f.origin.value.trim()) body.origin = f.origin.value.trim();
  await withBusy(ev.submitter, async () => {
    const r = await api("POST", "/forecasts", body);
    if (!r.ok) { toast(errText(r), "error"); return; }
    toast(`예측 #${r.data.id} 생성 (origin ${fmtTs(r.data.origin)})`);
    await loadRuns();
    await openRun(r.data.id);
  });
});

// ------------------------------------------------------------------ 예측 기록
async function loadRuns() {
  const params = { series_id: state.series.id, model_id: state.selectedModelId, limit: 300 };
  const r = await api("GET", `/forecasts${qs(params)}`);
  if (!r.ok) return;
  state.runs = r.data;
  const modelName = (id) => state.models.find((m) => m.id === id)?.name ?? `#${id}`;
  $("#runs-filter").textContent = state.selectedModelId
    ? `모델 '${modelName(state.selectedModelId)}'의 예측만 표시 (최신 300개)`
    : "모든 모델 (최신 300개) — 모델 행을 클릭하면 그 모델만";
  table($("#runs-table"), ["#", "모델", "origin (UTC)", { num: "horizon" }, "만든 시각"], r.data, (x) => [
    String(x.id), modelName(x.model_id), fmtTs(x.origin, { year: true }), cellNum(x.horizon), fmtTs(x.created_at),
  ], "예측이 없습니다. 모델을 고르고 백테스트나 단일 예측을 실행하세요.", (x) => ({
    class: "selectable",
    "aria-selected": state.selectedRun?.id === x.id ? "true" : "false",
    onclick: () => openRun(x.id),
  }));
}

async function openRun(id) {
  const r = await api("GET", `/forecasts/${id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  state.selectedRun = r.data;
  const run = r.data;
  const model = state.models.find((m) => m.id === run.model_id);
  $("#run-detail").hidden = false;
  $("#rd-title").textContent = `예측 #${run.id} — ${model?.name ?? run.model_id}, origin ${fmtTs(run.origin, { year: true })}, ${run.horizon}스텝`;
  table($("#points-table"), [{ num: "step" }, "target (UTC)", { num: "예측" }, { num: "실제" }, { num: "|오차|" }, "상태"], run.points, (p) => [
    cellNum(p.step), fmtTs(p.target_ts), cellNum(num(p.yhat)), cellNum(p.actual === null ? null : num(p.actual)),
    cellNum(p.abs_error === null ? null : num(p.abs_error)), statusBadge(p.status),
  ]);
  for (const tr of $("#runs-table").querySelectorAll("tbody tr")) tr.setAttribute("aria-selected", "false");
  await loadRuns();
  await drawChart();
}

$("#withdraw-btn").addEventListener("click", async () => {
  const run = state.selectedRun;
  if (!run || !confirm(`예측 #${run.id}을(를) 철회할까요? (첫 목표 시각 전에만 가능)`)) return;
  const r = await api("DELETE", `/forecasts/${run.id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  toast("철회했습니다");
  clearRun();
  await loadRuns();
});

function clearRun() {
  state.selectedRun = null;
  $("#run-detail").hidden = true;
  drawChart();
}
$("#clear-run-btn").addEventListener("click", clearRun);

// ------------------------------------------------------------------ 리더보드
async function loadLeaderboard() {
  const f = $("#lb-form");
  const r = await api("GET", `/series/${state.series.id}/leaderboard${qs({ step: f.step.value, common_only: f.common_only.checked })}`);
  if (!r.ok) return;
  const d = r.data;
  $("#lb-meta").textContent =
    `비교 모델 ${d.n_models}개 · ${d.common_only ? "모든 모델이 채점받은 시점만 비교" : "각 모델의 채점 전체 사용 (불공정할 수 있음)"}` +
    ` · 정답 대기 ${d.awaiting_actual} · 실제값 없음 ${d.missing_actual}`;
  const maxMae = Math.max(...d.rows.map((x) => x.mae), 1e-9);
  table($("#lb-table"), [{ num: "순위" }, "모델", "방법", { num: "채점 수" }, { num: "MAE" }, { num: "RMSE" }, { num: "bias" }], d.rows, (x) => [
    cellNum(x.rank), x.model_name, x.method, cellNum(x.n.toLocaleString()),
    h("div", { class: "bar-cell" },
      h("span", { class: "bar-track" }, h("span", { class: "bar", style: `width:${(x.mae / maxMae) * 100}%` })),
      h("span", { style: "font-variant-numeric:tabular-nums" }, num(x.mae))),
    cellNum(num(x.rmse)), cellNum((x.bias >= 0 ? "+" : "") + num(x.bias)),
  ], "채점된 예측이 없습니다. 백테스트를 돌리거나 '채점 실행'을 누르세요.");
}
$("#lb-form").addEventListener("submit", (ev) => { ev.preventDefault(); loadLeaderboard(); });
$("#lb-form").addEventListener("change", () => loadLeaderboard());

// ------------------------------------------------------------------ 차트
async function fetchObservations(startIso, endIso) {
  const out = [];
  let after = null;
  for (let page = 0; page < 4; page++) {
    const r = await api("GET", `/series/${state.series.id}/observations${qs({ start: startIso, end: endIso, after, limit: 5000 })}`);
    if (!r.ok) break;
    out.push(...r.data.items);
    if (!r.data.next_cursor) break;
    after = r.data.next_cursor;
  }
  return out;
}

let chartToken = 0;
async function drawChart() {
  const my = ++chartToken;
  const el = $("#chart");
  const sum = state.summary;
  const run = state.selectedRun;
  const model = state.models.find((m) => m.id === (run?.model_id ?? state.selectedModelId));
  $("#clear-run-btn").hidden = !run;
  $("#range-select").disabled = !!run;

  if (!sum || !sum.count) {
    el.replaceChildren(h("div", { class: "empty" }, "관측치가 없습니다. 수집하거나 데이터를 넣으세요."));
    $("#chart-legend").replaceChildren();
    return;
  }
  const step = STEP_MS[state.series.frequency];
  let start, end;
  if (run) {
    const span = Math.max(run.horizon * 3, 72) * step;
    start = Date.parse(run.origin) - span;
    end = Date.parse(run.points.at(-1).target_ts) + step;
    $("#chart-title").textContent = "관측치 + 예측";
  } else {
    const range = $("#range-select").value;
    end = Date.parse(sum.last_ts) + step;
    start = range === "all" ? Date.parse(sum.first_ts) : end - Number(range) * 86400e3;
    $("#chart-title").textContent = "관측치";
  }
  const obs = await fetchObservations(toIso(start), toIso(end));
  if (my !== chartToken) return; // 더 최근 요청이 있으면 버린다

  const series = [{ key: "obs", name: "관측치", color: "var(--series-1)", points: obs.map((o) => [Date.parse(o.ts), o.value]) }];
  if (run) series.push({ key: "fc", name: `예측 #${run.id}`, color: "var(--series-2)", points: run.points.map((p) => [Date.parse(p.target_ts), p.yhat]) });
  const markers = [];
  if (model) markers.push({ x: Date.parse(model.train_cutoff), label: "학습 cutoff" });
  if (run) markers.push({ x: Date.parse(run.origin), label: "예측 시작" });

  const shown = renderLineChart(el, series, markers, step);
  $("#chart-legend").replaceChildren(
    ...(series.length > 1 ? series.map((s) => h("span", {}, h("span", { class: "key", style: `background:${s.color}` }), s.name)) : []),
    ...shown.map((m) => h("span", {}, h("span", { class: "key dashed" }), m.label)),
  );
}

function niceTicks(min, max, count = 5) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const stepV = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const ticks = [];
  for (let v = Math.floor(min / stepV) * stepV; v <= max + stepV * 0.5; v += stepV) ticks.push(+v.toFixed(10));
  return ticks;
}

const SVG = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

function renderLineChart(el, series, markers, step) {
  const W = Math.max(el.clientWidth, 320), H = 300;
  const m = { l: 48, r: 16, t: 18, b: 28 };
  const all = series.flatMap((x) => x.points);
  if (!all.length) { el.replaceChildren(h("div", { class: "empty" }, "이 범위에 관측치가 없습니다.")); return []; }

  // x 범위는 데이터로만 정한다. 범위 밖의 기준선(예: 한참 전 학습 cutoff)은 그리지 않는다
  const xs = all.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  markers = markers.filter((mk) => mk.x >= x0 && mk.x <= x1);
  const yt = niceTicks(Math.min(...all.map((p) => p[1])), Math.max(...all.map((p) => p[1])));
  const y0 = yt[0], y1 = yt.at(-1);
  const X = (v) => m.l + ((v - x0) / Math.max(x1 - x0, 1)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((x) => x.name).join(", ") + " 시계열 차트" });
  for (const v of yt) {
    svg.append(s("line", { class: v === y0 ? "base-line" : "grid-line", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    svg.append(s("text", { class: "axis-text", x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, String(+v.toFixed(2))));
  }
  const spanDays = (x1 - x0) / 86400e3;
  const nX = Math.max(2, Math.min(7, Math.floor((W - m.l - m.r) / 110)));
  for (let i = 0; i <= nX; i++) {
    const t = x0 + ((x1 - x0) * i) / nX;
    const d = new Date(t);
    const label = spanDays > 4 ? `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` : `${pad(d.getUTCDate())}일 ${pad(d.getUTCHours())}시`;
    svg.append(s("text", { class: "axis-text", x: X(t), y: H - 8, "text-anchor": i === 0 ? "start" : i === nX ? "end" : "middle" }, label));
  }
  for (const mk of markers) {
    const mx = X(mk.x), nearRight = mx > W - m.r - 90;
    svg.append(s("line", { class: "marker-line", x1: mx, x2: mx, y1: m.t, y2: H - m.b }));
    svg.append(s("text", { class: "marker-text", x: nearRight ? mx - 4 : mx + 4, y: m.t - 5, "text-anchor": nearRight ? "end" : "start" }, mk.label));
  }
  for (const ser of series) {
    if (!ser.points.length) continue;
    // 관측 간격이 한 스텝보다 크게 벌어지면 선을 끊는다 (없는 데이터를 이어 그리지 않음)
    let d = "", prev = null;
    for (const [tx, ty] of ser.points) {
      d += `${prev !== null && tx - prev > step * 1.5 ? "M" : d ? "L" : "M"}${X(tx).toFixed(1)},${Y(ty).toFixed(1)}`;
      prev = tx;
    }
    svg.append(s("path", { d, fill: "none", stroke: ser.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    if (ser.key === "fc" && ser.points.length <= 72) {
      for (const [tx, ty] of ser.points) svg.append(s("circle", { cx: X(tx), cy: Y(ty), r: 3, fill: ser.color, stroke: "var(--surface)", "stroke-width": 2 }));
    }
  }

  // 크로스헤어 + 툴팁: 가장 가까운 x로 스냅, 그 시각의 모든 시리즈 값을 보여준다
  const cross = s("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const dots = series.map((ser) => s("circle", { r: 4, fill: ser.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }));
  svg.append(cross, ...dots);
  const hit = s("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
  svg.append(hit);

  const lookup = series.map((ser) => new Map(ser.points.map((p) => [p[0], p[1]])));
  const times = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b);
  const tip = $("#tooltip");
  hit.addEventListener("pointermove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const t = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
    let lo = 0, hi = times.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < t) lo = mid + 1; else hi = mid; }
    const tx = lo > 0 && Math.abs(times[lo - 1] - t) < Math.abs(times[lo] - t) ? times[lo - 1] : times[lo];
    cross.setAttribute("x1", X(tx)); cross.setAttribute("x2", X(tx)); cross.setAttribute("visibility", "visible");
    const rows = [];
    series.forEach((ser, i) => {
      const v = lookup[i].get(tx);
      if (v === undefined) { dots[i].setAttribute("visibility", "hidden"); return; }
      dots[i].setAttribute("cx", X(tx)); dots[i].setAttribute("cy", Y(v)); dots[i].setAttribute("visibility", "visible");
      rows.push(h("div", { class: "r" }, h("span", { class: "key", style: `background:${ser.color}` }), h("b", {}, num(v, 2)), h("span", {}, ser.name)));
    });
    if (lookup.length > 1) {
      const a = lookup[0].get(tx), f = lookup[1].get(tx);
      if (a !== undefined && f !== undefined) rows.push(h("div", { class: "r" }, h("span", { class: "key" }), h("b", {}, num(Math.abs(f - a), 2)), h("span", {}, "|오차|")));
    }
    tip.replaceChildren(h("div", { class: "t" }, `${fmtTs(toIso(tx), { year: true })} UTC`), ...rows);
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = ev.clientX + 14, top = ev.clientY - th - 10;
    if (left + tw > window.innerWidth - 8) left = ev.clientX - tw - 14;
    if (top < 8) top = ev.clientY + 16;
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  });
  hit.addEventListener("pointerleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  });
  el.replaceChildren(svg);
  return markers;
}

$("#range-select").addEventListener("change", drawChart);
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => state.series && drawChart(), 150); });

// ------------------------------------------------------------------ 데모
async function waitModelDone(id, token) {
  for (let i = 0; i < 40; i++) {
    const r = await api("GET", `/models/${id}`, undefined, token ? { token } : {});
    if (r.ok && (r.data.status === "done" || r.data.status === "failed")) return r.data;
    await sleep(300);
  }
  return null;
}

$("#demo-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const msg = $("#demo-msg");
  const say = (t, kind = "") => { msg.className = `msg small ${kind}`; msg.textContent = t; };
  const stamp = new Date().toISOString().slice(5, 16).replace(/[-:T]/g, "");
  say("시리즈 만드는 중…");
  const sr = await api("POST", "/series", {
    name: `demo-${stamp}`, frequency: "hourly", source: "synthetic",
    source_config: { backfill: 1000, period: 24, amplitude: 5, noise: 0.8 },
  });
  if (!sr.ok) { say(errText(sr), "error"); return; }
  const sid = sr.data.id;
  say("수집 중…");
  const ir = await api("POST", `/series/${sid}/ingest`);
  if (!ir.ok) { say(errText(ir), "error"); return; }
  const sum = (await api("GET", `/series/${sid}/summary`)).data;
  const a = Date.parse(sum.first_ts), b = Date.parse(sum.last_ts);
  const cutoff = toIso(a + Math.round((b - a) / 3600e3 * 0.6) * 3600e3);

  const specs = [["naive", "naive"], ["seasonal-24", "seasonal_naive"], ["ses", "ses"], ["drift", "drift"]];
  for (const [name, method] of specs) {
    say(`모델 학습 중: ${name}`);
    const mr = await api("POST", "/models", { series_id: sid, name, method, train_cutoff: cutoff });
    if (!mr.ok) { say(errText(mr), "error"); return; }
    const done = await waitModelDone(mr.data.id);
    if (done?.status !== "done") { say(`${name} 학습 실패`, "error"); return; }
    say(`백테스트 중: ${name}`);
    const br = await api("POST", `/models/${mr.data.id}/backtest`, { start: cutoff, end: sum.last_ts, every: 6, horizon: 24 });
    if (!br.ok) { say(errText(br), "error"); return; }
  }
  say("완료! 리더보드를 확인하세요.", "ok");
  await loadSeriesList();
  await selectSeries(sid);
}));

// ------------------------------------------------------------------ 규칙 위반 테스트
// 각 테스트는 임시 시리즈 위에서 돈다. expect: 기대 HTTP 상태와 code. run(ctx)는 마지막 응답을 돌려준다.
const RULES = [
  { name: "토큰 없이 접근", expect: [401], why: "로그인 필수",
    run: () => api("GET", "/series", undefined, { token: null }) },
  { name: "미래 시각의 관측치 업로드", expect: [422, "future_observation"], why: "아직 오지 않은 정답을 넣을 수 없음",
    run: (c) => api("POST", `/series/${c.sid}/observations`, { points: [{ ts: toIso(c.nowH + 2 * 3600e3), value: 1 }] }) },
  { name: "격자에서 벗어난 시각(30분)", expect: [422, "off_grid"], why: "hourly 시리즈는 정시만",
    run: (c) => api("POST", `/series/${c.sid}/observations`, { points: [{ ts: toIso(c.nowH - 5 * 3600e3 + 1800e3), value: 1 }] }) },
  { name: "같은 관측치 두 번 업로드", expect: [200], why: "중복은 무시 (duplicates=1)",
    check: (r) => r.data?.duplicates === 1,
    run: (c) => api("POST", `/series/${c.sid}/observations`, { points: [{ ts: c.ts[0], value: 999 }] }) },
  { name: "데이터 부족 (3개로 seasonal_naive)", expect: [422, "insufficient_data"], why: "계절 길이 24 이상 필요",
    run: async (c) => {
      const r = await api("POST", "/series", { name: `rule-small-${c.stamp}`, frequency: "hourly" });
      c.smallSid = r.data?.id;
      await api("POST", `/series/${c.smallSid}/observations`, { points: c.ts.slice(0, 3).map((ts) => ({ ts, value: 1 })) });
      return api("POST", "/models", { series_id: c.smallSid, name: "x", method: "seasonal_naive" });
    } },
  { name: "train_cutoff가 미래", expect: [422, "lookahead_violation"], why: "지금 없는 데이터로 학습 불가",
    run: (c) => api("POST", "/models", { series_id: c.sid, name: "x", method: "naive", train_cutoff: toIso(c.nowH + 86400e3) }) },
  { name: "없는 시리즈", expect: [404, "series_not_found"], why: "",
    run: () => api("GET", "/series/99999999") },
  { name: "다른 사용자의 시리즈 조회", expect: [404, "series_not_found"], why: "남의 것은 존재 여부도 숨김",
    run: async (c) => {
      const email = `other-${c.stamp}@example.com`;
      await api("POST", "/auth/signup", { email, password: "password123" }, { token: null });
      const t = await api("POST", "/auth/token", { username: email, password: "password123" }, { form: true, token: null });
      return api("GET", `/series/${c.sid}`, undefined, { token: t.data?.access_token, allow401: true });
    } },
  { name: "cutoff보다 과거 origin으로 예측", expect: [422, "lookahead_violation"], why: "미래를 이미 본 모델",
    run: (c) => api("POST", "/forecasts", { model_id: c.modelId, origin: c.ts[20], horizon: 3 }) },
  { name: "같은 (모델, origin) 두 번 예측", expect: [409, "duplicate_forecast"], why: "다시 뽑기 금지",
    run: async (c) => {
      const first = await api("POST", "/forecasts", { model_id: c.modelId, origin: c.ts[45], horizon: 2 });
      c.pastRunId = first.data?.id;
      return api("POST", "/forecasts", { model_id: c.modelId, origin: c.ts[45], horizon: 2 });
    } },
  { name: "늦게 온 실제값 채점", expect: [200], why: "정답 도착 후 채점 1개",
    check: (r) => r.data?.newly_scored === 1,
    run: async (c) => {
      await api("POST", `/series/${c.sid}/score`);
      const live = await api("POST", "/forecasts", { model_id: c.modelId, horizon: 3 }); // 마지막 관측에서 → 실제값 없음
      const target = live.data?.points?.[0]?.target_ts;
      await api("POST", `/series/${c.sid}/observations`, { points: [{ ts: target, value: 5 }] });
      return api("POST", `/series/${c.sid}/score`);
    } },
  { name: "채점 다시 실행 (멱등)", expect: [200], why: "두 번째는 0개",
    check: (r) => r.data?.newly_scored === 0,
    run: (c) => api("POST", `/series/${c.sid}/score`) },
  { name: "정답이 나온 예측 철회", expect: [409, "forecast_sealed"], why: "결과 보고 지우기 금지",
    run: (c) => api("DELETE", `/forecasts/${c.pastRunId}`) },
  { name: "예측 기록 있는 모델 삭제", expect: [409, "model_has_forecasts"], why: "나쁜 모델 숨기기 금지",
    run: (c) => api("DELETE", `/models/${c.modelId}`) },
];

function renderRules(results = []) {
  table($("#rules-table"), ["테스트", "기대", "결과", "응답"], RULES, (rule) => {
    const res = results[RULES.indexOf(rule)];
    return [
      h("div", {}, rule.name, rule.why && h("div", { class: "muted small" }, rule.why)),
      h("code", { class: "small" }, rule.expect.join(" ")),
      res ? statusBadgeResult(res) : h("span", { class: "muted small" }, "-"),
      res ? h("span", { class: "small muted" }, res.got) : "",
    ];
  });
}
function statusBadgeResult(res) {
  if (res.pending) return h("span", { class: "badge busy" }, "실행 중");
  return h("span", { class: `badge ${res.pass ? "good" : "critical"}` }, res.pass ? "통과" : "실패");
}

$("#rules-btn").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
  const results = [];
  renderRules(results);
  const stamp = `${Date.now()}`.slice(-8);
  const nowH = Math.floor(Date.now() / 3600e3) * 3600e3;
  const ts = Array.from({ length: 60 }, (_, i) => toIso(nowH - (70 - i) * 3600e3)); // 70~11시간 전
  const c = { stamp, nowH, ts };

  // 준비: 임시 시리즈 + 데이터 60개 + cutoff=40번째로 학습한 모델
  const sr = await api("POST", "/series", { name: `rule-test-${stamp}`, frequency: "hourly" });
  if (!sr.ok) { toast(errText(sr), "error"); return; }
  c.sid = sr.data.id;
  await api("POST", `/series/${c.sid}/observations`, { points: ts.map((t, i) => ({ ts: t, value: 10 + Math.sin(i / 4) })) });
  const mr = await api("POST", "/models", { series_id: c.sid, name: "rule-model", method: "naive", train_cutoff: ts[40] });
  c.modelId = mr.data?.id;
  await waitModelDone(c.modelId);

  for (let i = 0; i < RULES.length; i++) {
    results[i] = { pending: true };
    renderRules(results);
    const rule = RULES[i];
    let r;
    try { r = await rule.run(c); } catch (e) { r = { status: 0, data: { detail: String(e) } }; }
    const [status, code] = rule.expect;
    const pass = r.status === status && (!code || r.data?.code === code) && (!rule.check || rule.check(r));
    const got = `${r.status}${r.data?.code ? ` ${r.data.code}` : ""}${rule.check ? ` · ${JSON.stringify(r.data)}` : ""}`;
    results[i] = { pass, got };
    renderRules(results);
  }

  // 정리
  await api("DELETE", `/series/${c.sid}`);
  if (c.smallSid) await api("DELETE", `/series/${c.smallSid}`);
  const passed = results.filter((x) => x.pass).length;
  toast(`규칙 테스트 ${passed}/${RULES.length} 통과`, passed === RULES.length ? "" : "error");
  await loadSeriesList();
}));

// ------------------------------------------------------------------ 로그 패널
$("#log-toggle").addEventListener("click", () => {
  const d = $("#log-drawer");
  d.classList.toggle("collapsed");
  $("#log-toggle").textContent = d.classList.contains("collapsed") ? "API 로그 ▴" : "API 로그 ▾";
  storage("hindsight.logCollapsed", d.classList.contains("collapsed") ? "1" : "0");
});
$("#log-errors-only").addEventListener("change", renderLog);
$("#log-clear").addEventListener("click", () => { logEntries.length = 0; renderLog(); });

// ------------------------------------------------------------------ 시작
(async function init() {
  if (storage("hindsight.logCollapsed") !== "0") {
    $("#log-drawer").classList.add("collapsed");
    $("#log-toggle").textContent = "API 로그 ▴";
  }
  renderRules();
  const token = storage("hindsight.token");
  if (token) {
    state.token = token;
    state.email = storage("hindsight.email");
    const me = await api("GET", "/auth/me", undefined, { allow401: true });
    if (me.ok) { state.email = me.data.email; await showApp(); return; }
    setSession(null, null);
  }
  showAuth();
})();
