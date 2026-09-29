// hindsight 대시보드. 프레임워크 없이 fetch + DOM + SVG.
// 서버에서 온 문자열은 textContent로만 넣는다 (innerHTML 금지).
"use strict";

const $ = (sel) => document.querySelector(sel);
const STEP_MS = { hourly: 3600e3, daily: 86400e3 };
const STEP_UNIT = { hourly: "시간", daily: "일" };
const METHOD_LABEL = {
  naive: "나이브 (마지막 값 유지)",
  seasonal_naive: "계절 나이브 (한 주기 전 반복)",
  drift: "추세 연장",
  mean: "평균",
  ses: "지수평활",
};
const AUTO_REFRESH_MS = 60_000;

const state = {
  token: null,
  email: null,
  seriesList: [],
  series: null,
  summary: null,
  models: [],
  board: null,
  byStep: [],
  latest: [],
  range: "7",
  step: 1,
  hidden: new Set(),
  pollTimer: null,
};

// ================================================================== 유틸
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const pad = (n) => String(n).padStart(2, "0");
const toIso = (ms) => new Date(ms).toISOString().replace(".000Z", "Z");
const fix = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toFixed(d));
const fmtWhen = (ms) => {
  const d = new Date(ms);
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const qs = (obj) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== null && v !== undefined && v !== "") p.append(k, v);
  const s = p.toString();
  return s ? `?${s}` : "";
};
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
  toastTimer = setTimeout(() => (t.className = "toast"), kind === "error" ? 5000 : 2800);
}
function errText(res) {
  const d = res.data;
  if (d && typeof d === "object") {
    if (Array.isArray(d.detail)) return d.detail.map((e) => e.msg).join(" / ");
    return d.detail ?? JSON.stringify(d);
  }
  return `요청 실패 (HTTP ${res.status})`;
}
async function busy(button, fn) {
  if (button) button.disabled = true;
  try { return await fn(); } finally { if (button) button.disabled = false; }
}

// ================================================================== API
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
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch {
    return { ok: false, status: 0, data: { detail: "서버에 연결할 수 없습니다." } };
  }
  const text = await res.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch { data = text; } }
  if (res.status === 401 && token && token === state.token) logout("로그인이 만료됐습니다. 다시 로그인해 주세요.");
  return { ok: res.ok, status: res.status, data };
}

// ================================================================== 인증
let authMode = "login";
for (const tab of document.querySelectorAll("[data-tab]")) {
  tab.addEventListener("click", () => {
    authMode = tab.dataset.tab;
    for (const t of document.querySelectorAll("[data-tab]")) t.setAttribute("aria-selected", String(t === tab));
    $("#auth-submit").textContent = authMode === "login" ? "로그인" : "가입하고 시작하기";
    $("#auth-form").password.autocomplete = authMode === "login" ? "current-password" : "new-password";
    $("#auth-msg").textContent = "";
  });
}

$("#auth-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const msg = $("#auth-msg");
  msg.className = "msg error";
  if (!email || !form.email.checkValidity()) { msg.textContent = "이메일을 확인해 주세요."; return; }
  if (password.length < 8) { msg.textContent = "비밀번호는 8자 이상이어야 합니다."; return; }
  msg.textContent = "";
  busy($("#auth-submit"), async () => {
    if (authMode === "signup") {
      const r = await api("POST", "/auth/signup", { email, password }, { token: null });
      if (!r.ok) { msg.textContent = r.status === 409 ? "이미 가입된 이메일입니다. 로그인해 주세요." : errText(r); return; }
    }
    const r = await api("POST", "/auth/token", { username: email, password }, { form: true, token: null });
    if (!r.ok) { msg.textContent = r.status === 401 ? "이메일 또는 비밀번호가 올바르지 않습니다." : errText(r); return; }
    setSession(r.data.access_token, email);
    form.reset();
    await enterApp();
  });
});

function setSession(token, email) {
  state.token = token;
  state.email = email;
  storage("hindsight.token", token);
  storage("hindsight.email", email);
}
function logout(message) {
  setSession(null, null);
  clearTimeout(state.pollTimer);
  $("#app-view").hidden = true;
  $("#auth-view").hidden = false;
  if (message) toast(message, "error");
}
$("#logout-btn").addEventListener("click", () => logout());

async function enterApp() {
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  $("#user-email").textContent = state.email || "";
  await loadMethods();
  await loadSeriesList(Number(storage("hindsight.series")) || null);
}

