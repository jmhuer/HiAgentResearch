import { walkToNearestInScope } from "./lineage_walk.js";
import { mergeContributionEdges } from "./merge_contributions.js";

const SQL_HTTPVFS_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/+esm";
const ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.esm.min.js";
const ALL_GROUPS = "__all__";
const SERIES_COLORS = ["#ff5c00", "#2d7dd2", "#34a853", "#d39200", "#a64ac9", "#00a3a3"];
const THRESHOLD_LINE_COLOR = "#74716a";
const MERGE_CONTRIB_SERIES = "__merge_contributions__";

let dashboardData = null;
let selectedRunId = null;
// The active dashboard tab = a fan-out area (its leaves + collapse) or the final-merge
// tab. null/empty topology.tabs ⇒ flat config ⇒ no tabs, the whole page renders unscoped
// (today's behavior). Set lazily to the first tab on first render.
let activeArea = null;
let chartInstance = null;
let echartsModule = null;
let resizeListenerAttached = false;
let chartResizeObserver = null;
// Off by default — the dashed fold-in arrows are a focused "where did each merge's contributions
// come from" overlay, not part of the clean default view.
let showMergeContributions = false;

async function main() {
  const manifest = await fetchJson("./manifest.json");
  const summary = await fetchJson("./summary.json");
  dashboardData = mergePublishedSnapshot(await loadDashboardData(manifest, summary), summary);
  dashboardData.repository = manifest.repository || dashboardData.repository || {};
  dashboardData.summary = dashboardData.summary?.length ? dashboardData.summary : summary.groups;
  dashboardData.metric_names = chartMetricNames(dashboardData, summary);
  renderShell(manifest, summary);
  renderTabs();
  renderLineageChains();
  renderMergeGroups();
  renderFilters(dashboardData);
  renderRuns(dashboardData.runs || []);
  await renderChart();
}

// --- Fan-out area tabs (§5) ---------------------------------------------------
// The tab bar is a pure projection of topology.tabs (backend-driven; no task-kind
// strings here). Selecting a tab scopes the whole page — lineage, merge panel, chart,
// and runs — to that area's groups. A flat config emits no tabs, so the bar stays
// hidden and every view renders unscoped, exactly as before.

const OVERVIEW_AREA = "__overview__";

// The Overview tab (always first) is the big-picture map: research groups / areas as nodes
// with their inheritance and the final-merge placeholder — approaches abstracted away. The
// per-area tabs (from the backend) follow. A flat config still gets Overview + one tab per group.
function dashboardTabs() {
  const tabs = (dashboardData.lineage_topology || {}).tabs || [];
  if (!tabs.length) return [];
  const overview = {
    area: OVERVIEW_AREA,
    overview: true,
    objective: "Every research group and how it inherits and merges — the big picture. Open a group for its approaches.",
  };
  return [overview, ...tabs];
}

function activeTab() {
  const tabs = dashboardTabs();
  if (!tabs.length) return null;
  return tabs.find((tab) => tab.area === activeArea) || tabs[0];
}

// The final merge group (role-tagged by the desugar). It has no tab of its own; the Overview
// is its home.
function finalMergeGroupId() {
  const groups = (dashboardData.lineage_topology || {}).groups || {};
  for (const [groupId, meta] of Object.entries(groups)) {
    if (meta && meta.role === "final_merge") return groupId;
  }
  return null;
}

// The "area result" groups — each area's collapse, or its single leaf for a degenerate area,
// plus the final merge. These are the big-picture trajectories the Overview chart/runs scope to.
function areaResultGroupIds() {
  const ids = new Set();
  for (const tab of (dashboardData.lineage_topology || {}).tabs || []) {
    if (tab.collapse) ids.add(String(tab.collapse));
    else (tab.leaves || []).forEach((leaf) => ids.add(String(leaf)));
  }
  const finalMerge = finalMergeGroupId();
  if (finalMerge) ids.add(String(finalMerge));
  return ids;
}

// The set of group ids in scope for the active tab, or null when there is no active
// tab (flat config) — null means "everything is in scope".
function scopedGroupIds() {
  const tab = activeTab();
  if (!tab) return null;
  if (tab.overview) return areaResultGroupIds(); // big-picture: area results + final merge
  // A per-area tab scopes to its leaves + collapse (the final merge has no tab — it lives on
  // the Overview).
  const ids = new Set();
  (tab.leaves || []).forEach((id) => ids.add(String(id)));
  if (tab.collapse) ids.add(String(tab.collapse));
  return ids;
}

function inScope(groupId) {
  const ids = scopedGroupIds();
  return !ids || ids.has(String(groupId));
}

// The chart (and its group dropdown + run count) additionally shows a per-area tab's ANCESTOR
// area-results, so a trajectory is drawn from L0 — where it came from — matching the lineage
// panel. The dropdown can still narrow to a single group. Overview/flat use normal scope.
function chartScopedGroupIds() {
  const base = scopedGroupIds();
  const tab = activeTab();
  if (!tab || tab.overview || !base) return base;
  const ids = new Set(base);
  const areas = (dashboardData.lineage_topology || {}).area_lineage?.areas || {};
  for (const ancestor of areas[tab.area]?.ancestors || []) {
    const resultGroup = areas[ancestor]?.result_group;
    if (resultGroup) ids.add(String(resultGroup));
  }
  return ids;
}

function inChartScope(groupId) {
  const ids = chartScopedGroupIds();
  return !ids || ids.has(String(groupId));
}

// On the Overview, an area's trajectory ends at the commit its lineage carries FORWARD — its top
// (winning) commit. A MERGE collapse keeps integrating past that peak, and those trailing loops
// scored lower and feed nothing downstream (nothing inherits or merges them) — from the winning
// lineage's view they are dropped losers, like a SELECT's discarded leaves. So on the big-picture
// view we cut each trajectory at its carried-forward step to keep the "branches collapse into one
// path" story honest. SELECT collapses already end on their winner (no-op); a per-area tab keeps
// every integration loop (this fires only on the Overview). The cutoff is anchor-metric based —
// one carried commit, applied uniformly across every displayed metric.
function overviewTrajectoryCutoff(groupId) {
  if (!activeTab()?.overview) return Infinity;
  const step = ((dashboardData.lineage_topology || {}).group_trajectory_winners || {})[groupId]
    ?.trajectory_step;
  return Number.isFinite(step) ? Number(step) : Infinity;
}

// --- Lineage-DAG walk: the single rule for connecting trajectories ----------------------
// Connect each group's trajectory to its nearest IN-SCOPE ancestor. The backend emits the whole
// ancestry (lineage_parents); the frontend, which owns scope, picks the first hop in view — the
// direct parent on a per-area tab, the prior area-result on the Overview. This replaced the old
// inheritance-connector / area-spine / select-result / collapse-base point taxonomy.

function lineageParentsFor(groupId) {
  return ((dashboardData.lineage_topology || {}).lineage_parents || {})[groupId] || { primary: [], secondary: [] };
}

// The metric value of a group's real node at a given trajectory step (or commit). Used to
// resolve an ancestor hop's y-value at render time.
function nodeValueAt(groupId, metricName, step, commitSha) {
  let shaMatch = null;
  for (const m of dashboardData.metrics || []) {
    if (m.group_id !== groupId || m.metric_name !== metricName) continue;
    if (step != null && m.trajectory_x != null && Number(m.trajectory_x) === Number(step)) {
      return Number(m.metric_value);
    }
    if (commitSha && m.commit_sha && String(m.commit_sha) === String(commitSha)) {
      shaMatch = Number(m.metric_value);
    }
  }
  return shaMatch;
}

// Resolve a group's connecting origin in the active scope by walking its lineage parent chain.
// The pure walk lives in lineage_walk.js (unit-tested); here we just inject the view predicates.
function resolveOriginForGroupInScope(groupId, metricName) {
  return walkToNearestInScope(lineageParentsFor(groupId).primary, {
    inScope: (gid) => inChartScope(gid),
    valueAt: (gid, step, sha) => nodeValueAt(gid, metricName, step, sha),
    baselineValue: () => baselineMetricValue(metricName),
  });
}

// One origin row per in-scope group, prepended so each polyline connects back to its nearest
// in-scope ancestor. Deduped when the group already owns a real node at that step.
function lineageWalkOrigins(values, metricName, selectedGroupId) {
  const byGroup = new Map();
  for (const point of values) {
    if (!byGroup.has(point.group_id)) byGroup.set(point.group_id, []);
    byGroup.get(point.group_id).push(point);
  }
  const origins = [];
  for (const [groupId, rows] of byGroup) {
    if (selectedGroupId !== ALL_GROUPS && groupId !== selectedGroupId) continue;
    const origin = resolveOriginForGroupInScope(groupId, metricName);
    if (!origin) continue;
    if (rows.some((row) => Number(row.trajectory_x) === origin.trajectory_x)) continue; // already there
    // The connector lands on the ancestor's real node — carry that node's commit so a click on the
    // connector resolves to the run that produced it (see resolveRunIdForPoint).
    const ancestorRow = (dashboardData.metrics || []).find(
      (m) =>
        m.group_id === origin.source_group_id &&
        m.metric_name === metricName &&
        Number(m.trajectory_x) === Number(origin.trajectory_x),
    );
    origins.push({
      run_id: `walkorigin:${groupId}`,
      group_id: groupId,
      metric_name: metricName,
      metric_value: origin.metric_value,
      trajectory_x: origin.trajectory_x,
      loop_index: 0,
      is_walk_origin: true,
      connector_source_group_id: origin.source_group_id || "",
      ...walkOriginDisplayMetadata(origin, ancestorRow),
    });
  }
  return origins;
}

function walkOriginDisplayMetadata(origin, ancestorRow) {
  if (origin.is_baseline) {
    const baseline = (dashboardData.lineage_topology || {}).baseline_snapshot || {};
    const ref = baseline.ref || "main";
    return {
      commit_sha: baseline.commit_sha || "",
      goal: `Frozen eval anchor (${ref})`,
      outcome: "baseline",
      is_baseline_anchor: true,
    };
  }
  return {
    commit_sha: ancestorRow?.commit_sha || "",
    goal: ancestorRow?.goal || "",
    reason: ancestorRow?.reason || "",
    outcome: ancestorRow?.research_outcome || ancestorRow?.outcome || "",
    is_baseline_anchor: Boolean(ancestorRow?.is_baseline_anchor),
  };
}

