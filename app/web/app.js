/* Pointwise Delivery — dependency-free frontend. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const CLASS_ORDER = [
  "possible_effective",
  "dominated",
  "insufficient_evidence",
  "threshold_blocked",
  "preference_excluded",
];
const STATUS_ICON = {
  possible_effective: "✓",
  dominated: "▼",
  insufficient_evidence: "?",
  threshold_blocked: "⛔",
  preference_excluded: "↔",
};
const PRESET_LABELS = {
  broad: "宽松范围",
  cost_first: "成本优先带",
  quality_energy: "质量/能耗优先带",
  equal_band: "等权窄带",
  none: "不设红线",
  normal: "稳健常规红线",
  contradiction: "矛盾高压红线",
};

const state = {
  seed: null,
  dataset: null,
  analysis: null,
  recommendations: null,
  enumReport: null,
  weightRanges: {},
  thresholds: [],
  selectedAltId: null,
  selectedPairKey: null,
  pastDatasets: [],
  appliedRecords: [],
  latestDiff: null,
  refreshTimer: null,
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
function fmt(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return esc(value);
  return n.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}
function pct(value) {
  return `${Math.round(Number(value) * 100)}%`;
}
function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.className = "toast";
  }, 4200);
}
async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `请求失败 ${response.status}`);
  }
  return payload;
}
function metricById(id) {
  return state.dataset?.metrics?.find((m) => m.id === id) || { id, name: id };
}
function altById(id) {
  return state.dataset?.alternatives?.find((a) => a.id === id);
}
function analysisAlt(id) {
  return state.analysis?.alternatives?.find((a) => a.id === id);
}
function currentOptions() {
  return {
    weight_ranges: clone(state.weightRanges),
    thresholds: clone(state.thresholds),
  };
}

/* ----------------------------- Initialization ---------------------------- */

async function init() {
  initTheme();
  bindStaticEvents();
  try {
    const seed = await fetch("/api/seed").then((r) => r.json());
    resetWorkspace(seed);
  } catch (error) {
    showToast(`载入示例数据失败：${error.message}`, true);
  }
}

function initTheme() {
  const saved = localStorage.getItem("pointwise-theme");
  const dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("#themeToggle").textContent = dark ? "切换浅色" : "切换深色";
}

function bindStaticEvents() {
  $("#themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("pointwise-theme", next);
    $("#themeToggle").textContent = next === "dark" ? "切换浅色" : "切换深色";
  });

  $("#downloadTemplate").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(state.seed, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "pointwise-delivery-template.json";
    a.click();
    URL.revokeObjectURL(url);
  });

  $("#importFile").addEventListener("change", handleImport);
  $("#resetSeed").addEventListener("click", () => resetWorkspace(state.seed));
  $("#undoMeasure").addEventListener("click", undoMeasurement);
  $("#customMeasureForm").addEventListener("submit", submitCustomMeasurement);
  $("#runEnumBtn").addEventListener("click", () => runEnumeration());
  $("#enumBtnTop").addEventListener("click", () => {
    document.getElementById("enumerationSection").scrollIntoView({ behavior: "smooth" });
    runEnumeration();
  });

  document.addEventListener("input", handleControlInput);
  document.addEventListener("change", handleControlChange);
  document.addEventListener("click", handleDelegatedClick);
  document.addEventListener("submit", () => {});
}

function resetWorkspace(seed) {
  if (!seed) return;
  state.seed = clone(seed);
  state.dataset = clone(seed);
  state.weightRanges = clone(seed.weight_ranges || {});
  for (const m of seed.metrics || []) {
    if (!state.weightRanges[m.id]) state.weightRanges[m.id] = [0, 1];
  }
  state.thresholds = clone(seed.thresholds || []);
  state.selectedAltId = seed.alternatives?.[1]?.id || seed.alternatives?.[0]?.id || null;
  state.selectedPairKey = null;
  state.pastDatasets = [];
  state.appliedRecords = [];
  state.latestDiff = null;
  state.enumReport = null;
  renderDatasetHeader();
  renderPresets();
  renderWeightControls();
  renderThresholdControls();
  renderCustomForm();
  renderApplied();
  refreshAnalysisAndRecommendations();
  // Show the independent brute-force check immediately; the button can rerun it
  // after preference or data changes.
  runEnumeration();
}

/* --------------------------------- Render -------------------------------- */

function renderDatasetHeader() {
  $("#datasetName").textContent = state.dataset.name || "未命名决策数据";
  $("#datasetDescription").textContent = state.dataset.description || "";
  if (state.analysis?.principle) $("#principleBox").querySelector("p").textContent = state.analysis.principle;
}

function renderPresets() {
  const weightRow = $("#weightPresetRow");
  weightRow.innerHTML = `<span class="preset-label">权重预设</span>` +
    Object.entries(state.dataset.weight_presets || {})
      .map(([key]) => `<button class="btn small ghost" type="button" data-weight-preset="${key}">${PRESET_LABELS[key] || key}</button>`)
      .join("");

  const thresholdRow = $("#thresholdPresetRow");
  thresholdRow.innerHTML = `<span class="preset-label">阈值预设</span>` +
    Object.entries(state.dataset.threshold_presets || {})
      .map(([key]) => `<button class="btn small ghost" type="button" data-threshold-preset="${key}">${PRESET_LABELS[key] || key}</button>`)
      .join("");
}