// ================================================================== 시리즈
async function loadSeriesList(preferId = null) {
  const r = await api("GET", "/series");
  if (!r.ok) return;
  state.seriesList = r.data;
  const sel = $("#series-select");
  sel.replaceChildren(...r.data.map((s) => h("option", { value: s.id }, s.name)));
  sel.hidden = !r.data.length;
  if (!r.data.length) {
    state.series = null;
    $("#dash-view").hidden = true;
    $("#empty-view").hidden = false;
    $("#refresh-btn").hidden = true;
    return;
  }
  const target = r.data.find((s) => s.id === preferId) || r.data.find((s) => s.id === state.series?.id) || r.data[0];
  await selectSeries(target.id);
}

async function selectSeries(id) {
  state.series = state.seriesList.find((s) => s.id === id);
  state.hidden.clear();
  state.step = 1;
  $("#series-select").value = String(id);
  storage("hindsight.series", String(id));
  $("#empty-view").hidden = true;
  $("#dash-view").hidden = false;
  $("#refresh-btn").hidden = false;
  await loadDashboard();
}
$("#series-select").addEventListener("change", (ev) => selectSeries(Number(ev.target.value)));

// ================================================================== 대시보드 로딩
async function loadDashboard({ quiet = false } = {}) {
  const s = state.series;
  if (!s) return;
  const common = $("#common-only").checked;
  const [sum, models, board, byStep, latest] = await Promise.all([
    api("GET", `/series/${s.id}/summary`),
    api("GET", `/models${qs({ series_id: s.id })}`),
    api("GET", `/series/${s.id}/leaderboard${qs({ common_only: common })}`),
    api("GET", `/series/${s.id}/analytics/error-by-step`),
    api("GET", `/series/${s.id}/analytics/latest-forecasts`),
  ]);
  if (!sum.ok) { if (!quiet) toast(errText(sum), "error"); return; }
  state.summary = sum.data;
  state.models = (models.data || []).slice().sort((a, b) => a.id - b.id);
  state.board = board.data;
  state.byStep = byStep.data || [];
  state.latest = latest.data || [];

  renderHeader();
  renderKpis();
  renderStepOptions();
  renderRank();
  await drawAll();
  $("#updated-at").textContent = `${fmtWhen(Date.now())} 업데이트`;
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  // 학습 중인 모델이 있으면 자주, 아니면 1분마다 조용히 새로고침
  const training = state.models.some((m) => m.status === "queued" || m.status === "training");
  state.pollTimer = setTimeout(() => {
    if (document.visibilityState === "visible") loadDashboard({ quiet: true });
    else schedulePoll();
  }, training ? 2000 : AUTO_REFRESH_MS);
}

function renderHeader() {
  const s = state.series;
  $("#s-title").textContent = s.name;
  const src = { open_meteo: "실제 날씨 (Open-Meteo)", synthetic: "샘플 데이터", manual: "직접 입력" }[s.source] || s.source;
  const freq = s.frequency === "hourly" ? "1시간 간격" : "1일 간격";
  const last = state.summary.last_ts ? ` · 마지막 관측 ${fmtWhen(Date.parse(state.summary.last_ts))}` : "";
  $("#s-meta").textContent = `${src} · ${freq}${last}`;
}

function modelColor(modelId) {
  const i = state.models.findIndex((m) => m.id === modelId);
  return i >= 0 && i < 8 ? `var(--c${i + 1})` : "var(--muted)";
}
const modelName = (id) => state.models.find((m) => m.id === id)?.name ?? `모델 ${id}`;

function renderKpis() {
  const sum = state.summary;
  const board = state.board;
  const leader = board?.rows?.[0];
  const scored = state.byStep.reduce((a, r) => a + r.n, 0);
  const done = state.models.filter((m) => m.status === "done").length;
  const kpi = (k, v, s) => h("div", { class: "kpi" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), h("div", { class: "s" }, s));
  $("#kpis").replaceChildren(
    kpi("관측치", sum.count.toLocaleString(), sum.last_ts ? `마지막 ${fmtWhen(Date.parse(sum.last_ts))}` : "아직 없음"),
    kpi("가장 잘 맞춘 모델",
      leader ? [h("span", { class: "key", style: `background:${modelColor(leader.model_id)}` }), leader.model_name] : "-",
      leader ? `평균 오차 ${fix(leader.mae)} · ${leader.n.toLocaleString()}개 시점 비교` : "채점된 예측이 아직 없습니다"),
    kpi("채점된 예측", scored.toLocaleString(), `모델 ${done}개가 예측 중`),
    kpi("채점 대기", ((board?.awaiting_actual || 0) + (board?.missing_actual || 0)).toLocaleString(),
      `정답 대기 ${board?.awaiting_actual ?? 0} · 실제값 누락 ${board?.missing_actual ?? 0}`),
  );
}