// Merge-contribution arrows: a dashed edge from each merge's fold-in SOURCE into the merge's base
// node, drawn only when the toggle is on. The fold-in sources live in lineage_parents[merge].secondary
// (the non-base participants, already resolved to rendered area results by the backend). We reuse the
// SAME nearest-in-scope walk as the primary connectors — and only draw an edge when the source itself
// is in view (origin resolves to the source, not a fallback ancestor), so an area-internal fold-in
// (leaf → its collapse) shows on that area's tab, while a cross-area fold-in (a terminal area →
// final_merge) shows on the Overview. The arrow points INTO the merge's base (its earliest own node,
// excluding the walk origin and any path-of-leaf trace). Returns null when nothing is drawable.
function mergeContributionSeries(grouped, metricName) {
  const deps = {
    inScope: (gid) => inChartScope(gid),
    valueAt: (gid, step, sha) => nodeValueAt(gid, metricName, step, sha),
    baselineValue: () => baselineMetricValue(metricName),
  };
  // A SELECT collapse's secondary entries are the COMPETING (discarded) leaves, not folded-in
  // contributions — it adopts one approach and drops the rest. Only real MERGE collapses (and the
  // auto final_merge) actually integrate their sources, so only they get contribution arrows.
  const selectIds = new Set(
    ((dashboardData.lineage_topology || {}).merge_groups || [])
      .filter((mg) => mg.is_select)
      .map((mg) => mg.group_id),
  );
  const runsIdx = dashboardIndexes().runs;
  const edges = mergeContributionEdges({
    grouped,
    selectIds,
    cycles: dashboardData.cycles || [],
    runsIdx,
    lineageParentsFor,
    walkToNearestInScope,
    deps,
  });
  if (!edges.length) return null;
  return {
    name: MERGE_CONTRIB_SERIES,
    type: "line",
    data: [],
    showSymbol: false,
    silent: true,
    animation: false,
    legendHoverLink: false,
    tooltip: { show: false },
    z: 1,
    markLine: {
      silent: true,
      symbol: ["none", "arrow"],
      symbolSize: 9,
      lineStyle: { type: "dashed", color: "rgba(154, 164, 178, 0.55)", width: 1.5 },
      label: { show: false },
      emphasis: { disabled: true },
      data: edges,
    },
  };
}

// "model_architecture" → "Model Architecture", "final_merge" → "Final Merge". Keeps tab
// labels consistently title-cased (the underlying area ids stay snake_case in the data).
function humanizeAreaLabel(area) {
  return String(area)
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function renderTabs() {
  renderRunMode();
  const bar = document.getElementById("tab-bar");
  if (!bar) return;
  const tabs = dashboardTabs();
  if (!tabs.length) {
    bar.hidden = true;
    bar.innerHTML = "";
    renderAreaObjective(null);
    return;
  }
  if (!activeArea || !tabs.some((tab) => tab.area === activeArea)) {
    activeArea = tabs[0].area;
  }
  bar.hidden = false;
  bar.innerHTML = tabs
    .map((tab) => {
      const active = tab.area === activeArea ? " active" : "";
      return `<button class="tab-button${active}" data-area="${escapeAttribute(tab.area)}">${escapeHtml(humanizeAreaLabel(tab.area))}</button>`;
    })
    .join("");
  bar.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.area === activeArea) return;
      activeArea = button.dataset.area;
      selectedRunId = null; // the prior selection may be out of the new scope
      renderTabs();
      renderLineageChains();
      renderMergeGroups();
      renderFilters(dashboardData);
      renderRuns(dashboardData.runs || []);
      renderChart();
    });
  });
  renderAreaObjective(activeTab());
}

// The active tab's research goal, in plain text — shown for both fan-out and flat runs so
// every group explains what it's aiming at.
function renderAreaObjective(tab) {
  const el = document.getElementById("area-objective");
  if (!el) return;
  const objective = String(tab?.objective || "").trim();
  if (!objective) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = `<span class="area-objective-label">${escapeHtml(humanizeAreaLabel(tab.area))}</span> ${escapeHtml(objective)}`;
}

// A small badge stating whether this run branches (areas fan out into competing
// approaches) or is linear (one lineage per research group). One template serves both.
function renderRunMode() {
  const el = document.getElementById("run-mode");
  if (!el) return;
  const meta = (dashboardData.lineage_topology || {}).groups || {};
  const branching = Object.values(meta).some((m) => m && m.role === "collapse");
  el.hidden = false;
  el.textContent = branching ? "Branching" : "Linear";
  el.classList.toggle("run-mode-branching", branching);
}

function shouldPreferJsonSnapshot(manifest) {
  return String(manifest.source || "").startsWith("github_artifacts");
}

async function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

function mergePublishedSnapshot(data, summary = {}) {
  const summaryTopology = summary.lineage_topology || {};
  const dataTopology = data.lineage_topology || {};
  const baselineSnapshot = dataTopology.baseline_snapshot || summaryTopology.baseline_snapshot || null;
  return {
    ...data,
    lineage_topology: {
      ...summaryTopology,
      ...dataTopology,
      ...(baselineSnapshot ? { baseline_snapshot: baselineSnapshot } : {}),
      groups: { ...(summaryTopology.groups || {}), ...(dataTopology.groups || {}) },
      chains: dataTopology.chains?.length ? dataTopology.chains : summaryTopology.chains || [],
      execution_waves: dataTopology.execution_waves?.length
        ? dataTopology.execution_waves
        : summaryTopology.execution_waves || [],
    },
    metric_targets:
      data.metric_targets?.length
        ? data.metric_targets
        : summary.metric_targets || summary.metric_expectations || [],
  };
}

async function loadDashboardData(manifest, summary = {}) {
  if (shouldPreferJsonSnapshot(manifest)) {
    try {
      return mergePublishedSnapshot(await fetchJson("./dashboard.json"), summary);
    } catch (error) {
      console.warn("JSON snapshot unavailable, trying SQLite", error);
    }
  }
  try {
    return mergePublishedSnapshot(
      await withTimeout(loadFromSqlite(manifest), 20000, "SQLite dashboard load"),
      summary,
    );
  } catch (error) {
    console.warn("SQLite adapter unavailable, using JSON fallback", error);
    return mergePublishedSnapshot(await fetchJson("./dashboard.json"), summary);
  }
}

async function loadFromSqlite(manifest) {
  const sqliteModule = await import(SQL_HTTPVFS_URL);
  const createDbWorker =
    sqliteModule.createDbWorker ||
    sqliteModule.default?.createDbWorker ||
    (typeof sqliteModule.default === "function" ? sqliteModule.default : null);
  if (!createDbWorker) {
    throw new Error("sql.js-httpvfs createDbWorker export was not found");
  }
  const worker = await createDbWorker(
    [
      {
        from: "inline",
        config: {
          serverMode: "full",
          requestChunkSize: manifest.sqlite?.request_chunk_size || 4096,
          url: pageUrl(manifest.sqlite?.url || manifest.database || "dashboard.db"),
          cacheBust: manifest.cache_bust || "",
        },
      },
    ],
    pageUrl(manifest.sqlite?.worker_url || "sqlite.worker.js"),
    pageUrl(manifest.sqlite?.wasm_url || "sql-wasm.wasm"),
    50 * 1024 * 1024,
  );
  const [summary, runs, metrics, outcomes, cycles, artifacts, metricNames, expectations] = await Promise.all([
    query(worker, "SELECT * FROM latest_group_summary ORDER BY group_id"),
    query(worker, "SELECT * FROM runs ORDER BY group_id, created_at DESC"),
    query(worker, "SELECT * FROM metric_series ORDER BY group_id, created_at, metric_name"),
    query(worker, "SELECT * FROM research_outcomes ORDER BY created_at"),
    query(worker, "SELECT * FROM cycles ORDER BY group_id, loop_index, created_at"),
    query(worker, "SELECT * FROM artifacts ORDER BY run_id, artifact_path"),
    query(worker, "SELECT DISTINCT metric_name FROM metrics ORDER BY metric_name"),
    query(worker, "SELECT * FROM metric_expectations ORDER BY group_id, metric_name"),
  ]);
  return {
    summary,
    runs,
    metrics,
    metric_names: metricNames.map((row) => row.metric_name),
    research_outcomes: outcomes.map((row) => ({
      ...coerceBooleans(row),
      research_outcome: normalizeResearchOutcomeName(row.research_outcome),
    })),
    cycles: cycles.map(parseCycle),
    artifacts,
    metric_targets: expectations.map(normalizeTarget),
  };
}

async function query(worker, sql, params = []) {
  const result = await worker.db.exec(sql, params);
  if (!result.length) return [];
  const { columns, values } = result[0];
  return values.map((row) => Object.fromEntries(row.map((value, index) => [columns[index], value])));
}