function renderWeightControls() {
  $("#weightRows").innerHTML = state.dataset.metrics
    .map((m) => {
      const [lo, hi] = state.weightRanges[m.id] || [0, 1];
      const left = Math.max(0, Math.min(1, lo)) * 100;
      const width = Math.max(0, Math.min(1, hi - lo)) * 100;
      return `
        <div class="weight-row">
          <div class="weight-name">${esc(m.name)}<small>${m.direction === "max" ? "越高越好" : "越低越好"} · ${esc(m.unit || "")}</small></div>
          <div>
            <div class="range-inputs">
              <input type="number" min="0" max="1" step="0.05" aria-label="${esc(m.name)}权重下限" data-weight-lo="${m.id}" value="${lo}">
              <span>–</span>
              <input type="number" min="0" max="1" step="0.05" aria-label="${esc(m.name)}权重上限" data-weight-hi="${m.id}" value="${hi}">
            </div>
            <div class="weight-band" aria-hidden="true"><span style="left:${left}%;width:${width}%"></span></div>
          </div>
          <div class="weight-values" data-weight-value="${m.id}">${pct(lo)}–${pct(hi)}</div>
        </div>`;
    })
    .join("");
  updateWeightFeasibility();
}

function updateWeightFeasibility() {
  const lows = state.dataset.metrics.map((m) => Number(state.weightRanges[m.id]?.[0] ?? 0));
  const highs = state.dataset.metrics.map((m) => Number(state.weightRanges[m.id]?.[1] ?? 1));
  const sumLo = lows.reduce((a, b) => a + b, 0);
  const sumHi = highs.reduce((a, b) => a + b, 0);
  const el = $("#weightFeasibility");
  if (sumLo > 1.0000001 || sumHi < 0.9999999) {
    el.className = "feasibility bad";
    el.textContent = `权重范围不相容：下限合计 ${fmt(sumLo)}，上限合计 ${fmt(sumHi)}；归一化权重要求存在 w₁+…+wₙ=1。请放宽范围，系统不会自行挑选隐藏权重。`;
  } else {
    el.className = "feasibility good";
    el.textContent = `范围可行：下限合计 ${fmt(sumLo)} ≤ 1 ≤ 上限合计 ${fmt(sumHi)}。下列“最优见证”给出该范围内真实存在的一组权重。`;
  }
}

function renderThresholdControls() {
  const rows = state.thresholds;
  $("#thresholdRows").innerHTML = rows
    .map((th, i) => {
      const m = metricById(th.metric_id);
      const op = th.op || (m.direction === "max" ? ">=" : "<=");
      return `
        <div class="threshold-row">
          <label class="lock"><input type="checkbox" data-th-lock="${i}" ${th.locked ? "checked" : ""}> 锁定</label>
          <div class="weight-name">${esc(m.name)}<small>${esc(m.unit || "")}</small></div>
          <select data-th-op="${i}" aria-label="比较符">
            <option value="<="${op === "<=" ? " selected" : ""}>≤</option>
            <option value=">="${op === ">=" ? " selected" : ""}>≥</option>
          </select>
          <input type="number" step="0.1" data-th-value="${i}" value="${esc(th.value)}" aria-label="阈值">
          <span class="threshold-state" data-th-state="${i}">${th.locked ? "待计算" : "未锁定"}</span>
        </div>`;
    })
    .join("");
}

function updateThresholdStates() {
  if (!state.analysis) return;
  state.thresholds.forEach((th, i) => {
    const pill = $(`[data-th-state="${i}"]`);
    if (!pill) return;
    if (!th.locked) {
      pill.className = "threshold-state state-idle";
      pill.textContent = "未锁定";
      return;
    }
    const states = state.analysis.alternatives.map((a) => a.threshold_state);
    if (states.every((s) => s === "eligible")) {
      pill.className = "threshold-state state-pass";
      pill.textContent = "全部稳健通过";
    } else if (state.analysis.threshold_contradiction?.type === "no_joint_point_passes_all_locks") {
      pill.className = "threshold-state state-fail";
      pill.textContent = "无联合达标点";
    } else {
      pill.className = "threshold-state state-risk";
      pill.textContent = "存在风险/排除";
    }
  });
}

function renderAnalysis() {
  const a = state.analysis;
  if (!a) return;
  $("#datasetName").textContent = a.dataset_name;
  $("#principleBox").querySelector("p").textContent = a.principle;
  renderKpis();
  renderConflict();
  renderLegend();
  renderCandidates();
  renderDetail();
  renderRangeOverview();
  renderScenarioTable();
  updateThresholdStates();
}

function renderLegend() {
  $("#classLegend").innerHTML = CLASS_ORDER.map((c) => {
    const dot = c === "possible_effective" ? "good" : c === "dominated" ? "critical" : c === "insufficient_evidence" ? "warning" : "neutral";
    return `<span class="legend-item"><i class="dot ${dot}"></i>${state.analysis.alternatives.find((x) => x.decision_class === c)?.class_label || c}</span>`;
  }).join("");
}