function renderStepOptions() {
  const steps = [...new Set(state.byStep.map((r) => r.step))].sort((a, b) => a - b);
  if (!steps.length) steps.push(1);
  if (!steps.includes(state.step)) state.step = steps[0];
  const unit = STEP_UNIT[state.series.frequency];
  const sel = $("#step-select");
  sel.replaceChildren(...steps.map((s) => h("option", { value: s }, `${s}${unit} 앞`)));
  sel.value = String(state.step);
}
$("#step-select").addEventListener("change", (ev) => { state.step = Number(ev.target.value); drawAll(); });
for (const b of document.querySelectorAll("#range-seg button")) {
  b.addEventListener("click", () => {
    state.range = b.dataset.days;
    for (const x of document.querySelectorAll("#range-seg button")) x.setAttribute("aria-pressed", String(x === b));
    drawAll();
  });
}
$("#common-only").addEventListener("change", () => loadDashboard());

// ================================================================== 순위 표
function statusBadge(status) {
  const map = { done: ["good", "운영 중"], failed: ["critical", "학습 실패"], queued: ["busy", "대기"], training: ["busy", "학습 중"] };
  const [cls, label] = map[status] || ["", status];
  return h("span", { class: `badge ${cls}` }, label);
}

function renderRank() {
  const rows = state.board?.rows || [];
  const ranked = new Map(rows.map((r) => [r.model_id, r]));
  const maxMae = Math.max(...rows.map((r) => r.mae), 1e-9);
  const others = state.models.filter((m) => !ranked.has(m.id));
  const table = $("#rank-table");
  const head = h("thead", {}, h("tr", {},
    h("th", { class: "num" }, "순위"), h("th", {}, "모델"), h("th", {}, "방법"), h("th", {}, "학습 기간 끝"),
    h("th", { class: "num" }, "비교 시점"), h("th", { class: "num" }, "평균 오차 (MAE)"), h("th", { class: "num" }, "RMSE"),
    h("th", { class: "num" }, "편향"), h("th", {}, "상태")));
  const trFor = (m, r) => h("tr", {},
    h("td", { class: "num rank" }, r ? String(r.rank) : "-"),
    h("td", {}, h("span", { class: "key", style: `background:${modelColor(m.id)}` }), m.name),
    h("td", { class: "muted" }, METHOD_LABEL[m.method] || m.method),
    h("td", { class: "muted" }, fmtWhen(Date.parse(m.train_cutoff))),
    h("td", { class: "num" }, r ? r.n.toLocaleString() : "-"),
    h("td", { class: "num" }, r
      ? h("div", { class: "bar-cell" },
        h("span", { class: "bar-track" }, h("span", { class: "bar", style: `width:${(r.mae / maxMae) * 100}%;background:${modelColor(m.id)}` })),
        h("b", {}, fix(r.mae)))
      : "-"),
    h("td", { class: "num" }, r ? fix(r.rmse) : "-"),
    h("td", { class: "num" }, r ? `${r.bias >= 0 ? "+" : ""}${fix(r.bias)}` : "-"),
    h("td", {}, statusBadge(m.status), m.error && h("div", { class: "err" }, m.error)));
  const body = [
    ...rows.map((r) => trFor(state.models.find((m) => m.id === r.model_id) || { id: r.model_id, name: r.model_name, method: r.method, status: "done", train_cutoff: null }, r)),
    ...others.map((m) => trFor(m, null)),
  ];
  table.replaceChildren(head, h("tbody", {}, body.length ? body
    : h("tr", { class: "empty-row" }, h("td", { colspan: 9 }, "아직 모델이 없습니다. '모델 추가'로 예측을 시작하세요."))));
  const b = state.board;
  $("#rank-caption").textContent = b?.common_only
    ? "모든 모델이 예측한 같은 시점들로만 비교해서 공정하게 순위를 매깁니다. 오차가 작을수록 위."
    : "각 모델이 채점받은 모든 시점을 씁니다 (모델마다 비교 구간이 다를 수 있음).";
}

// ================================================================== 차트 데이터
function rangeStart() {
  const sum = state.summary;
  if (!sum.first_ts) return null;
  if (state.range === "all") return Date.parse(sum.first_ts);
  return Math.max(Date.parse(sum.first_ts), Date.parse(sum.last_ts) - Number(state.range) * 86400e3);
}