async function fetchJson(path) {
  // Always read fresh: the dashboard bundle is rebuilt in place (same filenames),
  // so a cached summary.json/dashboard.json would silently show stale/partial data
  // after a rebuild. `no-store` bypasses the browser cache so a plain reload always
  // reflects the latest build.
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to fetch ${path}: ${response.status}`);
  return response.json();
}

function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  const totalMin = Math.round(ms / 60000);
  if (totalMin < 1) return `${Math.round(ms / 1000)}s`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}

function formatTimestamp(ms) {
  if (!Number.isFinite(ms)) return "—";
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderRunStatus() {
  const el = document.getElementById("run-status");
  if (!el) return;
  // Static-page liveness: a build taken mid-run has no completed_at (loop still
  // going) -> live; once loops-all finishes it is stamped -> complete.
  const session = dashboardData.orchestration_session || {};
  if (!session.started_at) {
    el.hidden = true;
    return;
  }
  const live = !session.completed_at;
  el.hidden = false;
  el.className = `run-status ${live ? "run-status-live" : "run-status-done"}`;
  el.textContent = live ? "Live" : "Complete";
}

function renderHeroMeta(manifest) {
  renderRunStatus();
  const meta = document.getElementById("hero-meta");
  if (!meta) return;
  const baseline = (dashboardData.lineage_topology || {}).baseline_snapshot || {};
  const runs = dashboardData.runs || [];

  // Wall-clock span of the session: baseline start → end. While the run is live the
  // end is the most recent run (so the duration grows on every rebuild); once it has
  // finished, the recorded completed_at is the true end and the value freezes. The same
  // end timestamp is the dashboard's "Last update" — the baseline commit itself now lives
  // in the Lineage panel as the shared root node, so it no longer needs a hero row.
  const session = dashboardData.orchestration_session || {};
  const stamps = runs
    .map((r) => Date.parse(r.created_at))
    .filter((n) => Number.isFinite(n));
  const start = Date.parse(baseline.created_at);
  const startMs = Number.isFinite(start) ? start : stamps.length ? Math.min(...stamps) : NaN;
  const completed = Date.parse(session.completed_at);
  const endMs = Number.isFinite(completed)
    ? completed
    : stamps.length
    ? Math.max(...stamps)
    : NaN;

  const rows = [
    ["Last update", escapeHtml(formatTimestamp(endMs))],
    ["Duration", escapeHtml(formatDuration(endMs - startMs))],
  ];
  meta.innerHTML = rows
    .map(([label, value]) => `<div class="hero-meta-row"><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`)
    .join("");
}

function renderShell(manifest, summary) {
  document.title = manifest.title || summary.title || "HiAgentResearch Dashboard";
  text("dashboard-title", manifest.title || summary.title || "HiAgentResearch");
  text("dashboard-tagline", "Hierarchical, metric-guided agent exploration.");
  renderHeroMeta(manifest);
  const repository = dashboardData.repository || manifest.repository || {};
  const repoLink = document.getElementById("repo-link");
  const repoLabel = document.getElementById("repo-label");
  if (repoLink && repoLabel) {
    const slug = String(repository.repository || "").trim();
    const webUrl = String(repository.web_url || "").trim();
    if (slug && webUrl) {
      repoLink.href = webUrl;
      repoLink.hidden = false;
      repoLabel.textContent = slug;
    } else {
      repoLink.hidden = true;
    }
  }
}

function groupLineageLabel(groupId) {
  const meta = (dashboardData.lineage_topology || {}).groups?.[groupId];
  if (!meta) return "";
  if (meta.mode === "inherit" && meta.inherit_from) {
    return `Inherits ${meta.inherit_from}`;
  }
  return "Baseline · L0 frozen-eval anchor";
}

// A lineage node's label: the group id is the primary label so it matches the merge-source
// labels (e.g. "optimization__a1") and the lineage→merge feed reads at a glance — its top
// commit sha lines up with the "@ sha" the merge folds in. The goal is on hover.
function lineageNodeLabel(groupId) {
  const meta = (dashboardData.lineage_topology || {}).groups?.[groupId] || {};
  return { text: groupId, title: String(meta.seed_approach || "").trim() };
}

function renderLineageChains() {
  const container = document.getElementById("lineage-chains");
  if (!container) {
    return;
  }
  // In a fan-out area tab, every leaf is its own short lineage (origin → goal):
  // baseline leaves root at L0, inherit-area leaves root at the upstream area's ★ commit
  // (making inheritance visible). Only the flat (no-tab) view uses the global baseline-rooted
  // chains below.
  const tab = activeTab();
  if (tab) {
    if (tab.overview) container.innerHTML = renderOverviewLineage();
    else container.innerHTML = renderAreaLeafLineages(tab);
    return;
  }
  const topology = dashboardData.lineage_topology || {};
  const chains = (topology.chains || []);
  const winners = topology.lineage_winners || {};
  if (!chains.length) {
    container.innerHTML = "";
    return;
  }
  // A group has "started" once it has produced a run of its own (the baseline
  // bootstrap doesn't count). Everything configured but not yet started is dimmed,
  // so the panel doubles as a progress map of the whole intended lineage.
  const started = new Set((dashboardData.runs || []).map((run) => String(run.group_id)).filter(Boolean));
  container.innerHTML = chains
    .map((chain) => {
      const lineageId = String(chain[0]);
      const winner = winners[lineageId] || {};
      const leaf = String(winner.leaf_group_id || "");
      // When no loop has beaten the frozen baseline, the chain's top commit IS the
      // baseline — so the ★ belongs on the baseline node, not on any group node.
      const baselineWins = !!winner.is_baseline_anchor;
      const groupNodes = chain
        .map((groupId) => {
          const id = String(groupId);
          const isPending = !started.has(id);
          const isTop = !baselineWins && id === leaf;
          // Not-started wins over "top": a group that hasn't run its own cycles
          // shouldn't look achieved, even when its chain's current top is the baseline.
          const cls = isPending ? " lineage-node-pending" : isTop ? " lineage-node-top" : "";
          // A fan-out leaf shows its goal (its real identity); the opaque group id
          // moves to the hover title. Non-leaves keep the group id as the label.
          const { text, title: nodeTitle } = lineageNodeLabel(id);
          const titleParts = [nodeTitle, isPending ? "not started yet" : ""].filter(Boolean);
          const title = titleParts.length ? ` title="${escapeAttribute(titleParts.join(" · "))}"` : "";
          return `<span class="lineage-node${cls}"${title}>${escapeHtml(text)}</span>`;
        });
      // Every lineage branches from the same frozen L0 baseline commit; show it as the
      // shared root node so the ★ has a home when the baseline is still best.
      const path = [baselineNodeHtml(baselineWins), ...groupNodes]
        .filter(Boolean)
        .join('<span class="lineage-arrow">→</span>');
      const sha = winner.winner_commit_sha ? String(winner.winner_commit_sha).slice(0, 7) : "—";
      const where = baselineWins
        ? "baseline L0"
        : `${escapeHtml(leaf || "?")} · L${winner.trajectory_step ?? "?"}`;
      return `
        <article class="lineage-chain">
          <div class="lineage-path">${path}</div>
          <div class="lineage-top"><span class="lineage-star">★</span> top commit <strong>${escapeHtml(sha)}</strong> &middot; ${where}</div>
        </article>
      `;
    })
    .join("");
}

// A node for an area's *result* (its collapse, or its single leaf) labeled by the humanized
// area name + that result's ★ commit. Used in the Overview map and as an ancestor node in the
// always-from-L0 per-area lineage.
function areaResultNodeHtml(areaId, isTop) {
  const topology = dashboardData.lineage_topology || {};
  const resultGroup = String(topology.area_lineage?.areas?.[areaId]?.result_group || "");
  const winner = (topology.group_trajectory_winners || {})[resultGroup] || {};
  const started = new Set((dashboardData.runs || []).map((run) => String(run.group_id)).filter(Boolean));
  // Resolved if it has a run OR a real (non-baseline) top commit. A select collapse never runs —
  // it adopts the strongest leaf's commit — so "no run" must not grey out an already-resolved area.
  const resolved = started.has(resultGroup) || (Boolean(winner.commit_sha) && !winner.is_baseline_anchor);
  const isPending = !resolved;
  const sha = winner.commit_sha ? shortSha(String(winner.commit_sha)) : "";
  // Label by the result GROUP id (e.g. "optimization__collapse @ opcol1"), so a lineage
  // node reads identically to the same node in the Merge panel — obvious they're the same.
  const label = sha ? `${resultGroup} @ ${sha}` : resultGroup;
  const cls = `lineage-node${isPending ? " lineage-node-pending" : isTop ? " lineage-node-top" : ""}`;
  return `<span class="${cls}" title="${escapeAttribute(`${humanizeAreaLabel(areaId)} area result`)}">${escapeHtml(label)}</span>`;
}

// A trajectory always starts at L0: baseline → ancestor *area* results (approaches abstracted)
// → this group. Works for a leaf (terminal = its goal id) and for a merge/collapse base
// (terminal = its area-result label). One uniform "where did this come from" row.
function renderLeafLineageRow(groupId) {
  const topology = dashboardData.lineage_topology || {};
  const meta = topology.groups?.[groupId] || {};
  const groupWinners = topology.group_trajectory_winners || {};
  const started = new Set((dashboardData.runs || []).map((run) => String(run.group_id)).filter(Boolean));
  const id = String(groupId);
  const isPending = !started.has(id);
  const winner = groupWinners[id] || {};
  const isTop = !isPending && Boolean(winner.commit_sha);

  // Ancestor area results, root → parent, back to L0 (area-level; no approaches).
  const ancestorAreas = topology.area_lineage?.areas?.[String(meta.area || "")]?.ancestors || [];
  const ancestorNodes = ancestorAreas.map((area) => areaResultNodeHtml(area, false));

  const { text, title: nodeTitle } = lineageNodeLabel(id);
  const cls = isPending ? " lineage-node-pending" : isTop ? " lineage-node-top" : "";
  const titleParts = [nodeTitle, isPending ? "not started yet" : ""].filter(Boolean);
  const titleAttr = titleParts.length ? ` title="${escapeAttribute(titleParts.join(" · "))}"` : "";
  const terminal = `<span class="lineage-node${cls}"${titleAttr}>${escapeHtml(text)}</span>`;
  const path = [baselineNodeHtml(false), ...ancestorNodes, terminal]
    .filter(Boolean)
    .join('<span class="lineage-arrow">→</span>');

  const sha = winner.commit_sha ? shortSha(String(winner.commit_sha)) : "—";
  const where = isPending ? "not started" : `L${winner.trajectory_step ?? "?"}`;
  return `
    <article class="lineage-chain">
      <div class="lineage-path">${path}</div>
      <div class="lineage-top"><span class="lineage-star">★</span> top commit <strong>${escapeHtml(sha)}</strong> &middot; ${escapeHtml(where)}</div>
    </article>
  `;
}

function renderAreaLeafLineages(tab) {
  const rows = (tab.leaves || []).map((leafId) => renderLeafLineageRow(leafId)).join("");
  return rows || `<p class="lineage-empty">No approaches configured for this area yet.</p>`;
}

// The Overview map: one row per maximal area chain, baseline → area result → area result …,
// at the research-group/area altitude (approaches hidden). For a linear config the areas are
// the groups, so this is the familiar full-chain view.
function renderOverviewLineage() {
  const topology = dashboardData.lineage_topology || {};
  const chains = topology.area_lineage?.chains || [];
  if (!chains.length) {
    return `<p class="lineage-empty">No research groups configured.</p>`;
  }
  return chains
    .map((chain) => {
      const nodes = chain.map((area, i) => areaResultNodeHtml(area, i === chain.length - 1));
      const path = [baselineNodeHtml(false), ...nodes]
        .filter(Boolean)
        .join('<span class="lineage-arrow">→</span>');
      const tipArea = chain[chain.length - 1];
      const tipResult = String(topology.area_lineage?.areas?.[tipArea]?.result_group || "");
      const winner = (topology.group_trajectory_winners || {})[tipResult] || {};
      const sha = winner.commit_sha ? shortSha(String(winner.commit_sha)) : "—";
      return `
        <article class="lineage-chain">
          <div class="lineage-path">${path}</div>
          <div class="lineage-top"><span class="lineage-star">★</span> top commit <strong>${escapeHtml(sha)}</strong> &middot; ${escapeHtml(tipResult)}</div>
        </article>
      `;
    })
    .join("");
}

// The final-merge tab does only merges (shown in the Merge panel), but it's useful to see
// where the strongest *starting* commit came from — its lineage, all the way to L0.
// The frozen L0 baseline rendered as a lineage node (linked to its commit when the host
// templates are available). Returns "" when there is no baseline snapshot yet.
function baselineNodeHtml(isTop) {
  const baseline = (dashboardData.lineage_topology || {}).baseline_snapshot || {};
  const ref = String(baseline.ref || "");
  const fullSha = String(baseline.commit_sha || "");
  if (!ref && !fullSha) {
    return "";
  }
  const label = ref ? (fullSha ? `${ref} @ ${shortSha(fullSha)}` : ref) : shortSha(fullSha);
  const cls = `lineage-node lineage-node-baseline${isTop ? " lineage-node-top" : ""}`;
  const href = fullSha
    ? fillTemplate((dashboardData.repository || {}).commit_url_template, { commit_sha: fullSha })
    : "";
  const inner = href
    ? `<a class="lineage-node-link" href="${escapeAttribute(href)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`
    : escapeHtml(label);
  return `<span class="${cls}" title="frozen baseline (L0)">${inner}</span>`;
}