function renderKpis() {
  const { counts, ambiguity } = state.analysis;
  const cards = [
    { num: counts.total, label: "虚构候选方案", sub: `${state.analysis.scenario_count} 个联合场景`, cls: "info" },
    { num: counts.possible_effective, label: "可能有效", sub: `最优见证：${state.analysis.possible_winner_ids.join("、") || "无"}`, cls: "good" },
    { num: counts.dominated, label: "确定被支配", sub: "同场景全维不优且有严格差", cls: "critical" },
    { num: counts.insufficient_evidence, label: "证据不足", sub: "缺联合证据或阈值不可稳健保证", cls: "warning" },
    { num: ambiguity.score, label: "决策歧义指数", sub: `多余最优 ${ambiguity.choice_ambiguity} · 权衡对 ${ambiguity.unresolved_tradeoff_pairs}`, cls: "info" },
    { num: state.analysis.eligible_ids.length, label: "稳健入围", sub: `阈值排除 ${counts.threshold_blocked} · 偏好外 ${counts.preference_excluded}`, cls: "" },
  ];
  $("#kpiGrid").innerHTML = cards
    .map((c) => `<div class="kpi ${c.cls}"><div class="num">${c.num}</div><div class="label">${esc(c.label)}</div><div class="sub">${esc(c.sub)}</div></div>`)
    .join("");
}

function renderConflict() {
  const banner = $("#conflictBanner");
  const conflict = state.analysis.threshold_contradiction;
  if (!conflict) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  banner.hidden = false;
  const relax = (conflict.relaxation_candidates || []).slice(0, 3);
  banner.innerHTML = `
    <h2>⚠ ${esc(conflict.title)}</h2>
    <p>${esc(conflict.detail)}</p>
    <div class="conflict-grid">
      ${relax
        .map((r) => {
          const alt = altById(r.alternative_id);
          const vals = Object.entries(r.required_threshold_values)
            .map(([mid, v]) => `${metricById(mid).name} ${metricById(mid).direction === "max" ? "≥" : "≤"} ${fmt(v)}`)
            .join("；");
          return `<div class="relax-card"><strong>${esc(r.alternative_id)} · ${esc(alt?.name || "")}</strong><br><span class="hint">同方案联合包络要求：${esc(vals)}</span></div>`;
        })
        .join("")}
    </div>`;
}