async function fetchObservations(startMs, endMs) {
  const out = [];
  let after = null;
  for (let page = 0; page < 6; page++) {
    const r = await api("GET", `/series/${state.series.id}/observations${qs({ start: toIso(startMs), end: toIso(endMs), after, limit: 5000 })}`);
    if (!r.ok) break;
    out.push(...r.data.items);
    if (!r.data.next_cursor) break;
    after = r.data.next_cursor;
  }
  return out;
}

let drawToken = 0;
async function drawAll() {
  const my = ++drawToken;
  const sum = state.summary;
  const unit = STEP_UNIT[state.series.frequency];
  if (!sum?.count) {
    for (const id of ["#main-chart", "#trend-chart", "#step-chart"]) {
      $(id).replaceChildren(h("div", { class: "empty" }, "아직 관측치가 없습니다. 오른쪽 위 '지금 업데이트'로 데이터를 가져오세요."));
    }
    $("#legend").replaceChildren();
    return;
  }
  const stepMs = STEP_MS[state.series.frequency];
  const start = rangeStart();
  const last = Date.parse(sum.last_ts);
  const rangeDays = (last - start) / 86400e3;
  const bucket = rangeDays > 3 ? "day" : "hour";

  const [obs, fp, trend] = await Promise.all([
    fetchObservations(start, last + stepMs),
    api("GET", `/series/${state.series.id}/analytics/forecast-points${qs({ step: state.step, start: toIso(start) })}`),
    api("GET", `/series/${state.series.id}/analytics/error-trend${qs({ bucket, step: state.step, start: toIso(start) })}`),
  ]);
  if (my !== drawToken) return; // 더 최근 요청이 있으면 버린다

  // ---- 메인: 실제값 + 모델별 (채점된 과거 예측 실선 / 최신 예측 점선)
  const byModel = new Map();
  for (const p of fp.data?.points || []) {
    if (!byModel.has(p.model_id)) byModel.set(p.model_id, []);
    byModel.get(p.model_id).push([Date.parse(p.target_ts), p.yhat]);
  }
  const future = new Map(state.latest.map((run) => [run.model_id,
    run.points.map((p) => [Date.parse(p.target_ts), p.yhat]).filter(([t]) => t > last)]));
  const shownModels = state.models.filter((m) => byModel.has(m.id) || future.get(m.id)?.length);

  const series = [{ id: "actual", name: "실제값", color: "var(--ink)", points: obs.map((o) => [Date.parse(o.ts), o.value]), gap: stepMs * 1.5, width: 2 }];
  for (const m of shownModels) {
    if (state.hidden.has(m.id)) continue;
    const hist = byModel.get(m.id) || [];
    series.push({ id: `h${m.id}`, name: `${m.name} · ${state.step}${unit} 앞 예측`, color: modelColor(m.id), points: hist, width: 1.75, dots: hist.length <= 60 });
    const fut = future.get(m.id) || [];
    if (fut.length) series.push({ id: `f${m.id}`, name: `${m.name} · 최신 예측`, color: modelColor(m.id), points: fut, dashed: true, width: 2 });
  }
  const hasFuture = [...future.values()].some((f) => f.length);
  renderLegend(shownModels, hasFuture);
  lineChart($("#main-chart"), {
    series, height: 320,
    markers: [{ x: last, label: "지금" }],
    band: hasFuture ? { from: last } : null,
    emptyText: "표시할 데이터가 없습니다.",
  });
  $("#main-caption").textContent = shownModels.length
    ? `굵은 무채색 선이 실제값, 색 실선은 각 모델이 ${state.step}${unit} 전에 내놓았던 예측, 점선은 가장 최근에 봉인된 앞으로의 예측입니다.`
    : "모델을 추가하면 예측이 실제값 위에 겹쳐 그려집니다.";

  // ---- 오차 추이
  const trendByModel = new Map();
  for (const r of trend.data || []) {
    const t = Date.parse(r.bucket.length === 10 ? `${r.bucket}T00:00:00Z` : `${r.bucket}:00Z`);
    if (!trendByModel.has(r.model_id)) trendByModel.set(r.model_id, []);
    trendByModel.get(r.model_id).push([t, r.mae]);
  }
  staticLegend($("#trend-legend"), [...trendByModel.keys()]);
  $("#trend-caption").textContent = `${state.step}${unit} 앞 예측의 ${bucket === "day" ? "일별" : "시간별"} 평균 절대오차 · 낮을수록 좋음`;
  lineChart($("#trend-chart"), {
    height: 220, zero: true,
    series: [...trendByModel].filter(([id]) => !state.hidden.has(id)).map(([id, pts]) => ({
      id: `t${id}`, name: modelName(id), color: modelColor(id), points: pts, width: 2, dots: pts.length <= 40,
    })),
    emptyText: "채점된 예측이 쌓이면 여기에 오차가 날짜별로 그려집니다.",
  });

  // ---- 예측 거리별 오차 (전체 기간)
  const stepByModel = new Map();
  for (const r of state.byStep) {
    if (!stepByModel.has(r.model_id)) stepByModel.set(r.model_id, []);
    stepByModel.get(r.model_id).push([r.step, r.mae]);
  }
  staticLegend($("#step-legend"), [...stepByModel.keys()]);
  lineChart($("#step-chart"), {
    height: 220, zero: true, xType: "number", xUnit: `${unit} 앞`,
    series: [...stepByModel].filter(([id]) => !state.hidden.has(id)).map(([id, pts]) => ({
      id: `s${id}`, name: modelName(id), color: modelColor(id), points: pts, width: 2, dots: pts.length <= 30,
    })),
    emptyText: "채점된 예측이 쌓이면 거리별 오차가 그려집니다.",
  });
}