// A merge converges every lineage into one branch through a SEQUENCE of integration steps:
// it starts from the strongest lineage (base) and folds in the rest best→worst, one per
// loop cycle. So N lineages show one base node + N-1 merge step nodes (e.g. 3 lineages →
// 2 merge nodes). Steps light up as the merge's cycles complete; until then they're greyed
// (planned). We know the lineages it will combine from config, so a configured-but-not-yet
// -run merge is shown up front. No merge configured (e.g. commented out) ⇒ nothing renders.
function renderMergeGroups() {
  const container = document.getElementById("lineage-merge");
  if (!container) {
    return;
  }
  const topology = dashboardData.lineage_topology || {};
  // Which merge to show: Overview shows only the FINAL merge (area collapses are area-internal,
  // shown on their own tabs); an area tab shows its collapse; the final-merge tab its merge.
  // Unscoped (no tabs) shows every merge.
  const tab = activeTab();
  let mergeFilter = () => true;
  if (tab && tab.overview) {
    const finalMerge = finalMergeGroupId();
    mergeFilter = (merge) => Boolean(finalMerge) && String(merge.group_id) === String(finalMerge);
  } else if (tab) {
    mergeFilter = (merge) => String(merge.group_id) === String(tab.collapse);
  }
  const mergeGroups = (topology.merge_groups || []).filter(mergeFilter);
  if (!mergeGroups.length) {
    const _det0 = container.closest("details");
    if (_det0) _det0.querySelectorAll("summary .merge-subtype").forEach((n) => n.remove());
    // A single-result area (e.g. the engineering foundation) has no competing approaches to
    // select or merge. Keep the section present but greyed so the two-section layout stays
    // stable when switching tabs, rather than collapsing to an empty box.
    container.innerHTML = `<p class="lineage-empty">Single result — no competing approaches to select or merge.</p>`;
    return;
  }
  const runs = dashboardData.runs || [];
  const groupWinners = topology.group_trajectory_winners || {};
  const commitTemplate = (dashboardData.repository || {}).commit_url_template;

  // A resolved node (real winner commit), linked to its commit.
  const commitNode = (label, sha, { top, base }) => {
    const cls = `lineage-node${base ? " lineage-node-merge-base" : ""}${top ? " lineage-node-top" : ""}`;
    const text = sha ? `${label} @ ${shortSha(sha)}` : label;
    const href = sha ? fillTemplate(commitTemplate, { commit_sha: sha }) : "";
    const inner = href
      ? `<a class="lineage-node-link" href="${escapeAttribute(href)}" target="_blank" rel="noreferrer">${escapeHtml(text)}</a>`
      : escapeHtml(text);
    return `<span class="${cls}">${inner}</span>`;
  };
  // A greyed placeholder for something not yet known (base or a pending source).
  const pendingNode = (label, title) =>
    `<span class="lineage-node lineage-node-pending" title="${escapeAttribute(title)}">${escapeHtml(label)}</span>`;
  // A leaf that competed in a SELECT collapse but was not adopted — dimmed, no merge arrow.
  const competedNode = (label, sha) => {
    const text = sha ? `${label} @ ${shortSha(sha)}` : label;
    return `<span class="lineage-node lineage-node-pending" title="competed for this area but was not adopted">${escapeHtml(text)}</span>`;
  };

  const articles = mergeGroups
    .map((merge) => {
      const id = String(merge.group_id);
      const winner = groupWinners[id] || {};
      const doneCount = runs.filter((r) => String(r.group_id) === id).length;
      const noOps = merge.no_ops || [];
      const noOpCaption = noOps.length
        ? ` &middot; ${noOps.length} no-op ${noOps.length === 1 ? "source" : "sources"}`
        : "";

      // Ordered participants, base first (only meaningful once every source lineage has a
      // real run — the base and the integration order aren't known before then).
      const parts = (merge.participants || []).map((p) => ({
        name: String(p.group_id || ""),
        sha: String(p.commit_sha || ""),
        known: !!p.known,
      }));
      // How many lineages this merge considers is known from config even before any run. Once
      // resolved, no-op sources still count as considered, but they do not become merge steps.
      const sourceCount = Math.max(parts.length + noOps.length, (merge.planned_sources || []).length);
      if (!sourceCount) {
        return "";
      }
      const resolved = parts.length > 0 && parts.every((p) => p.known);
      const stepCount = resolved ? Math.max(0, parts.length - 1) : Math.max(0, sourceCount - 1);

      if (merge.is_select) {
        // Select collapse: ADOPT the single strongest leaf; the others competed and were
        // dropped. Render the selected commit (★) plus the competitors dimmed — never a
        // "base → + fold-in" chain, which would imply an integration that never happens.
        const selected = parts[0];
        const competed = parts.slice(1);
        let selectPath;
        let selectCaption;
        if (resolved) {
          const sha = doneCount > 0 && winner.commit_sha ? winner.commit_sha : selected.sha;
          const selNode = commitNode(selected.name, selected.sha, { base: true, top: doneCount > 0 });
          // The selected leaf carries the ★; the rest are shown dimmed. No "competed" label —
          // "strongest of N" in the caption already says it, and the dimming reads as not-adopted.
          const competedNodes = competed.map((c) => competedNode(c.name, c.sha));
          selectPath = [selNode, ...competedNodes].join(" ");
          selectCaption = `<span class="lineage-star">★</span> selected <strong>${escapeHtml(selected.name)}</strong> @ ${escapeHtml(shortSha(sha))} &middot; strongest of ${sourceCount}${noOpCaption}`;
        } else {
          selectPath = pendingNode("select · TBD", "the strongest of the area's leaves is adopted after the leaf runs finish");
          selectCaption = `adopts the strongest of ${sourceCount} after the leaf runs finish${noOpCaption}`;
        }
        return `
        <article class="lineage-chain lineage-merge-chain">
          <div class="lineage-path">${selectPath}</div>
          <div class="lineage-top">${selectCaption}</div>
        </article>
      `;
      }

      let path;
      let caption;
      const stepWord = stepCount === 1 ? "step" : "steps";
      if (resolved) {
        const base = parts[0];
        const steps = parts.slice(1);
        const baseNode = commitNode(base.name, base.sha, { base: true });
        const stepNodes = steps.map((s, i) => {
          const lit = doneCount > i; // integration step i+1 has a completed merge cycle
          const top = lit && i === Math.min(doneCount, steps.length) - 1; // latest completed step holds the ★
          return commitNode(`+ ${s.name}`, s.sha, { top });
        });
        path = [baseNode, ...stepNodes].join('<span class="lineage-arrow">→</span>');
        // Same caption shape as a lineage row: "★ top commit <sha> · <detail>". The active
        // tab already names which merge this is, so we don't repeat the group id here.
        caption =
          steps.length === 0
            ? `no distinct merge steps${noOpCaption}`
            : doneCount > 0 && winner.commit_sha
            ? `<span class="lineage-star">★</span> top commit <strong>${escapeHtml(shortSha(winner.commit_sha))}</strong> &middot; ${Math.min(doneCount, steps.length)}/${steps.length} merges`
            : `${steps.length} merge ${stepWord} planned${noOpCaption}`;
      } else {
        // Unknown: we know HOW MANY lineages will merge, but not which is the base, which
        // are the sources, or the order — so show generic positional placeholders.
        const baseNode = pendingNode("base · TBD", "strongest lineage is resolved after the source runs finish");
        const stepNodes = Array.from({ length: stepCount }, (_, i) =>
          pendingNode(`merge ${i + 1}`, "source and order resolved after the lineage runs finish"),
        );
        path = [baseNode, ...stepNodes].join('<span class="lineage-arrow">→</span>');
        caption = `base + ${stepCount} merge ${stepWord}, resolved after the lineage runs finish${noOpCaption}`;
      }

      return `
        <article class="lineage-chain lineage-merge-chain">
          <div class="lineage-path">${path}</div>
          <div class="lineage-top">${caption}</div>
        </article>
      `;
    })
    .join("");

  // When every shown collapse is a select (an area tab for a combine:false area), the section
  // is about adoption, not integration — so the heading shouldn't say "merge … fold in the rest".
  const allSelect = mergeGroups.length > 0 && mergeGroups.every((m) => m.is_select);
  const headingTitle = allSelect ? "Select" : "Merge";
  const headingBlurb = allSelect
    ? "Adopt the single strongest competing leaf; the rest competed and were dropped (★)."
    : "Cross-lineage merges: start from the strongest lineage and fold in the rest (★).";
  // The "plot merge contributions" toggle belongs to merges, so it lives HERE — only when a real
  // merge is shown (never on a SELECT-only area, which folds nothing in). Plotting it overlays
  // dashed arrows on the chart from each fold-in source into the merge's base.
  const contributionToggle = allSelect
    ? ""
    : `<label class="toggle merge-contrib-toggle" title="Overlay dashed arrows from each merge's fold-in sources into the merge, on the trajectory chart">
        <input type="checkbox" id="merge-contributions-toggle"${showMergeContributions ? " checked" : ""} />
        Plot merge contributions
      </label>`;
  container.innerHTML = `
    <div class="section-title inline">
      <div>
        <h2>${escapeHtml(headingTitle)}</h2>
        <p>${escapeHtml(headingBlurb)}</p>
      </div>
      ${contributionToggle}
    </div>
    <div class="lineage-chains">${articles}</div>
  `;
  // PROTO: surface merge subtype(s) next to the "Merge" title in the collapsed <summary>,
  // so the kind (Select / Iterative) is visible before expanding.
  const _det = container.closest("details");
  const _h2 = _det ? _det.querySelector("summary h2") : null;
  if (_h2) {
    _h2.querySelectorAll(".merge-subtype").forEach((n) => n.remove());
    const _hint = _h2.querySelector(".collapsible-hint");
    [...new Set(mergeGroups.map((m) => (m.is_select ? "Select" : "Iterative")))].forEach((s) => {
      const _tag = document.createElement("span");
      _tag.className = "merge-subtype merge-subtype--" + s.toLowerCase();
      _tag.textContent = s;
      _h2.insertBefore(_tag, _hint);
    });
  }
  const toggle = document.getElementById("merge-contributions-toggle");
  if (toggle) {
    toggle.onchange = (event) => {
      showMergeContributions = event.target.checked;
      renderChart();
    };
  }
}