function renderCandidates() {
  $("#candidateList").innerHTML = state.analysis.alternatives
    .map((a) => {
      const alt = altById(a.id) || {};
      const selected = a.id === state.selectedAltId ? "selected" : "";
      const bounds = state.dataset.metrics
        .map((m) => {
          const b = a.bounds?.metrics?.[m.id];
          const text = b ? `${fmt(b.min, 1)}–${fmt(b.max, 1)}` : "缺证据";
          return `<div class="mini-bound"><span>${esc(m.name)}</span><strong title="${b?.source || ""}">${text}</strong></div>`;
        })
        .join("");
      const tags = [
        ...(alt.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`),
        ...(a.equivalent_to?.length ? [`<span class="tag equiv">≡ 与 ${a.equivalent_to.join(", ")} 完全等价</span>`] : []),
      ].join("");
      return `
        <div class="candidate ${selected}" role="button" tabindex="0" data-select-alt="${esc(a.id)}">
          <div class="candidate-main">
            <div class="candidate-title">
              <span class="candidate-id">${esc(a.id)}</span>
              <span class="candidate-name">${esc(a.name)}</span>
              <span class="badge ${a.decision_class}"><span class="icon">${STATUS_ICON[a.decision_class] || "•"}</span>${esc(a.class_label)}</span>
            </div>
            <div class="candidate-process">${esc(alt.process || a.process || "")}</div>
            <div class="mini-bounds">${bounds}</div>
            <div class="candidate-meta">${tags}</div>
          </div>
          <div class="candidate-side">
            <span class="btn small secondary" data-select-alt="${esc(a.id)}">查看见证</span>
          </div>
        </div>`;
    })
    .join("");
}

/* ------------------------------- Detail view ----------------------------- */

function pairKey(pair) {
  return [pair.a, pair.b].sort().join("__");
}
function getSelectedAlt() {
  return analysisAlt(state.selectedAltId) || state.analysis?.alternatives?.[0];
}
function getPairForSelected() {
  const a = getSelectedAlt();
  if (!a) return null;
  const pairs = state.analysis.pairs.filter((p) => p.a === a.id || p.b === a.id);
  let pair = pairs.find((p) => pairKey(p) === state.selectedPairKey);
  if (!pair) {
    const dominator = a.dominated_by?.[0];
    pair = pairs.find((p) => p.a === dominator || p.b === dominator) || pairs[0] || null;
    state.selectedPairKey = pair ? pairKey(pair) : null;
  }
  return pair;
}
function pairView(pair, altId) {
  if (!pair) return null;
  const reverse = pair.b === altId;
  const rival = reverse ? pair.a : pair.b;
  const mapComp = (c) => {
    if (!reverse) return c;
    if (c === "a_better") return "b_better";
    if (c === "b_better") return "a_better";
    return c;
  };
  return {
    candidate: altId,
    rival,
    relation: pair.relation,
    candidate_dominates: reverse ? pair.b_dominates_a : pair.a_dominates_b,
    rival_dominates: reverse ? pair.a_dominates_b : pair.b_dominates_a,
    equivalent: pair.equivalent,
    rows: pair.rows.map((row) => ({
      ...row,
      metrics: row.metrics.map((cell) => {
        const comp = mapComp(cell.comparison);
        return {
          ...cell,
          candidate_value: reverse ? cell.b_value : cell.a_value,
          rival_value: reverse ? cell.a_value : cell.b_value,
          candidate_comparison: comp,
        };
      }),
    })),
  };
}

function renderDetail() {
  const host = $("#detailContent");
  const a = getSelectedAlt();
  if (!a) {
    host.innerHTML = `<div class="detail-empty">请选择一个方案查看见证。</div>`;
    return;
  }
  state.selectedAltId = a.id;
  const alt = altById(a.id) || {};
  const pair = getPairForSelected();
  const view = pair ? pairView(pair, a.id) : null;
  const pairOptions = state.analysis.pairs
    .filter((p) => p.a === a.id || p.b === a.id)
    .map((p) => {
      const rivalId = p.a === a.id ? p.b : p.a;
      const rival = altById(rivalId);
      return `<option value="${pairKey(p)}"${pairKey(p) === state.selectedPairKey ? " selected" : ""}>vs ${esc(rivalId)} ${esc(rival?.name || "")} — ${relationLabel(p, a.id)}</option>`;
    })
    .join("");

  host.innerHTML = `
    <div class="detail-section">
      <div class="candidate-title">
        <span class="candidate-id">${esc(a.id)}</span>
        <h2 style="margin:0">${esc(a.name)}</h2>
        <span class="badge ${a.decision_class}"><span>${STATUS_ICON[a.decision_class] || "•"}</span>${esc(a.class_label)}</span>
      </div>
      <p class="muted">${esc(alt.notes || a.notes || "")}</p>
      <dl class="kv-list">
        <div class="kv"><dt>判定理由</dt><dd>${esc(a.reason)}</dd></div>
        ${a.dominated_by?.length ? `<div class="kv"><dt>支配方案</dt><dd>${a.dominated_by.map((id) => `<strong>${esc(id)}</strong> ${esc(altById(id)?.name || "")}`).join("；")}</dd></div>` : ""}
        ${a.equivalent_to?.length ? `<div class="kv"><dt>完全等价</dt><dd>${a.equivalent_to.map(esc).join("、")}（所有联合场景、所有指标一致，不判为互相支配）</dd></div>` : ""}
      </dl>
    </div>

    <div class="detail-section">
      <p class="eyebrow">支配 / 反支配见证</p>
      <h3>选择对照方案</h3>
      ${pairOptions ? `<select class="pair-select" data-pair-select="${esc(a.id)}">${pairOptions}</select>` : `<p class="hint">该方案缺少完整联合场景，尚不能进行可审计的成对支配枚举。</p>`}
      <div id="pairProof">${view ? renderPairProof(view) : renderEvidenceGap(a)}</div>
    </div>

    <div class="detail-section">
      <p class="eyebrow">权重范围见证</p>
      <h3>它能否在偏好范围内成为最优？</h3>
      <div id="witnessBox">${renderWitness(a.optimal_witness)}</div>
      ${a.conditional_optimal_witness ? `<h3 style="margin-top:14px">条件场景见证（不绕过稳健红线）</h3>${renderWitness(a.conditional_optimal_witness, true)}` : ""}
    </div>`;
}

function relationLabel(pair, selectedId) {
  if (pair.equivalent) return "完全等价";
  if (pair.a_dominates_b) return pair.a === selectedId ? "本方案支配对手" : "被对手支配";
  if (pair.b_dominates_a) return pair.b === selectedId ? "本方案支配对手" : "被对手支配";
  return "有权衡/互相反证";
}

function renderEvidenceGap(a) {
  return `<div class="diff-box" style="background:var(--warning-soft);color:var(--warning-text)">
    <strong>不能逐维拼端点。</strong><br>${esc(a.reason)}
    ${a.missing_evidence?.length ? `<div class="code-line">缺失：${esc(a.missing_evidence.slice(0, 8).join(", "))}${a.missing_evidence.length > 8 ? " …" : ""}</div>` : ""}
    建议运行“${esc(state.dataset.measurements?.find((m) => m.target?.alternative_id === a.id)?.name || "联合测量")}”。
  </div>`;
}

function renderPairProof(view) {
  let headline;
  if (view.equivalent) headline = `<strong>${view.candidate}</strong> 与 <strong>${view.rival}</strong> 在全部 ${view.rows.length} 个场景完全等价。`;
  else if (view.rival_dominates) headline = `<strong>${view.rival}</strong> 在全部联合场景中不劣于 <strong>${view.candidate}</strong>，且至少一维严格更好，因此 ${view.candidate} 确定被支配。`;
  else if (view.candidate_dominates) headline = `<strong>${view.candidate}</strong> 稳健支配 <strong>${view.rival}</strong>。`;
  else headline = `两者未形成确定支配：下表中任一“候选优”或“对手优”单元格都是阻断单向支配的联合场景见证。`;

  return `
    <p>${headline}</p>
    <div class="table-scroll">
      <table class="proof-table">
        <thead><tr><th>联合场景</th>${state.dataset.metrics.map((m) => `<th>${esc(m.name)}<br><span class="muted">${esc(m.unit || "")}</span></th>`).join("")}</tr></thead>
        <tbody>
          ${view.rows
            .map(
              (row) => `<tr>
                <td><strong>${esc(row.scenario_id)}</strong><br><span class="muted">${esc(row.scenario_name)}</span></td>
                ${row.metrics
                  .map((cell) => {
                    const cls = `cell-${cell.candidate_comparison}`;
                    const mark = cell.candidate_comparison === "a_better" ? "▲ 候选优" : cell.candidate_comparison === "b_better" ? "▼ 对手优" : "＝ 相等";
                    return `<td class="${cls}"><span class="cell-compare"><span class="vals">${fmt(cell.candidate_value)} / ${fmt(cell.rival_value)}</span><span class="mark">${mark}</span></span></td>`;
                  })
                  .join("")}
              </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
    <p class="hint">单元格数值顺序为“候选 / 对照”；每一列比较都取自同一行场景，未跨场景选择最好或最坏端点。</p>`;
}

function renderWitness(witness, conditional = false) {
  if (!witness) {
    return `<div class="diff-box" style="background:var(--surface-2)">当前没有满足全部约束的最优见证。可能是权重范围过窄，或该方案在现有联合场景中不会成为加权最优。</div>`;
  }
  const metricIds = state.dataset.metrics.map((m) => m.id);
  const colors = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4, #eda100)"];
  const entries = metricIds.map((id, i) => ({ id, value: witness.weights[id] || 0, color: colors[i] }));
  return `
    <div class="weights-witness">
      <div class="stacked-weight" aria-label="见证权重堆叠条">
        ${entries.map((e) => `<span style="width:${e.value * 100}%;background:${e.color}"></span>`).join("")}
      </div>
      <div class="weight-chips">
        ${entries.map((e) => `<span class="weight-chip" style="background:${e.color}">${esc(metricById(e.id).name)} ${pct(e.value)}</span>`).join("")}
      </div>
      <p class="hint">${conditional ? "条件：" : ""}联合场景 <strong>${esc(witness.scenario_id)} · ${esc(witness.scenario_name)}</strong>；领先第二名 ${fmt(witness.margin_over_runner_up, 4)} 个归一化分。${witness.active_ties?.length ? ` 边界约束：${witness.active_ties.join(", ")} 打平。` : ""}</p>
      <table class="score-table">
        <thead><tr><th>方案</th><th>归一化加权得分</th></tr></thead>
        <tbody>
          ${witness.scores
            .map((s) => `<tr class="${s.alternative_id === state.selectedAltId ? "highlight" : ""}"><td>${esc(s.alternative_id)} ${esc(altById(s.alternative_id)?.name || "")}</td><td>${fmt(s.score, 4)}</td></tr>`)
            .join("")}
        </tbody>
      </table>
      <p class="hint">${esc(witness.weight_constraint_note)}</p>
    </div>`;
}

/* ------------------------------ Range charts ----------------------------- */

function renderRangeOverview() {
  $("#rangeOverview").innerHTML = state.dataset.metrics
    .map((m, metricIndex) => {
      const span = m.scale[1] - m.scale[0];
      const rows = state.analysis.alternatives
        .map((a) => {
          const b = a.bounds?.metrics?.[m.id];
          if (!b) {
            return `<div class="range-row"><span class="rid">${esc(a.id)}</span><div class="range-track"></div><span class="range-values">缺联合证据</span></div>`;
          }
          const left = ((b.min - m.scale[0]) / span) * 100;
          const width = ((b.max - b.min) / span) * 100;
          const marginal = b.source === "marginal_interval";
          const ticks = state.dataset.scenarios
            .map((s) => {
              const raw = s.values?.[a.id]?.[m.id];
              if (raw === undefined) return "";
              const x = ((raw - m.scale[0]) / span) * 100;
              return `<i class="range-tick" style="left:${x}%" title="${esc(s.id)} ${esc(s.name)}: ${fmt(raw)} ${esc(m.unit || "")}"></i>`;
            })
            .join("");
          const endpointNote = b.min_scenario ? `${b.min_scenario} → ${b.max_scenario}` : "边际区间，端点共存性未知";
          return `
            <div class="range-row" title="${esc(endpointNote)}">
              <span class="rid">${esc(a.id)}</span>
              <div class="range-track">
                <div class="range-fill ${marginal ? "marginal" : ""}" style="left:${left}%;width:${Math.max(width, 0.8)}%"></div>
                ${ticks}
              </div>
              <span class="range-values">${fmt(b.min, 1)}–${fmt(b.max, 1)}</span>
            </div>`;
        })
        .join("");
      return `<section class="range-metric">
        <h3>${esc(m.name)} <span>${m.direction === "max" ? "越高越好" : "越低越好"} · ${esc(m.unit || "")}</span></h3>
        ${rows}
      </section>`;
    })
    .join("");
}

function renderScenarioTable() {
  const metricIds = state.dataset.metrics.map((m) => m.id);
  const rows = [];
  for (const s of state.dataset.scenarios) {
    for (const alt of state.dataset.alternatives) {
      const values = s.values?.[alt.id];
      if (!values) continue;
      rows.push({ scenario: s, alt, values });
    }
  }
  const marginalAlts = state.dataset.alternatives.filter((a) => a.evidence_mode === "marginal_only" || !state.dataset.scenarios.some((s) => s.values?.[a.id]));
  $("#scenarioTable").innerHTML = `
    <table class="data-table">
      <thead><tr><th>联合场景</th><th>方案</th>${state.dataset.metrics.map((m) => `<th>${esc(m.name)} (${esc(m.unit || "")})</th>`).join("")}</tr></thead>
      <tbody>
        ${rows
          .map(({ scenario, alt, values }) => `<tr>
            <td><strong>${esc(scenario.id)}</strong> ${esc(scenario.name)}</td>
            <td><strong>${esc(alt.id)}</strong> ${esc(alt.name)}</td>
            ${metricIds.map((id) => `<td>${fmt(values[id])}</td>`).join("")}
          </tr>`)
          .join("")}
        ${marginalAlts
          .map((alt) => `<tr>
            <td><span class="tag">边际区间</span> 全部场景</td>
            <td><strong>${esc(alt.id)}</strong> ${esc(alt.name)}</td>
            ${metricIds.map((id) => `<td>[${fmt(alt.intervals?.[id]?.[0])}, ${fmt(alt.intervals?.[id]?.[1])}]</td>`).join("")}
          </tr>`)
          .join("")}
      </tbody>
    </table>`;
}

/* ------------------------------ Measurements ----------------------------- */

function renderCustomForm() {
  $("#customAlt").innerHTML = state.dataset.alternatives.map((a) => `<option value="${esc(a.id)}">${esc(a.id)} · ${esc(a.name)}</option>`).join("");
  $("#customMetric").innerHTML = state.dataset.metrics.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
  updateCustomDefault();
  $("#customAlt").onchange = updateCustomDefault;
  $("#customMetric").onchange = updateCustomDefault;
}
function updateCustomDefault() {
  const aid = $("#customAlt").value || state.selectedAltId;
  const mid = $("#customMetric").value || state.dataset.metrics[0].id;
  const a = analysisAlt(aid);
  const b = a?.bounds?.metrics?.[mid];
  let value = "";
  if (b) value = fmt((b.min + b.max) / 2, 2);
  else {
    const alt = altById(aid);
    const r = alt?.intervals?.[mid];
    if (r) value = fmt((r[0] + r[1]) / 2, 2);
  }
  $("#customValue").value = value;
}

function renderRecommendations() {
  const host = $("#recommendationList");
  const rec = state.recommendations;
  if (!rec?.recommendations?.length) {
    host.innerHTML = `<p class="hint">当前数据没有测量目录。可使用右侧自定义合成测量。</p>`;
    return;
  }
  const maxGain = Math.max(1, ...rec.recommendations.map((r) => r.expected_reduction));
  const maxCost = Math.max(1, ...rec.recommendations.map((r) => r.cost));
  host.innerHTML = rec.recommendations
    .map((r) => {
      const gainWidth = (r.expected_reduction / maxGain) * 100;
      const costWidth = (r.cost / maxCost) * 100;
      const zero = r.expected_reduction === 0 && r.guaranteed_reduction === 0 && r.upside_reduction === 0;
      return `
        <article class="rec-card">
          <div class="rec-head">
            <div>
              <div class="rec-title">${esc(r.name)} ${r.pareto_efficient ? `<span class="stat-pill pareto">费用-收益 Pareto 前沿</span>` : ""}</div>
              <div class="rec-desc">${esc(r.description || "")}</div>
            </div>
            <div class="rec-cost">${fmt(r.cost, 1)}<small> 万元</small></div>
          </div>
          <div class="rec-metrics">
            <div>
              <div class="bar-label"><span>期望排除歧义</span><strong>${fmt(r.expected_reduction, 2)}</strong></div>
              <div class="bar"><i style="width:${gainWidth}%"></i></div>
            </div>
            <div>
              <div class="bar-label"><span>费用相对值</span><strong>${fmt(r.cost, 1)}</strong></div>
              <div class="bar cost"><i style="width:${costWidth}%"></i></div>
            </div>
          </div>
          <div class="rec-stats">
            <span class="stat-pill">保底排除 ${r.guaranteed_reduction}</span>
            <span class="stat-pill">最好分支 ${r.upside_reduction}</span>
            <span class="stat-pill">${r.roi === null ? "—" : `${fmt(r.roi, 2)} 歧义/万元`}</span>
            ${r.guaranteed_removed_winner_ids?.length ? `<span class="stat-pill">保底排除最优候选 ${r.guaranteed_removed_winner_ids.join(",")}</span>` : ""}
            ${zero ? `<span class="stat-pill" style="background:var(--critical-soft);color:var(--critical-text)">决策歧义不变：反例测量</span>` : ""}
          </div>
          <div class="outcome-buttons">
            ${r.outcomes
              .map(
                (o) => `<button class="btn small secondary outcome-btn" type="button" data-measurement="${esc(r.measurement_id)}" data-outcome="${esc(o.outcome_id)}">
                  ${esc(o.label)}<span class="prob">${pct(o.probability)}</span>
                </button>`
              )
              .join("")}
          </div>
        </article>`;
    })
    .join("");
}

function renderApplied() {
  const totalCost = state.appliedRecords.reduce((sum, r) => sum + (Number(r.cost) || 0), 0);
  const list = state.appliedRecords.length
    ? state.appliedRecords
        .map(
          (r, i) => `<div class="applied-chip"><span><strong>${i + 1}.</strong> ${esc(r.title)}${r.outcome_label ? `：${esc(r.outcome_label)}` : ""} · ${fmt(r.cost, 1)}万元</span></div>`
        )
        .join("")
    : `<p class="hint">还没有补入测量。</p>`;
  $("#appliedList").innerHTML = state.appliedRecords.length
    ? `<div class="stat-pill" style="justify-self:start">累计测量费用 ${fmt(totalCost, 1)} 万元</div>${list}`
    : list;
  $("#undoMeasure").disabled = state.pastDatasets.length === 0;
  if (state.latestDiff) $("#latestDiff").innerHTML = esc(state.latestDiff.explanation);
  else $("#latestDiff").textContent = "尚未补入测量。";
}

async function applyOutcome(measurementId, outcomeId) {
  try {
    const result = await api("/api/apply-measurement", {
      dataset: state.dataset,
      options: currentOptions(),
      measurement_id: measurementId,
      outcome_id: outcomeId,
    });
    adoptMeasurementResult(result);
  } catch (error) {
    showToast(`补测失败：${error.message}`, true);
  }
}

async function submitCustomMeasurement(event) {
  event.preventDefault();
  const custom = {
    alternative_id: $("#customAlt").value,
    metric_id: $("#customMetric").value,
    value: Number($("#customValue").value),
    tolerance: Number($("#customTol").value),
    cost: Number($("#customCost").value),
  };
  try {
    const result = await api("/api/apply-measurement", {
      dataset: state.dataset,
      options: currentOptions(),
      custom,
    });
    adoptMeasurementResult(result);
  } catch (error) {
    showToast(`合成测量失败：${error.message}`, true);
  }
}

function adoptMeasurementResult(result) {
  state.pastDatasets.push(state.dataset);
  state.dataset = result.dataset;
  state.appliedRecords.push(result.applied_record);
  state.latestDiff = result.diff;
  state.analysis = result.after;
  state.selectedPairKey = null;
  renderDatasetHeader();
  renderAnalysis();
  renderApplied();
  renderCustomForm();
  refreshRecommendations();
  showToast("已补入合成测量并重算候选集合");
}

async function undoMeasurement() {
  if (!state.pastDatasets.length) return;
  state.dataset = state.pastDatasets.pop();
  state.appliedRecords.pop();
  state.latestDiff = null;
  state.selectedPairKey = null;
  renderDatasetHeader();
  renderCustomForm();
  renderApplied();
  await refreshAnalysisAndRecommendations();
}

/* ------------------------------ Enumeration ------------------------------ */

async function runEnumeration() {
  const host = $("#enumContent");
  host.innerHTML = `<p class="hint">正在枚举单纯形权重网格与全部联合场景…</p>`;
  try {
    const report = await api("/api/enumeration-check", { dataset: state.dataset, options: currentOptions(), grid_n: 180 });
    state.enumReport = report;
    host.innerHTML = `
      <div class="enum-stats">
        <div class="enum-stat"><strong>${report.weight_points_in_preference_range}</strong><span>偏好范围内权重格点</span></div>
        <div class="enum-stat"><strong>${report.score_evaluations.toLocaleString()}</strong><span>得分枚举次数</span></div>
        <div class="enum-stat"><strong>${report.brute_force_winner_ids.join("、") || "∅"}</strong><span>暴力网格观察到的最优集</span></div>
        <div class="enum-stat"><strong>${report.exact_winner_ids.join("、") || "∅"}</strong><span>见证求解返回的候选集</span></div>
        <div class="enum-stat"><strong>${report.sound ? "通过" : "不一致"}</strong><span>${report.sound ? "网格见证均属于精确集合" : "网格发现精确集合外赢家"}</span></div>
        <div class="enum-stat"><strong>${report.grid_covers_exact_set ? "完全覆盖" : "网格未触达窄楔"}</strong><span>${report.grid_covers_exact_set ? "精确候选均被网格核对" : "不影响存在性结论，需加密网格"}</span></div>
      </div>
      <p>${esc(report.statement)}</p>
      <div class="code-line">pair checks=${report.pair_count}, comparison cells=${report.blocking_or_strict_cells}; grid=${report.grid_n}; missing_from_exact=${JSON.stringify(report.grid_winners_missing_from_exact)}; exact_not_hit=${JSON.stringify(report.exact_winners_not_hit_by_grid)}</div>`;
  } catch (error) {
    host.innerHTML = `<div class="diff-box" style="background:var(--critical-soft);color:var(--critical-text)">枚举核对失败：${esc(error.message)}</div>`;
  }
}

/* --------------------------------- Events -------------------------------- */

async function handleImport(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const imported = JSON.parse(text);
    if (!imported.metrics || !imported.alternatives) throw new Error("JSON 必须包含 metrics 和 alternatives");
    resetWorkspace(imported);
    showToast(`已导入 ${file.name}`);
  } catch (error) {
    showToast(`导入失败：${error.message}`, true);
  } finally {
    event.target.value = "";
  }
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refreshAnalysisAndRecommendations, 220);
}

async function refreshAnalysisAndRecommendations() {
  try {
    state.analysis = await api("/api/analyze", { dataset: state.dataset, options: currentOptions() });
    renderDatasetHeader();
    renderAnalysis();
    updateWeightFeasibility();
    await refreshRecommendations();
  } catch (error) {
    showToast(`分析失败：${error.message}`, true);
  }
}

async function refreshRecommendations() {
  try {
    state.recommendations = await api("/api/recommend", { dataset: state.dataset, options: currentOptions() });
    renderRecommendations();
  } catch (error) {
    $("#recommendationList").innerHTML = `<p class="hint">测量建议计算失败：${esc(error.message)}</p>`;
  }
}

function handleControlInput(event) {
  const target = event.target;
  if (target.matches("[data-weight-lo],[data-weight-hi]")) {
    const id = target.dataset.weightLo || target.dataset.weightHi;
    const index = target.matches("[data-weight-lo]") ? 0 : 1;
    state.weightRanges[id][index] = Number(target.value);
    const row = target.closest(".weight-row");
    const lo = Number($(`[data-weight-lo="${id}"]`).value);
    const hi = Number($(`[data-weight-hi="${id}"]`).value);
    const valueEl = $(`[data-weight-value="${id}"]`);
    if (valueEl) valueEl.textContent = `${pct(lo)}–${pct(hi)}`;
    const fill = row?.querySelector(".weight-band span");
    if (fill && lo <= hi) {
      fill.style.left = `${Math.max(0, lo) * 100}%`;
      fill.style.width = `${Math.max(0, hi - lo) * 100}%`;
    }
    updateWeightFeasibility();
    scheduleRefresh();
  }
  if (target.matches("[data-th-value]")) {
    const i = Number(target.dataset.thValue);
    state.thresholds[i].value = Number(target.value);
    scheduleRefresh();
  }
}

function handleControlChange(event) {
  const target = event.target;
  if (target.matches("[data-weight-lo],[data-weight-hi]")) {
    for (const id of Object.keys(state.weightRanges)) {
      let [lo, hi] = state.weightRanges[id];
      lo = Math.max(0, Math.min(1, lo));
      hi = Math.max(0, Math.min(1, hi));
      if (lo > hi) [lo, hi] = [hi, lo];
      state.weightRanges[id] = [lo, hi];
    }
    renderWeightControls();
    refreshAnalysisAndRecommendations();
  }
  if (target.matches("[data-th-lock]")) {
    const i = Number(target.dataset.thLock);
    state.thresholds[i].locked = target.checked;
    renderThresholdControls();
    scheduleRefresh();
  }
  if (target.matches("[data-th-op]")) {
    const i = Number(target.dataset.thOp);
    state.thresholds[i].op = target.value;
    scheduleRefresh();
  }
}

function handleDelegatedClick(event) {
  const weightPreset = event.target.closest("[data-weight-preset]");
  if (weightPreset) {
    const key = weightPreset.dataset.weightPreset;
    state.weightRanges = clone(state.dataset.weight_presets[key]);
    renderWeightControls();
    refreshAnalysisAndRecommendations();
    return;
  }
  const thresholdPreset = event.target.closest("[data-threshold-preset]");
  if (thresholdPreset) {
    applyThresholdPreset(thresholdPreset.dataset.thresholdPreset);
    return;
  }
  const select = event.target.closest("[data-select-alt]");
  if (select) {
    state.selectedAltId = select.dataset.selectAlt;
    state.selectedPairKey = null;
    renderCandidates();
    renderDetail();
    if (window.innerWidth < 1180) $("#detailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const pairSelect = event.target.closest("[data-pair-select]");
  if (pairSelect) return; // change event handles below
  const measurementButton = event.target.closest("[data-measurement][data-outcome]");
  if (measurementButton) {
    applyOutcome(measurementButton.dataset.measurement, measurementButton.dataset.outcome);
  }
}

document.addEventListener("change", (event) => {
  const select = event.target.closest("[data-pair-select]");
  if (select) {
    state.selectedPairKey = select.value;
    renderDetail();
  }
});

function applyThresholdPreset(key) {
  const preset = key === "none" ? [] : state.dataset.threshold_presets?.[key] || [];
  const defaults = state.dataset.thresholds || state.dataset.metrics.map((m) => ({
    metric_id: m.id,
    op: m.direction === "max" ? ">=" : "<=",
    value: m.direction === "max" ? 80 : 6,
    locked: false,
  }));
  state.thresholds = state.dataset.metrics.map((m) => {
    const base = clone(defaults.find((t) => t.metric_id === m.id) || {});
    const forced = preset.find((t) => t.metric_id === m.id);
    return {
      metric_id: m.id,
      op: forced?.op || base.op || (m.direction === "max" ? ">=" : "<="),
      value: forced?.value ?? base.value,
      locked: Boolean(forced?.locked),
    };
  });
  renderThresholdControls();
  refreshAnalysisAndRecommendations();
}

init();