function staticLegend(el, modelIds) {
  const ids = modelIds.filter((id) => !state.hidden.has(id)).sort((a, b) => a - b);
  el.replaceChildren(...(ids.length > 1 ? ids.map((id) =>
    h("span", { class: "note" }, h("span", { class: "key", style: `background:${modelColor(id)}` }), modelName(id))) : []));
}

function renderLegend(models, hasFuture) {
  const items = [h("span", { class: "note" }, h("span", { class: "key", style: "background:var(--ink)" }), "실제값")];
  for (const m of models) {
    const on = !state.hidden.has(m.id);
    items.push(h("button", {
      "aria-pressed": String(on), title: on ? "클릭해서 숨기기" : "클릭해서 보이기",
      onclick: () => { on ? state.hidden.add(m.id) : state.hidden.delete(m.id); drawAll(); },
    }, h("span", { class: "key", style: `background:${modelColor(m.id)}` }), m.name));
  }
  if (hasFuture) items.push(h("span", { class: "sep" }), h("span", { class: "note" }, h("span", { class: "key dash", style: "border-color:var(--muted)" }), "최신 예측 (정답 대기)"));
  $("#legend").replaceChildren(...items);
}

// ================================================================== 라인 차트 (SVG)
const SVG = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}
function niceTicks(min, max, count = 5) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const ticks = [];
  for (let v = Math.floor(min / step) * step; v <= max + step * 0.5; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function hideTooltip() { $("#tooltip").hidden = true; }
window.addEventListener("scroll", hideTooltip, { passive: true });

function lineChart(el, { series, height = 280, xType = "time", xUnit = "", markers = [], band = null, zero = false, emptyText }) {
  hideTooltip(); // 다시 그리면 이전 차트의 pointerleave가 오지 않으므로 직접 닫는다
  const all = series.flatMap((x) => x.points);
  if (!all.length) { el.replaceChildren(h("div", { class: "empty" }, emptyText)); return; }
  const W = Math.max(el.clientWidth, 300), H = height;
  const m = { l: 44, r: 12, t: 20, b: 26 };
  const xs = all.map((p) => p[0]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  const ys = all.map((p) => p[1]);
  const yt = niceTicks(zero ? Math.min(0, ...ys) : Math.min(...ys), Math.max(...ys));
  const y0 = yt[0], y1 = yt.at(-1);
  const X = (v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `${series.map((x) => x.name).join(", ")} 추이` });
  if (band && band.from < x1) svg.append(s("rect", { class: "future-band", x: X(Math.max(band.from, x0)), y: m.t, width: X(x1) - X(Math.max(band.from, x0)), height: H - m.t - m.b }));
  for (const v of yt) {
    svg.append(s("line", { class: v === y0 ? "base-line" : "grid-line", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    svg.append(s("text", { class: "axis-text", x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, String(+v.toFixed(2))));
  }
  // x 눈금
  const nX = Math.max(2, Math.min(8, Math.floor((W - m.l - m.r) / 90)));
  if (xType === "number") {
    const stepT = Math.max(1, Math.ceil((x1 - x0) / nX));
    for (let t = Math.ceil(x0); t <= x1; t += stepT) {
      svg.append(s("text", { class: "axis-text", x: X(t), y: H - 6, "text-anchor": "middle" }, String(t)));
    }
  } else {
    const spanDays = (x1 - x0) / 86400e3;
    for (let i = 0; i <= nX; i++) {
      const t = x0 + ((x1 - x0) * i) / nX;
      const d = new Date(t);
      const label = spanDays > 2 ? `${d.getMonth() + 1}/${d.getDate()}` : `${d.getDate()}일 ${pad(d.getHours())}시`;
      svg.append(s("text", { class: "axis-text", x: X(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === nX ? "end" : "middle" }, label));
    }
  }
  for (const mk of markers.filter((mk) => mk.x >= x0 && mk.x <= x1)) {
    const mx = X(mk.x), right = mx > W - m.r - 40;
    svg.append(s("line", { class: "marker-line", x1: mx, x2: mx, y1: m.t - 4, y2: H - m.b }));
    svg.append(s("text", { class: "marker-text", x: right ? mx - 4 : mx + 4, y: m.t - 8, "text-anchor": right ? "end" : "start" }, mk.label));
  }
  for (const ser of series) {
    if (!ser.points.length) continue;
    let d = "", prev = null;
    for (const [tx, ty] of ser.points) {
      const brk = prev === null || (ser.gap && tx - prev > ser.gap);
      d += `${brk ? "M" : "L"}${X(tx).toFixed(1)},${Y(ty).toFixed(1)}`;
      prev = tx;
    }
    svg.append(s("path", {
      d, fill: "none", stroke: ser.color, "stroke-width": ser.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      ...(ser.dashed ? { "stroke-dasharray": "5 4" } : {}),
    }));
    if (ser.dots || ser.points.length === 1) {
      for (const [tx, ty] of ser.points) svg.append(s("circle", { cx: X(tx), cy: Y(ty), r: 3.5, fill: ser.color, stroke: "var(--surface)", "stroke-width": 1.5 }));
    }
  }

  // 크로스헤어 + 툴팁 (가장 가까운 x로 스냅, 그 x의 모든 시리즈 값)
  const cross = s("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const dots = series.map((ser) => s("circle", { r: 4, fill: ser.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }));
  const hit = s("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
  svg.append(cross, ...dots, hit);
  const lookup = series.map((ser) => new Map(ser.points));
  const times = [...new Set(xs)].sort((a, b) => a - b);
  const tip = $("#tooltip");
  const move = (clientX, clientY) => {
    const rect = svg.getBoundingClientRect();
    const px = ((clientX - rect.left) / rect.width) * W;
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
      rows.push(h("div", { class: "r" },
        h("span", { class: `key${ser.dashed ? " dash" : ""}`, style: ser.dashed ? `border-color:${ser.color}` : `background:${ser.color}` }),
        h("b", {}, fix(v)), h("span", {}, ser.name)));
    });
    const title = xType === "number" ? `${tx}${xUnit}` : fmtWhen(tx);
    tip.replaceChildren(h("div", { class: "t" }, title), ...rows);
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = clientX + 14, top = clientY - th - 12;
    if (left + tw > window.innerWidth - 8) left = clientX - tw - 14;
    if (top < 8) top = clientY + 16;
    tip.style.left = `${left}px`; tip.style.top = `${top}px`;
  };
  hit.addEventListener("pointermove", (ev) => move(ev.clientX, ev.clientY));
  hit.addEventListener("pointerleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  });
  el.replaceChildren(svg);
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => state.series && state.summary && drawAll(), 150);
});

// ================================================================== 동작: 업데이트 / 모델 추가 / 시리즈 추가
async function waitTrained(modelId) {
  for (let i = 0; i < 120; i++) {
    const r = await api("GET", `/models/${modelId}`);
    if (r.ok && (r.data.status === "done" || r.data.status === "failed")) return r.data;
    await sleep(500);
  }
  return null;
}

function liveHorizon(freq) { return freq === "hourly" ? 24 : 7; }

async function backtestAndGoLive(model, series, lastTs) {
  const stepMs = STEP_MS[series.frequency];
  const span = (Date.parse(lastTs) - Date.parse(model.train_cutoff)) / stepMs;
  if (span >= 1) {
    const every = Math.max(series.frequency === "hourly" ? 6 : 1, Math.ceil(span / 800));
    await api("POST", `/models/${model.id}/backtest`, { start: model.train_cutoff, end: lastTs, every, horizon: liveHorizon(series.frequency) });
  }
  // 지금 시점 예측 봉인 (이미 있으면 409 → 무시)
  await api("POST", "/forecasts", { model_id: model.id, horizon: liveHorizon(series.frequency) });
}

$("#refresh-btn").addEventListener("click", (ev) => busy(ev.currentTarget, async () => {
  const s = state.series;
  let note = "";
  if (s.source !== "manual") {
    const r = await api("POST", `/series/${s.id}/ingest`);
    if (!r.ok) { toast(`데이터 수집 실패: ${errText(r)}`, "error"); return; }
    note = r.data.inserted ? `새 관측치 ${r.data.inserted}개` : "새 관측치 없음";
  }
  for (const m of state.models.filter((x) => x.status === "done")) {
    await api("POST", "/forecasts", { model_id: m.id, horizon: liveHorizon(s.frequency) });
  }
  const sc = await api("POST", `/series/${s.id}/score`);
  if (sc.ok) note += `${note ? " · " : ""}새로 채점 ${sc.data.newly_scored}개`;
  toast(note || "업데이트했습니다");
  await loadDashboard();
}));

// ---- 모델 추가
async function loadMethods() {
  const r = await api("GET", "/methods");
  if (!r.ok) return;
  $("#method-select").replaceChildren(...r.data.map((m) => h("option", { value: m.name, "data-desc": m.description }, METHOD_LABEL[m.name] || m.name)));
  onMethodChange();
}
function onMethodChange() {
  const opt = $("#method-select").selectedOptions[0];
  if (!opt) return;
  $("#method-desc").textContent = opt.dataset.desc || "";
  const base = opt.value;
  const taken = new Set(state.models.map((m) => m.name));
  let name = base, i = 2;
  while (taken.has(name)) name = `${base}-${i++}`;
  $("#model-form").model_name.value = name;
}
$("#method-select").addEventListener("change", onMethodChange);

$("#add-model-btn").addEventListener("click", () => {
  if (!state.summary?.count) { toast("먼저 데이터를 가져와야 모델을 학습할 수 있습니다.", "error"); return; }
  onMethodChange();
  $("#model-msg").textContent = "";
  $("#model-dialog").showModal();
});

$("#model-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const msg = $("#model-msg");
  const s = state.series, sum = state.summary;
  const ratio = Number(f.cutoff.value);
  const stepMs = STEP_MS[s.frequency];
  const a = Date.parse(sum.first_ts), b = Date.parse(sum.last_ts);
  const body = { series_id: s.id, name: f.model_name.value.trim(), method: f.model_method.value };
  if (ratio < 1) body.train_cutoff = toIso(a + Math.round(((b - a) / stepMs) * ratio) * stepMs);
  busy(ev.submitter, async () => {
    msg.className = "msg";
    msg.textContent = "학습 중…";
    const r = await api("POST", "/models", body);
    if (!r.ok) { msg.className = "msg error"; msg.textContent = errText(r); return; }
    const model = await waitTrained(r.data.id);
    if (!model || model.status !== "done") {
      msg.className = "msg error";
      msg.textContent = model?.error ? `학습 실패: ${model.error}` : "학습이 끝나지 않았습니다. 잠시 후 다시 확인해 주세요.";
      await loadDashboard();
      return;
    }
    msg.textContent = ratio < 1 ? "과거 구간으로 성적 매기는 중…" : "예측 봉인 중…";
    await backtestAndGoLive(model, s, sum.last_ts);
    $("#model-dialog").close();
    toast(`'${model.name}' 모델을 추가했습니다`);
    await loadDashboard();
  });
});

// ---- 시리즈 추가
function openSeriesDialog() {
  $("#series-msg").textContent = "";
  $("#series-form").reset();
  syncSourceFields();
  $("#series-dialog").showModal();
}
function syncSourceFields() {
  const src = $("#series-form").source.value;
  for (const el of document.querySelectorAll("#series-form [data-for]")) el.hidden = el.dataset.for !== src;
  const f = $("#series-form");
  if (!f.series_name.dataset.touched) f.series_name.value = src === "open_meteo" ? `${f.city.selectedOptions[0].textContent} 기온` : "샘플 시리즈";
}
$("#series-form").addEventListener("change", (ev) => {
  if (ev.target.name === "series_name") ev.target.dataset.touched = "1";
  syncSourceFields();
});
$("#add-series-btn").addEventListener("click", openSeriesDialog);
$("#add-series-menu").addEventListener("click", () => { $(".menu").open = false; openSeriesDialog(); });

$("#series-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const msg = $("#series-msg");
  const source = f.source.value;
  let source_config = { backfill: 1000 };
  if (source === "open_meteo") {
    const [lat, lon] = f.city.value.split(",").map(Number);
    source_config = { latitude: lat, longitude: lon };
  }
  busy(ev.submitter, async () => {
    msg.className = "msg";
    msg.textContent = "만드는 중…";
    const r = await api("POST", "/series", { name: f.series_name.value.trim(), frequency: f.frequency.value, source, source_config });
    if (!r.ok) { msg.className = "msg error"; msg.textContent = r.status === 409 ? "같은 이름의 시리즈가 이미 있습니다." : errText(r); return; }
    msg.textContent = "데이터 가져오는 중…";
    const ing = await api("POST", `/series/${r.data.id}/ingest`);
    $("#series-dialog").close();
    delete f.series_name.dataset.touched;
    if (!ing.ok) toast(`시리즈는 만들었지만 데이터 수집에 실패했습니다: ${errText(ing)}`, "error");
    else toast(`관측치 ${ing.data.inserted.toLocaleString()}개를 가져왔습니다. 이제 모델을 추가해 보세요.`);
    await loadSeriesList(r.data.id);
  });
});

for (const btn of document.querySelectorAll("[data-close]")) btn.addEventListener("click", () => btn.closest("dialog").close());

// ---- 샘플 시리즈 (한 번에 결과까지)
async function buildDemo(report) {
  let name = "샘플 · 하루 주기 패턴";
  const taken = new Set(state.seriesList.map((x) => x.name));
  for (let i = 2; taken.has(name); i++) name = `샘플 · 하루 주기 패턴 ${i}`;
  report("시리즈 만드는 중…");
  const sr = await api("POST", "/series", { name, frequency: "hourly", source: "synthetic", source_config: { backfill: 1000, amplitude: 5, noise: 0.8 } });
  if (!sr.ok) throw new Error(errText(sr));
  const series = sr.data;
  report("데이터 가져오는 중…");
  const ir = await api("POST", `/series/${series.id}/ingest`);
  if (!ir.ok) throw new Error(errText(ir));
  const sum = (await api("GET", `/series/${series.id}/summary`)).data;
  const a = Date.parse(sum.first_ts), b = Date.parse(sum.last_ts);
  const cutoff = toIso(a + Math.round(((b - a) / 3600e3) * 0.6) * 3600e3);
  for (const method of ["naive", "seasonal_naive", "ses", "drift"]) {
    report(`${METHOD_LABEL[method]} 학습·채점 중…`);
    const mr = await api("POST", "/models", { series_id: series.id, name: method, method, train_cutoff: cutoff });
    if (!mr.ok) throw new Error(errText(mr));
    const model = await waitTrained(mr.data.id);
    if (model?.status === "done") await backtestAndGoLive(model, series, sum.last_ts);
  }
  return series.id;
}
async function runDemo(button, report) {
  await busy(button, async () => {
    try {
      const id = await buildDemo(report);
      report("");
      await loadSeriesList(id);
      toast("샘플 시리즈를 만들었습니다");
    } catch (e) {
      report("");
      toast(`샘플 만들기 실패: ${e.message}`, "error");
    }
  });
}
$("#demo-btn").addEventListener("click", (ev) => runDemo(ev.currentTarget, (t) => ($("#demo-msg").textContent = t)));
$("#demo-menu").addEventListener("click", (ev) => {
  $(".menu").open = false;
  runDemo(ev.currentTarget, (t) => t && toast(t));
});

// ---- 시리즈 삭제
$("#delete-series-btn").addEventListener("click", async () => {
  const s = state.series;
  if (!confirm(`'${s.name}'의 관측치·모델·예측 기록이 모두 삭제됩니다. 계속할까요?`)) return;
  const r = await api("DELETE", `/series/${s.id}`);
  if (!r.ok) { toast(errText(r), "error"); return; }
  toast("삭제했습니다");
  state.series = null;
  await loadSeriesList();
});

document.addEventListener("click", (ev) => {
  const menu = $(".menu");
  if (menu?.open && !menu.contains(ev.target)) menu.open = false;
});

// ================================================================== 시작
(async function init() {
  const token = storage("hindsight.token");
  if (token) {
    state.token = token;
    const me = await api("GET", "/auth/me");
    if (me.ok) { state.email = me.data.email; await enterApp(); return; }
    setSession(null, null);
  }
  $("#auth-view").hidden = false;
})();