function runCountLabel(groupId) {
  const runs = visibleChartRuns(dashboardData.runs || [], groupId, selectedMetricName());
  if (groupId === ALL_GROUPS) {
    const groupCount = new Set(runs.map((run) => run.group_id).filter(Boolean)).size;
    return `${runs.length} runs · ${groupCount} groups`;
  }
  return `${runs.length} runs · ${groupId}`;
}

function updateChartRunSummary(groupId) {
  const summary = document.getElementById("chart-run-summary");
  if (!summary) {
    return;
  }
  summary.textContent = runCountLabel(groupId);
}

function chartLegendRows(seriesCount) {
  const width = window.innerWidth || 1200;
  if (width <= 820) {
    return 1;
  }
  const itemsPerRow = width <= 1100 ? 3 : 4;
  return Math.max(1, Math.ceil(seriesCount / itemsPerRow));
}

function legendBandHeight(seriesCount) {
  return 16 + chartLegendRows(seriesCount) * 34;
}

function renderFilters(data) {
  // Scope the group dropdown to the chart's scope (this area + its ancestors), so you can
  // filter the from-L0 trajectory down to any single group on it.
  const groups = configuredGroupIds(data).filter((id) => inChartScope(id));
  const metrics = chartMetricNames(data);
  setOptions("group-filter", groups, { allLabel: "All groups" });
  setOptions("metric-filter", metrics);
  // Assign (not addEventListener) so re-rendering on tab switch never double-binds.
  document.getElementById("group-filter").onchange = renderChartAndRuns;
  document.getElementById("metric-filter").onchange = renderChartAndRuns;
}

function renderRuns(runs) {
  const container = document.getElementById("run-list");
  const sorted = sortRunsForDisplay(visibleChartRuns(runs, selectedGroupId(), selectedMetricName()));
  if (!sorted.length) {
    container.textContent = "No runs found.";
    selectedRunId = null;
    renderRunDetail();
    return;
  }
  const visibleIds = new Set(sorted.map((run) => run.run_id));
  selectedRunId = selectedRunId && visibleIds.has(selectedRunId) ? selectedRunId : sorted[0].run_id;
  container.innerHTML = sorted
    .map(
      (run) => `
        <button class="run-button ${run.run_id === selectedRunId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}">
          <strong>${escapeHtml(run.group_id)}</strong>
          <div>${escapeHtml(loopLabel(run))} · ${escapeHtml(run.run_id)} · ${escapeHtml(run.failure_class)}</div>
          <div class="run-meta">${escapeHtml(formatRunMeta(run))}</div>
          <small>${escapeHtml(run.created_at || "")}</small>
        </button>
      `,
    )
    .join("");
  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectRun(button.dataset.runId, { scroll: false });
    });
  });
  renderRunDetail();
}

function selectedGroupId() {
  return document.getElementById("group-filter")?.value || ALL_GROUPS;
}

function selectedMetricName() {
  const selected = document.getElementById("metric-filter")?.value;
  return selected || chartMetricNames(dashboardData)[0] || "";
}

function renderChartAndRuns() {
  renderRuns(dashboardData.runs || []);
  renderChart();
}

function visibleChartRuns(runs, groupId, metricName) {
  const byId = new Map(runs.map((run) => [run.run_id, run]));
  const runIds = [];
  const seen = new Set();
  for (const point of chartPointsForSelection(groupId, metricName)) {
    const runId = resolveRunIdForPoint(point);
    if (!runId || seen.has(runId) || !byId.has(runId)) continue;
    seen.add(runId);
    runIds.push(runId);
  }
  return runIds.map((runId) => byId.get(runId));
}

function chartPointsForSelection(groupId, metricName) {
  const indexes = dashboardIndexes();
  let values = (dashboardData.metrics || [])
    .filter(
      (metric) =>
        (groupId === ALL_GROUPS || metric.group_id === groupId) &&
        inChartScope(metric.group_id) &&
        metric.metric_name === metricName &&
        !(metric.path_of_leaf && inChartScope(metric.path_of_leaf)) &&
        Number(metric.trajectory_x) <= overviewTrajectoryCutoff(metric.group_id),
    )
    .map((metric) => enrichMetricPoint(metric, indexes))
    .filter((point) => Number.isFinite(point.metric_value));
  if (values.length) {
    values = [...values, ...lineageWalkOrigins(values, metricName, groupId)];
  }
  return values;
}

function showChartMessage(container, message) {
  if (chartInstance && !chartInstance.isDisposed?.()) {
    chartInstance.dispose();
    chartInstance = null;
  }
  container.innerHTML = `<div class="chart-message">${escapeHtml(message)}</div>`;
}

function ensureChartCanvas(container) {
  container.querySelector(".chart-message")?.remove();
  let canvas = document.getElementById("metric-chart-canvas");
  if (!canvas) {
    canvas = document.createElement("div");
    canvas.id = "metric-chart-canvas";
    canvas.className = "metric-chart-canvas";
    container.appendChild(canvas);
  }
  return canvas;
}

async function renderChart() {
  const container = document.getElementById("metric-chart");
  try {
    const groupId = selectedGroupId();
    const metricName = selectedMetricName();
    updateChartRunSummary(groupId);
    // The backend already emits render-ready rows: deduped, with baseline/origin
    // anchors, trajectory_x, and symbol. The frontend only filters and joins run
    // metadata for tooltips — it does not recompute chart geometry.
    // The backend emits only real nodes (loop runs, baseline, collapse merge-base / select-adopted)
    // plus collapse path-to-winner nodes (path_of_leaf). Drop those path nodes on the
    // leaf's own tab — there the leaf line already draws the climb, so re-drawing it would double.
    const values = chartPointsForSelection(groupId, metricName);
    if (!values.length) {
      showChartMessage(container, "No metric data for this selection.");
      updateChartRunSummary(groupId);
      return;
    }
    await renderEChart(container, values, metricName, groupId);
  } catch (error) {
    console.error("Dashboard chart render failed", error);
    showChartMessage(container, `Chart failed to render: ${error.message}`);
    const groupId = document.getElementById("group-filter")?.value || ALL_GROUPS;
    updateChartRunSummary(groupId);
  }
}

function renderRunDetail() {
  const indexes = dashboardIndexes();
  const run = indexes.runs.get(selectedRunId);
  const outcome = indexes.outcomes.get(selectedRunId);
  const cycle = indexes.cycles.get(selectedRunId);
  const artifacts = (dashboardData.artifacts || []).filter((item) => item.run_id === selectedRunId);
  const metrics = (dashboardData.metrics || []).filter((item) => item.run_id === selectedRunId);
  const fallbackCycle = {
    goal: `Direct eval fallback for ${run?.group_id || "unknown"} (${run?.run_id || "unknown"}): cycle manifest metadata was not uploaded.`,
    planned_code_changes: ["No cycle_manifest.json found for this run; showing eval-only provenance."],
  };
  const effectiveCycle = cycle || fallbackCycle;
  const container = document.getElementById("run-detail");
  const links = runLinks(run);
  if (!run) {
    container.textContent = "Select a run.";
    return;
  }
  // Present the run in its task's frame (generic — labels come from the backend, no
  // task-kind strings hardcoded here): engineering shows "Change goal" + a metric-
  // preservation note; metric cycles show "Hypothesis".
  const groupMeta = (dashboardData.lineage_topology || {}).groups?.[run.group_id] || {};
  const intentLabel = groupMeta.intent_label || "Hypothesis";
  const metricsLabel = groupMeta.preserve_metrics ? "Metrics (must be preserved)" : "Metrics";
  const anchorSha = cycle?.lineage_anchor_sha ? String(cycle.lineage_anchor_sha).slice(0, 7) : "";
  const drawFrom = Array.isArray(groupMeta.draw_from) ? groupMeta.draw_from : [];
  // For a merge run, show the RESOLVED fold-in contributors (the non-base participants + their
  // commits) — analogous to the anchor tag. Falls back to the planned draw_from before resolution.
  const mergeEntry = ((dashboardData.lineage_topology || {}).merge_groups || []).find(
    (m) => String(m.group_id) === String(run.group_id),
  );
  const contributors = mergeEntry
    ? (mergeEntry.participants || [])
        .slice(1)
        .filter((p) => p && p.group_id)
        .map((p) => `${p.source_group_id || p.group_id}${p.commit_sha ? " @" + String(p.commit_sha).slice(0, 7) : ""}`)
    : [];
  const noOps = mergeEntry
    ? (mergeEntry.no_ops || [])
        .filter((p) => p && (p.source_group_id || p.group_id))
        .map((p) => `${p.source_group_id || p.group_id}${p.reason ? " (" + p.reason + ")" : ""}`)
    : [];
  const mergeTag = contributors.length
    ? `<span class="tag">contributors: ${escapeHtml(contributors.join(", "))}</span>`
    : noOps.length
      ? `<span class="tag">no distinct fold-ins: ${escapeHtml(noOps.join(", "))}</span>`
      : drawFrom.length
        ? `<span class="tag">merges in: ${escapeHtml(drawFrom.join(", "))} (pending)</span>`
        : "";
  const tags = [
    `<span class="badge ${outcomeClass(outcome?.research_outcome)}">${escapeHtml(displayResearchOutcome(outcome?.research_outcome))}</span>`,
    `<span class="tag">failure: ${escapeHtml(run.failure_class || "none")}</span>`,
    `<span class="tag">${escapeHtml(groupLineageLabel(run.group_id) || "lineage")}</span>`,
    anchorSha ? `<span class="tag">anchor ${escapeHtml(anchorSha)}</span>` : "",
    mergeTag,
  ]
    .filter(Boolean)
    .join("");
  container.innerHTML = `
    <div class="detail-block">
      <strong>${escapeHtml(run.run_id)}</strong>
      ${escapeHtml(run.group_id)} · ${escapeHtml(run.branch)} · ${escapeHtml(run.created_at || "")}
      <div class="detail-tags">${tags}</div>
    </div>
    <div class="detail-block detail-links">
      <strong>Links</strong>
      ${links.length ? links.map((link) => `<a href="${escapeAttribute(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("") : "No repository links available."}
    </div>
    <div class="detail-block">
      <strong>Outcome</strong>
      ${escapeHtml(displayResearchOutcome(outcome?.research_outcome))}${outcome?.next_action ? ` · next: ${escapeHtml(outcome.next_action)}` : ""} — ${escapeHtml(outcome?.reason || "")}
    </div>
    <div class="detail-block">
      <strong>${escapeHtml(intentLabel)}</strong>
      ${escapeHtml(effectiveCycle.goal || "No cycle manifest recorded.")}
    </div>
    <div class="detail-block">
      <strong>Planned Changes</strong>
      ${(effectiveCycle.planned_code_changes || []).map((item) => `<div>${escapeHtml(item)}</div>`).join("") || "None recorded."}
    </div>
    <div class="detail-block">
      <strong>${escapeHtml(metricsLabel)}</strong>
      ${metrics.map((metric) => `<div>${escapeHtml(metric.metric_name)} = ${formatMetric(metric.metric_value)}</div>`).join("")}
    </div>
    <div class="detail-block">
      <strong>Artifacts</strong>
      ${artifacts.map((artifact) => `<div>${escapeHtml(artifact.artifact_path)} (${artifact.size_bytes} bytes)</div>`).join("") || "None recorded."}
    </div>
  `;
}

function computeChartLayout({ seriesCount, hasDataZoom }) {
  return {
    left: 12,
    right: 16,
    top: legendBandHeight(seriesCount) + 16,
    bottom: (hasDataZoom ? 56 : 40) + 28,
    containLabel: true,
  };
}

function thresholdMarkSeries(thresholdLines) {
  if (!thresholdLines.length) {
    return null;
  }
  return {
    type: "line",
    name: "Thresholds",
    data: [],
    showSymbol: false,
    silent: true,
    animation: false,
    tooltip: { show: false },
    legendHoverLink: false,
    itemStyle: { color: THRESHOLD_LINE_COLOR },
    lineStyle: { color: THRESHOLD_LINE_COLOR, type: "dashed", width: 2 },
    z: 20,
    zlevel: 1,
    markLine: {
      z: 20,
      symbol: "none",
      silent: true,
      lineStyle: { color: THRESHOLD_LINE_COLOR, type: "dashed", width: 1.5 },
      label: {
        show: true,
        color: THRESHOLD_LINE_COLOR,
        formatter: (params) => params.name,
        position: "insideEndTop",
        fontSize: 11,
        backgroundColor: "rgba(255,255,255,0.98)",
        borderColor: "rgba(154, 164, 178, 0.35)",
        borderWidth: 1,
        borderRadius: 4,
        padding: [2, 6],
      },
      data: thresholdLines.map((line) => ({ name: line.label, yAxis: line.value })),
    },
  };
}

function axisNameStyle() {
  return { color: "#74716a", fontSize: 12 };
}

function attachChartResizeObserver(container) {
  if (chartResizeObserver || !window.ResizeObserver) {
    return;
  }
  chartResizeObserver = new ResizeObserver(() => {
    if (chartInstance && !chartInstance.isDisposed?.()) {
      chartInstance.resize();
    }
  });
  chartResizeObserver.observe(container);
}

async function renderEChart(container, values, metricName, groupId) {
  const echarts = await loadECharts();
  updateChartRunSummary(groupId);
  const canvas = ensureChartCanvas(container);
  chartInstance = chartInstance && !chartInstance.isDisposed?.() ? chartInstance : echarts.init(canvas);
  chartInstance.off("click");
  attachChartResizeObserver(container);

  const positioned = assignTrajectoryPositions(values);
  const ordered = [...positioned].sort((left, right) => {
    const traj = Number(left.trajectory_x) - Number(right.trajectory_x);
    if (traj !== 0) return traj;
    return String(left.group_id).localeCompare(String(right.group_id));
  });
  const trajectoryAxis = trajectoryCategoryAxis(ordered, metricName);
  const categories = trajectoryAxis.labels;
  const grouped = groupBy(ordered, (point) => point.group_id);
  const thresholdLines = referenceThresholdLines(metricName, groupId);
  const domain = metricDomain(ordered, thresholdLines);
  const useValueAxis = trajectoryAxis.mode === "trajectory";
  const trajectoryMin = useValueAxis ? trajectoryAxis.indices[0] : null;
  const trajectoryMax = useValueAxis ? trajectoryAxis.indices[trajectoryAxis.indices.length - 1] : null;
  const groupSeries = Object.entries(grouped).map(([groupId, rows]) => {
    const seriesData = seriesDataForGroup(groupId, rows, trajectoryAxis);
    const visiblePoints = seriesData.filter((entry) => entry != null).length;
    const hasConnector = seriesData.some(
      (entry) => entry?.point?.is_walk_origin || entry?.point?.is_baseline_anchor,
    );
    return {
      name: groupId,
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 8,
      showAllSymbol: true,
      connectNulls: visiblePoints > 1 || hasConnector,
      emphasis: { focus: "series" },
      lineStyle: { width: visiblePoints > 1 || hasConnector ? 3 : 0, type: "solid" },
      z: 2,
      data: seriesData,
    };
  });
  const trajectorySeries = useValueAxis
    ? groupSeries.map((entry) => ({ ...entry, data: toTrajectorySeriesEntries(entry.data, trajectoryAxis) }))
    : groupSeries;
  const referenceSeries = thresholdMarkSeries(thresholdLines);
  // The fold-in arrows live on a value (trajectory) axis only — they reference numeric L-steps.
  // Excluded from the legend (and legendItemCount) so the overlay never clutters the legend band.
  const contributionSeries =
    showMergeContributions && useValueAxis ? mergeContributionSeries(grouped, metricName) : null;
  const series = [
    ...trajectorySeries,
    ...(referenceSeries ? [referenceSeries] : []),
    ...(contributionSeries ? [contributionSeries] : []),
  ];
  const legendItemCount = trajectorySeries.length + (thresholdLines.length ? 1 : 0);
  const hasDataZoom = (useValueAxis ? trajectoryAxis.indices.length : categories.length) > 12;
  const chartLayout = computeChartLayout({ seriesCount: legendItemCount, hasDataZoom });
  const axisName = {
    show: true,
    name: "Lineage step",
    nameLocation: "middle",
    nameGap: 32,
    nameTextStyle: axisNameStyle(),
  };
  const yAxisName = {
    name: metricName,
    nameLocation: "middle",
    nameGap: 44,
    nameRotate: 90,
    nameTextStyle: axisNameStyle(),
  };

  chartInstance.setOption(
    {
      backgroundColor: "transparent",
      color: SERIES_COLORS,
      animationDuration: 450,
      grid: chartLayout,
      legend: {
        data: [
          ...Object.keys(grouped).map((name) => ({ name, icon: "roundRect" })),
          ...(thresholdLines.length
            ? [
                {
                  name: "Thresholds",
                  itemStyle: { color: THRESHOLD_LINE_COLOR },
                  lineStyle: { color: THRESHOLD_LINE_COLOR, type: "dashed", width: 2 },
                },
              ]
            : []),
        ],
        type: legendItemCount > 4 ? "scroll" : "plain",
        top: 8,
        left: "center",
        width: "92%",
        align: "auto",
        itemGap: 14,
        itemWidth: 12,
        textStyle: { color: "#74716a", fontSize: 12 },
        pageIconColor: THRESHOLD_LINE_COLOR,
        pageIconInactiveColor: "rgba(154, 164, 178, 0.45)",
        pageTextStyle: { color: THRESHOLD_LINE_COLOR },
      },
      tooltip: {
        trigger: "item",
        appendToBody: true,
        confine: false,
        borderWidth: 1,
        borderColor: "rgba(0,0,0,0.30)",
        backgroundColor: "rgba(255,255,255,0.98)",
        textStyle: { color: "#15140f" },
        extraCssText:
          "max-width:min(340px,calc(100vw - 48px));white-space:normal;word-break:break-word;line-height:1.45;box-shadow:0 20px 60px rgba(0,0,0,.45);border-radius:14px;padding:12px;",
        formatter: (params) => pointTooltipHtml(params.data?.point),
      },
      xAxis: useValueAxis
        ? {
            type: "value",
            min: trajectoryMin,
            max: trajectoryMax === trajectoryMin ? trajectoryMax + 1 : trajectoryMax,
            minInterval: 1,
            splitLine: { show: false },
            axisLine: { onZero: true, lineStyle: { color: "rgba(0,0,0,0.45)" } },
            axisTick: { show: false },
            axisLabel: {
              color: "#74716a",
              formatter: (value) => `L${value}`,
              hideOverlap: true,
            },
            ...axisName,
          }
        : {
            type: "category",
            data: categories,
            boundaryGap: true,
            axisLine: { lineStyle: { color: "rgba(0,0,0,0.45)" } },
            axisTick: { show: false },
            axisLabel: {
              color: "#74716a",
              formatter: (value) => value,
              hideOverlap: true,
            },
            ...axisName,
          },
      yAxis: {
        type: "value",
        min: domain.min,
        max: domain.max,
        splitLine: { lineStyle: { color: "rgba(0,0,0,0.10)" } },
        axisLabel: { color: "#74716a", formatter: formatMetric },
        ...yAxisName,
      },
      media: [
        {
          query: { maxWidth: 820 },
          option: {
            legend: { type: "scroll", width: "96%", top: 6 },
            grid: { ...chartLayout, top: chartLayout.top + 10 },
          },
        },
      ],
      dataZoom: hasDataZoom
          ? [
              { type: "inside", throttle: 50 },
              {
                type: "slider",
                height: 24,
                bottom: 14,
                borderColor: "rgba(0,0,0,0.20)",
                fillerColor: "rgba(154, 164, 178, 0.2)",
                handleStyle: {
                  color: THRESHOLD_LINE_COLOR,
                  borderColor: THRESHOLD_LINE_COLOR,
                },
                moveHandleStyle: {
                  color: THRESHOLD_LINE_COLOR,
                  borderColor: THRESHOLD_LINE_COLOR,
                },
                emphasis: {
                  handleStyle: {
                    color: THRESHOLD_LINE_COLOR,
                    borderColor: THRESHOLD_LINE_COLOR,
                  },
                  moveHandleStyle: {
                    color: THRESHOLD_LINE_COLOR,
                    borderColor: THRESHOLD_LINE_COLOR,
                  },
                },
                textStyle: { color: "#74716a" },
              },
            ]
          : [],
      series,
    },
    true,
  );
  chartInstance.on("click", (params) => {
    const point = params.data?.point;
    if (!point) return;
    // A point maps to the run that PRODUCED its commit. Real loop-run nodes carry that run_id
    // directly; synthetic nodes (collapse base / path trace / walk-origin connectors) carry only a
    // commit, so resolve to the owning run by sha. The L0 baseline has no run — leave the selection
    // untouched rather than blanking the panel.
    const runId = resolveRunIdForPoint(point);
    if (!runId) return;
    selectRun(runId, { scroll: true });
    void renderChart();
  });
  if (!resizeListenerAttached) {
    window.addEventListener("resize", () => chartInstance?.resize());
    resizeListenerAttached = true;
  }
  chartInstance.resize();
  updateChartRunSummary(groupId);
}

function groupLineageMode(groupId) {
  return dashboardData.lineage_topology?.groups?.[groupId]?.mode || "baseline";
}

function toTrajectorySeriesEntries(series, trajectoryAxis) {
  return trajectoryAxis.indices.map((trajectoryX, index) => {
    const entry = series[index];
    if (!entry) {
      return null;
    }
    const metricValue = Array.isArray(entry.value) ? entry.value[1] : entry.value;
    return {
      ...entry,
      value: [Number(trajectoryX), metricValue],
    };
  });
}

function chartSeriesData(rows, trajectoryAxis) {
  if (trajectoryAxis.mode === "run_id") {
    return trajectoryAxis.points.map((point) => {
      if (!rows.some((row) => row.run_id === point.run_id)) return null;
      return chartPointDatum(point);
    });
  }
  return trajectoryAxis.indices.map((trajectoryX) => {
    const point = rows.find((row) => Number(row.trajectory_x) === trajectoryX);
    if (!point) return null;
    return chartPointDatum(point);
  });
}

function seriesDataForGroup(groupId, rows, trajectoryAxis) {
  // Real nodes (loop runs, the baseline L0 for baseline-mode roots, collapse base nodes) plus the
  // walk origin prepended in renderChart — all carry backend/walk-assigned trajectory_x, so we
  // just plot them. Inherit groups have no L0 anchor of their own; their origin comes from the walk.
  return chartSeriesData(rows, trajectoryAxis);
}

function configuredGroupIds(data) {
  const topology = data.lineage_topology || {};
  const fromWaves = (topology.execution_waves || []).flat();
  const fromChains = (topology.chains || []).flat();
  const fromRuns = (data.runs || []).map((run) => run.group_id);
  return unique([...fromWaves, ...fromChains, ...fromRuns].filter(Boolean)).sort();
}

function chartPointDatum(point) {
  const selected = point.run_id === selectedRunId;
  // ★ = this lineage/area's best commit for the viewed metric (per-group winner) — matches the
  // panels. ◆ = a commit a downstream group inherits from. Backend precomputes `symbol`; the
  // local fallback (older snapshots) mirrors that precedence.
  const isTopCommit = Boolean(point.is_group_policy_winner);
  const isInheritAnchor = Boolean(point.is_inherit_anchor);
  const symbol = point.symbol || (isTopCommit ? "star" : isInheritAnchor ? "diamond" : "circle");
  const baseSize = isTopCommit ? 12 : isInheritAnchor ? 10 : 8;
  return {
    value: point.metric_value,
    point,
    symbol,
    symbolSize: selected ? baseSize + 2 : baseSize,
    itemStyle: selected
      ? { borderColor: "#15140f", borderWidth: 2 }
      : isTopCommit
        ? { borderColor: "#f2cc60", borderWidth: 1.5 }
        : isInheritAnchor
          ? { borderColor: "#7eb6ff", borderWidth: 1.5 }
          : { borderWidth: 0 },
  };
}

function enrichMetricPoint(metric, indexes) {
  const run = indexes.runs.get(metric.run_id) || {};
  const outcome = indexes.outcomes.get(metric.run_id) || {};
  const cycle = indexes.cycles.get(metric.run_id) || {};
  const loopIndex =
    cycle.loop_index != null
      ? Number(cycle.loop_index)
      : metric.loop_index != null
        ? Number(metric.loop_index)
        : null;
  return {
    ...metric,
    branch: run.branch || metric.branch || "",
    commit_sha: run.commit_sha || metric.commit_sha || "",
    workflow_run_id: run.workflow_run_id || metric.workflow_run_id || "",
    loop_index: Number.isFinite(loopIndex) && loopIndex > 0 ? loopIndex : null,
    lineage_mode: cycle.lineage_mode || "",
    lineage_parent_group_id: cycle.lineage_parent_group_id || "",
    lineage_anchor_sha: cycle.lineage_anchor_sha || "",
    lineage_anchor_policy: cycle.lineage_anchor_policy || "",
    outcome: outcome.research_outcome || "unknown",
    reason: outcome.reason || "",
    // Synthetic nodes (collapse base / path-trace) carry a backend-set goal but have NO cycle, so
    // fall back to the point's own goal rather than clobbering it to empty ("No summary recorded").
    goal: cycle.goal || metric.goal || "",
    planned_code_changes: cycle.planned_code_changes || metric.planned_code_changes || [],
    metric_value: Number(metric.metric_value),
    trajectory_x:
      metric.trajectory_x != null && metric.trajectory_x !== "" ? Number(metric.trajectory_x) : undefined,
    is_baseline_anchor: Boolean(metric.is_baseline_anchor),
  };
}

function baselineMetricValue(metricName) {
  const metrics = dashboardData.lineage_topology?.baseline_snapshot?.metrics;
  if (!metrics || metrics[metricName] == null) {
    return null;
  }
  const value = Number(metrics[metricName]);
  return Number.isFinite(value) ? value : null;
}

function targetThresholdLines(groupId, metricName) {
  const rows = (dashboardData.metric_targets || dashboardData.metric_expectations || []).filter(
    (row) => (groupId === ALL_GROUPS || row.group_id === groupId) && row.metric_name === metricName,
  );
  const lines = [];
  for (const row of rows) {
    const min = row.min ?? row.min_value;
    const max = row.max ?? row.max_value;
    if (min !== null && min !== undefined && min !== "") {
      lines.push({ key: `targets_min:${min}`, value: Number(min), label: "targets_min" });
    }
    if (max !== null && max !== undefined && max !== "") {
      lines.push({ key: `targets_max:${max}`, value: Number(max), label: "targets_max" });
    }
  }
  return [...new Map(lines.map((line) => [line.key, line])).values()];
}

function referenceThresholdLines(metricName, groupId) {
  const lines = [];
  const baselineValue = baselineMetricValue(metricName);
  if (baselineValue != null) {
    lines.push({ key: `baseline:${baselineValue}`, value: baselineValue, label: "baseline" });
  }
  lines.push(...targetThresholdLines(groupId, metricName));
  return [...new Map(lines.map((line) => [line.key, line])).values()];
}

// A plot point maps to the run that produced its commit. Real loop-run nodes carry a run_id that
// exists in `runs`; synthetic nodes (collapse base / path trace / walk-origin connectors) carry a
// commit but a placeholder run_id, so we resolve them to the owning run by commit sha. Returns null
// when nothing backs the point (e.g. the frozen L0 baseline, which has no run) — the caller then
// leaves the current selection in place instead of clearing the detail panel.
function resolveRunIdForPoint(point) {
  if (!point) return null;
  const runs = dashboardIndexes().runs;
  if (point.run_id && runs.has(point.run_id)) return point.run_id;
  const sha = String(point.commit_sha || "");
  if (sha) {
    const owner = (dashboardData.runs || []).find((run) => {
      const runSha = String(run.commit_sha || "");
      return runSha && (runSha === sha || runSha.startsWith(sha) || sha.startsWith(runSha));
    });
    if (owner) return owner.run_id;
  }
  return null;
}

function selectRun(runId, { scroll }) {
  selectedRunId = runId;
  renderRuns(dashboardData.runs || []);
  renderRunDetail();
  if (scroll) {
    document.querySelector(`.run-button[data-run-id="${cssEscape(runId)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function pointTooltipHtml(point) {
  if (!point) return "";
  const inheritAnchorHint =
    point.is_inherit_anchor && (point.inherit_anchor_for_groups || []).length
      ? ` · anchor for ${point.inherit_anchor_for_groups.join(", ")}`
      : "";
  const winnerHint = point.is_group_policy_winner ? " · top commit (best for this metric)" : "";
  const connector = point.is_baseline_anchor
    ? " · frozen baseline anchor"
    : point.is_walk_origin
      ? point.connector_source_group_id
        ? ` · continues from ${point.connector_source_group_id}`
        : " · continues from baseline"
      : "";
  const lineage =
    point.lineage_mode === "inherit" && point.lineage_parent_group_id
      ? ` · inherit ${point.lineage_parent_group_id}@${shortSha(point.lineage_anchor_sha)}${winnerHint}${inheritAnchorHint}`
      : connector + winnerHint + inheritAnchorHint;
  return `
    <div class="tooltip-title">${escapeHtml(point.group_id)} · ${escapeHtml(point.metric_name)} ${formatMetric(point.metric_value)}</div>
    <div class="tooltip-muted">${escapeHtml(trajectoryLabel(point))}${lineage} · ${escapeHtml(shortRunId(point.run_id))} · ${escapeHtml(point.outcome)}</div>
    <div class="tooltip-body">${escapeHtml(shortText(point.goal || point.reason || "No summary recorded.", 190))}</div>
    ${(point.planned_code_changes || []).slice(0, 2).map((item) => `<div class="tooltip-muted">${escapeHtml(shortText(item, 130))}</div>`).join("")}
  `;
}

function runLinks(run) {
  if (!run) return [];
  const repository = dashboardData.repository || {};
  return [
    run.commit_sha ? { label: `Commit ${shortSha(run.commit_sha)}`, href: fillTemplate(repository.commit_url_template, { commit_sha: run.commit_sha }) } : null,
    run.branch ? { label: "Branch", href: fillTemplate(repository.branch_url_template, { branch: run.branch }) } : null,
    run.workflow_run_id ? { label: `Workflow ${run.workflow_run_id}`, href: fillTemplate(repository.workflow_run_url_template, { workflow_run_id: run.workflow_run_id }) } : null,
  ].filter((link) => link?.href);
}

function fillTemplate(template, values) {
  if (!template) return "";
  return Object.entries(values).reduce((result, [key, value]) => {
    const encoded = key === "branch" ? encodeURI(value) : encodeURIComponent(value);
    return result.replaceAll(`{${key}}`, encoded);
  }, template);
}

// Keep aligned with hiagentresearch.src.dashboard.trajectory.assign_trajectory_positions.
function assignTrajectoryPositions(points) {
  // trajectory_x is computed once, authoritatively, by the backend
  // (trajectory.assign_trajectory_positions). The frontend only normalizes the
  // type — it never re-derives the lineage axis.
  return points.map((point) => ({
    ...point,
    trajectory_x: point.trajectory_x != null && point.trajectory_x !== "" ? Number(point.trajectory_x) : 0,
  }));
}

function baselineMetricAvailable(metricName) {
  const metrics = dashboardData.lineage_topology?.baseline_snapshot?.metrics;
  if (!metrics || metricName == null) {
    return false;
  }
  const value = Number(metrics[metricName]);
  return Number.isFinite(value);
}

function trajectoryCategoryAxis(points, metricName) {
  let indices = uniqueInOrder(
    points
      .map((point) => point.trajectory_x)
      .filter((value) => value != null && value !== "" && Number.isFinite(Number(value)))
      .map((value) => Number(value))
      .sort((left, right) => left - right),
  );
  const hasBaseline =
    points.some((point) => point.is_baseline_anchor) || baselineMetricAvailable(metricName);
  if (hasBaseline && !indices.includes(0)) {
    indices = [0, ...indices];
  }
  if (indices.length) {
    return { mode: "trajectory", labels: indices.map((value) => `L${value}`), indices };
  }
  const sorted = sortRunsForDisplay(points);
  return {
    mode: "run_id",
    labels: sorted.map((point) => loopLabel(point)),
    indices: sorted.map((_, index) => index),
    points: sorted,
  };
}

function trajectoryLabel(point) {
  if (point.is_baseline_anchor) {
    return "L0 · baseline";
  }
  const trajectoryX = point.trajectory_x;
  const loopIndex = point.loop_index;
  if (trajectoryX != null && trajectoryX !== "") {
    const local = loopIndex ? ` · loop ${loopIndex}` : "";
    return `L${trajectoryX}${local}`;
  }
  return loopLabel(point);
}

function sortRunsForDisplay(runs) {
  const indexes = dashboardIndexes();
  return [...runs].sort((left, right) => {
    const group = String(left.group_id).localeCompare(String(right.group_id));
    if (group !== 0) return group;
    const loopLeft = Number((indexes.cycles.get(left.run_id) || {}).loop_index || 0);
    const loopRight = Number((indexes.cycles.get(right.run_id) || {}).loop_index || 0);
    if (loopLeft !== loopRight) return loopLeft - loopRight;
    return String(left.created_at).localeCompare(String(right.created_at));
  });
}

function loopLabel(runOrPoint) {
  const loopIndex = runOrPoint.loop_index ?? (dashboardIndexes().cycles.get(runOrPoint.run_id) || {}).loop_index;
  return loopIndex ? `L${loopIndex}` : shortRunId(runOrPoint.run_id);
}

function groupBy(values, keyFn) {
  return values.reduce((groups, value) => {
    const key = keyFn(value);
    groups[key] = groups[key] || [];
    groups[key].push(value);
    return groups;
  }, {});
}

function dashboardIndexes() {
  return {
    runs: byRunId(dashboardData.runs || []),
    outcomes: byRunId(dashboardData.research_outcomes || []),
    cycles: byRunId(dashboardData.cycles || []),
  };
}

function byRunId(rows) {
  return new Map(rows.map((row) => [row.run_id, row]));
}

async function loadECharts() {
  echartsModule = echartsModule || (await import(ECHARTS_URL));
  return echartsModule;
}

function metricDomain(points, thresholdLines) {
  const values = [
    ...points.map((point) => point.metric_value),
    ...thresholdLines.map((line) => line.value),
  ].filter((value) => Number.isFinite(Number(value)));
  if (!values.length) {
    return { min: 0, max: 1 };
  }
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = Math.max(maxValue - minValue, 0.000001);
  const padding = span * 0.1;
  return {
    min: Number((minValue - padding).toFixed(6)),
    max: Number((maxValue + padding).toFixed(6)),
  };
}

function shortRunId(runId) {
  return String(runId || "").replace(/^gh_/, "");
}

function shortSha(sha) {
  return String(sha || "").slice(0, 7);
}

function shortText(value, maxLength) {
  const textValue = String(value || "");
  return textValue.length > maxLength ? `${textValue.slice(0, maxLength - 1)}...` : textValue;
}

function formatRunMeta(run) {
  return [run.commit_sha ? `commit ${shortSha(run.commit_sha)}` : "", run.workflow_run_id ? `workflow ${run.workflow_run_id}` : ""]
    .filter(Boolean)
    .join(" · ");
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value).replaceAll('"', '\\"');
}

function normalizeTarget(row) {
  return {
    group_id: row.group_id,
    metric_name: row.metric_name,
    min: row.min ?? row.min_value ?? null,
    max: row.max ?? row.max_value ?? null,
    source: row.source || "unknown",
  };
}

function parseCycle(row) {
  return {
    ...row,
    target_files: parseJson(row.target_files_json, row.target_files || []),
    planned_code_changes: parseJson(row.planned_code_changes_json, row.planned_code_changes || []),
    merge_plan: parseJson(row.merge_plan_json, row.merge_plan || null),
    merge_cycle_provenance: parseJson(
      row.merge_cycle_provenance_json,
      row.merge_cycle_provenance || null,
    ),
  };
}

function coerceBooleans(row) {
  return { ...row };
}

function asBoolean(value) {
  return value === true || value === 1 || value === "1";
}

function setOptions(id, values, options = {}) {
  const select = document.getElementById(id);
  const allOption = options.allLabel ? `<option value="${ALL_GROUPS}">${escapeHtml(options.allLabel)}</option>` : "";
  select.innerHTML = `${allOption}${values.map((value) => `<option value="${escapeAttribute(value)}">${escapeHtml(value)}</option>`).join("")}`;
}

function chartMetricNames(data, summary = {}) {
  const configured = summary.metric_names?.length ? summary.metric_names : data.metric_names;
  const candidates = configured?.length ? configured : unique((data.metrics || []).map((metric) => metric.metric_name));
  // Which metrics are discrete (non-charted as smooth lines) is config-driven.
  const discrete = new Set(summary.discrete_metrics || data.discrete_metrics || []);
  return candidates.filter((metric) => !discrete.has(metric));
}

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

function uniqueInOrder(values) {
  return [...new Set(values.filter(Boolean))];
}

function text(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function displayResearchOutcome(outcome) {
  const normalized = normalizeResearchOutcomeName(outcome);
  return normalized || "unknown";
}

function normalizeResearchOutcomeName(outcome) {
  const value = String(outcome || "").trim();
  if (!value) {
    return "";
  }
  if (value === "improved_baseline") {
    return "met_targets";
  }
  if (value === "did_not_improve_baseline") {
    return "below_targets";
  }
  return value;
}

function outcomeClass(outcome) {
  const normalized = normalizeResearchOutcomeName(outcome);
  if (normalized === "met_targets") return "good";
  if (normalized === "below_targets") return "warn";
  if (normalized === "execution_blocked") return "bad";
  return "";
}

function formatMetric(value) {
  if (value === null || value === undefined || value === "") return "n/a";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(number >= 10 ? 2 : 4).replace(/0+$/, "").replace(/\.$/, "") : value;
}

function parseJson(value, fallback) {
  if (!value || Array.isArray(value)) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function pageUrl(path) {
  return new URL(path, window.location.href).href;
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

main().catch((error) => {
  console.error(error);
  text("dashboard-tagline", `Dashboard failed to load: ${error.message}`);
});
