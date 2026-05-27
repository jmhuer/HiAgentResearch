const SQL_HTTPVFS_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/+esm";
const ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.esm.min.js";
const ALL_GROUPS = "__all__";
const SERIES_COLORS = ["#89b4ff", "#7ee787", "#f2cc60", "#ff8b8b", "#c9a8ff", "#77d4ff"];
const THRESHOLD_LINE_COLOR = "#9aa4b2";
const DISCRETE_LINE_METRICS = new Set(["tests_passed", "tests_failed"]);

let dashboardData = null;
let selectedRunId = null;
let chartInstance = null;
let echartsModule = null;
let resizeListenerAttached = false;

async function main() {
  const manifest = await fetchJson("./manifest.json");
  const summary = await fetchJson("./summary.json");
  dashboardData = await loadDashboardData(manifest);
  dashboardData.repository = manifest.repository || dashboardData.repository || {};
  dashboardData.summary = dashboardData.summary?.length ? dashboardData.summary : summary.groups;
  dashboardData.metric_names = chartMetricNames(dashboardData, summary);
  dashboardData.lineage_topology = dashboardData.lineage_topology || summary.lineage_topology || { chains: [], groups: {} };
  renderShell(manifest, summary);
  renderGroups(dashboardData.summary || []);
  renderFilters(dashboardData);
  renderRuns(dashboardData.runs || []);
  await renderChart();
}

async function loadDashboardData(manifest) {
  try {
    return await loadFromSqlite(manifest);
  } catch (error) {
    console.warn("SQLite adapter unavailable, using JSON fallback", error);
    return fetchJson("./dashboard.json");
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
  const [summary, runs, metrics, outcomes, experiments, artifacts, metricNames, expectations] = await Promise.all([
    query(worker, "SELECT * FROM latest_group_summary ORDER BY group_id"),
    query(worker, "SELECT * FROM runs ORDER BY group_id, created_at DESC"),
    query(worker, "SELECT * FROM metric_series ORDER BY group_id, created_at, metric_name"),
    query(worker, "SELECT * FROM research_outcomes ORDER BY created_at"),
    query(worker, "SELECT * FROM experiments ORDER BY group_id, loop_index, created_at"),
    query(worker, "SELECT * FROM artifacts ORDER BY run_id, artifact_path"),
    query(worker, "SELECT DISTINCT metric_name FROM metrics ORDER BY metric_name"),
    query(worker, "SELECT * FROM metric_expectations ORDER BY group_id, metric_name"),
  ]);
  return {
    summary,
    runs,
    metrics,
    metric_names: metricNames.map((row) => row.metric_name),
    research_outcomes: outcomes.map(coerceBooleans),
    experiments: experiments.map(parseExperiment),
    artifacts,
    metric_expectations: expectations.map(normalizeExpectation),
  };
}

async function query(worker, sql, params = []) {
  const result = await worker.db.exec(sql, params);
  if (!result.length) return [];
  const { columns, values } = result[0];
  return values.map((row) => Object.fromEntries(row.map((value, index) => [columns[index], value])));
}

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Failed to fetch ${path}: ${response.status}`);
  return response.json();
}

function renderShell(manifest, summary) {
  document.title = manifest.title || summary.title || "HiAgentResearch Dashboard";
  text("dashboard-title", manifest.title || summary.title || "HiAgentResearch");
  text("dashboard-subtitle", `${dashboardData.runs?.length || 0} runs across ${dashboardData.summary?.length || 0} groups`);
  text("source-label", manifest.source || "dashboard");
  text("schema-label", `dashboard v${manifest.dashboard_schema_version || "?"}`);
}

function groupLineageLabel(groupId) {
  const meta = (dashboardData.lineage_topology || {}).groups?.[groupId];
  if (!meta) return "";
  if (meta.mode === "inherit" && meta.inherit_from) {
    return `Inherits ${meta.inherit_from}`;
  }
  return "Baseline · L0 frozen-eval anchor";
}

function renderGroups(groups) {
  const container = document.getElementById("group-cards");
  container.innerHTML = groups
    .map(
      (group) => `
        <article class="card">
          <h3>${escapeHtml(group.group_id || "unknown")}</h3>
          <div class="metric-row"><span>Lineage</span><strong>${escapeHtml(groupLineageLabel(group.group_id))}</strong></div>
          <span class="badge ${outcomeClass(group.research_outcome)}">${escapeHtml(group.research_outcome || "unknown")}</span>
          <div class="metric-row"><span>Failure class</span><strong>${escapeHtml(group.failure_class || "unknown")}</strong></div>
          <div class="metric-row"><span>Accuracy</span><strong>${formatMetric(group.accuracy)}</strong></div>
          <div class="metric-row"><span>Latency</span><strong>${formatMetric(group.latency_ms)} ms</strong></div>
          <div class="metric-row"><span>Next action</span><strong>${escapeHtml(group.next_action || "")}</strong></div>
        </article>
      `,
    )
    .join("");
}

function renderFilters(data) {
  const groups = unique((data.runs || []).map((run) => run.group_id));
  const metrics = chartMetricNames(data);
  setOptions("group-filter", groups, { allLabel: "All groups" });
  setOptions("metric-filter", metrics);
  document.getElementById("group-filter").addEventListener("change", renderChart);
  document.getElementById("metric-filter").addEventListener("change", renderChart);
}

function renderRuns(runs) {
  const container = document.getElementById("run-list");
  const sorted = sortRunsForDisplay(runs);
  if (!sorted.length) {
    container.textContent = "No runs found.";
    return;
  }
  selectedRunId = selectedRunId || sorted[0].run_id;
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

async function renderChart() {
  const container = document.getElementById("metric-chart");
  try {
    const groupId = document.getElementById("group-filter").value;
    const metricName = document.getElementById("metric-filter").value;
    const indexes = dashboardIndexes();
    const filtered = (dashboardData.metrics || []).filter(
      (metric) => (groupId === ALL_GROUPS || metric.group_id === groupId) && metric.metric_name === metricName,
    );
    let values = chartMetricPoints(filtered, indexes);
    values = appendBaselinePoints(values, metricName, groupId);
    values = assignTrajectoryPositions(values, dashboardData.lineage_topology);
    if (!values.length) {
      if (chartInstance && !chartInstance.isDisposed?.()) {
        chartInstance.dispose();
        chartInstance = null;
      }
      container.textContent = "No metric data for this selection.";
      return;
    }
    const expectations = expectationLines({ groupId, metricName });
    await renderEChart(container, values, metricName, expectations);
  } catch (error) {
    console.error("Dashboard chart render failed", error);
    container.textContent = `Chart failed to render: ${error.message}`;
  }
}

function renderRunDetail() {
  const indexes = dashboardIndexes();
  const run = indexes.runs.get(selectedRunId);
  const outcome = indexes.outcomes.get(selectedRunId);
  const experiment = indexes.experiments.get(selectedRunId);
  const artifacts = (dashboardData.artifacts || []).filter((item) => item.run_id === selectedRunId);
  const metrics = (dashboardData.metrics || []).filter((item) => item.run_id === selectedRunId);
  const container = document.getElementById("run-detail");
  const links = runLinks(run);
  if (!run) {
    container.textContent = "Select a run.";
    return;
  }
  container.innerHTML = `
    <div class="detail-block">
      <strong>${escapeHtml(run.run_id)}</strong>
      ${escapeHtml(run.group_id)} · ${escapeHtml(run.branch)} · ${escapeHtml(run.created_at || "")}
    </div>
    <div class="detail-block detail-links">
      <strong>Links</strong>
      ${links.length ? links.map((link) => `<a href="${escapeAttribute(link.href)}" target="_blank" rel="noreferrer">${escapeHtml(link.label)}</a>`).join("") : "No repository links available."}
    </div>
    <div class="detail-block">
      <strong>Outcome</strong>
      ${escapeHtml(outcome?.research_outcome || "unknown")} — ${escapeHtml(outcome?.reason || "")}
    </div>
    <div class="detail-block">
      <strong>Hypothesis</strong>
      ${escapeHtml(experiment?.hypothesis || "No experiment manifest recorded.")}
    </div>
    <div class="detail-block">
      <strong>Planned Changes</strong>
      ${(experiment?.planned_code_changes || []).map((item) => `<div>${escapeHtml(item)}</div>`).join("") || "None recorded."}
    </div>
    <div class="detail-block">
      <strong>Metrics</strong>
      ${metrics.map((metric) => `<div>${escapeHtml(metric.metric_name)} = ${formatMetric(metric.metric_value)}</div>`).join("")}
    </div>
    <div class="detail-block">
      <strong>Artifacts</strong>
      ${artifacts.map((artifact) => `<div>${escapeHtml(artifact.artifact_path)} (${artifact.size_bytes} bytes)</div>`).join("") || "None recorded."}
    </div>
  `;
}

async function renderEChart(container, values, metricName, expectations) {
  const echarts = await loadECharts();
  let canvas = document.getElementById("metric-chart-canvas");
  if (!canvas) {
    container.innerHTML = '<div id="metric-chart-canvas" class="metric-chart-canvas"></div>';
    canvas = document.getElementById("metric-chart-canvas");
  }
  chartInstance = chartInstance && !chartInstance.isDisposed?.() ? chartInstance : echarts.init(canvas, "dark");
  chartInstance.off("click");

  const positioned = assignTrajectoryPositions(values, dashboardData.lineage_topology);
  const ordered = [...positioned].sort((left, right) => {
    const traj = Number(left.trajectory_x) - Number(right.trajectory_x);
    if (traj !== 0) return traj;
    return String(left.group_id).localeCompare(String(right.group_id));
  });
  const trajectoryAxis = trajectoryCategoryAxis(ordered);
  const categories = trajectoryAxis.labels;
  const grouped = groupBy(ordered, (point) => point.group_id);
  const targetLines = dedupeExpectationLines(expectations);
  const domain = metricDomain(ordered, targetLines);
  const groupSeries = Object.entries(grouped).map(([groupId, rows], index) => {
    const seriesData = seriesDataForGroup(groupId, rows, trajectoryAxis, grouped);
    const visiblePoints = seriesData.filter((entry) => entry != null).length;
    const hasConnector = seriesData.some((entry) => entry?.point?.is_inheritance_connector);
    return {
    name: groupId,
    type: "line",
    smooth: true,
    symbol: "circle",
    symbolSize: 9,
    showAllSymbol: true,
    connectNulls: visiblePoints > 1 || hasConnector,
    emphasis: { focus: "series" },
    lineStyle: { width: visiblePoints > 1 || hasConnector ? 3 : 0, type: "solid" },
    itemStyle: { borderColor: "#090b12", borderWidth: 1.5 },
    data: seriesData,
    markLine:
      index === 0 && targetLines.length
        ? {
            symbol: "none",
            silent: true,
            lineStyle: { color: THRESHOLD_LINE_COLOR, type: "dashed", width: 1.5 },
            label: {
              color: THRESHOLD_LINE_COLOR,
              formatter: (params) => params.name,
              position: "insideEndTop",
            },
            data: targetLines.map((line) => ({ name: line.label, yAxis: line.value })),
          }
        : undefined,
  };
  });
  const series = groupSeries;

  chartInstance.setOption(
    {
      backgroundColor: "transparent",
      color: SERIES_COLORS,
      animationDuration: 450,
      grid: { left: 52, right: 28, top: 70, bottom: categories.length > 12 ? 72 : 42, containLabel: true },
      legend: {
        top: 4,
        left: 0,
        textStyle: { color: "#9aa4b2" },
        icon: "roundRect",
      },
      tooltip: {
        trigger: "item",
        appendToBody: true,
        confine: false,
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.16)",
        backgroundColor: "rgba(9,11,18,0.96)",
        textStyle: { color: "#f6f7fb" },
        extraCssText:
          "max-width:min(340px,calc(100vw - 48px));white-space:normal;word-break:break-word;line-height:1.45;box-shadow:0 20px 60px rgba(0,0,0,.45);border-radius:14px;padding:12px;",
        formatter: (params) => pointTooltipHtml(params.data?.point),
      },
      xAxis: {
        type: "category",
        data: categories,
        boundaryGap: categories.length <= 1,
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.18)" } },
        axisTick: { show: false },
        axisLabel: {
          color: "#9aa4b2",
          formatter: (value) => value,
          hideOverlap: true,
        },
        name: "Lineage step",
      },
      yAxis: {
        type: "value",
        name: metricName,
        min: domain.min,
        max: domain.max,
        nameTextStyle: { color: "#9aa4b2" },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
        axisLabel: { color: "#9aa4b2", formatter: formatMetric },
      },
      dataZoom:
        categories.length > 12
          ? [
              { type: "inside", throttle: 50 },
              {
                type: "slider",
                height: 24,
                bottom: 14,
                borderColor: "rgba(255,255,255,0.12)",
                fillerColor: "rgba(137,180,255,0.18)",
                handleStyle: { color: "#89b4ff" },
                textStyle: { color: "#9aa4b2" },
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
    selectRun(point.run_id, { scroll: true });
    void renderChart();
  });
  if (!resizeListenerAttached) {
    window.addEventListener("resize", () => chartInstance?.resize());
    resizeListenerAttached = true;
  }
  chartInstance.resize();
}

function chartMetricPoints(metrics, indexes) {
  const enriched = metrics
    .map((metric) => enrichMetricPoint(metric, indexes))
    .map((point) => resolveLoopIndex(point, indexes))
    .filter((point) => Number.isFinite(point.metric_value) && !point.is_baseline_anchor);
  const byGroupLoop = new Map();
  for (const point of enriched) {
    const loopKey = point.loop_index != null ? String(point.loop_index) : point.run_id;
    const key = `${point.group_id}:${loopKey}`;
    const existing = byGroupLoop.get(key);
    if (!existing || preferCanonicalRun(point.run_id, existing.run_id)) {
      byGroupLoop.set(key, point);
    }
  }
  return [...byGroupLoop.values()];
}

function resolveLoopIndex(point, indexes) {
  if (point.loop_index != null && point.loop_index > 0) {
    return point;
  }
  const run = indexes.runs.get(point.run_id) || {};
  const correlationId = String(run.correlation_id || "").trim();
  if (correlationId) {
    const linked = indexes.experiments.get(correlationId);
    if (linked?.loop_index != null) {
      return { ...point, loop_index: Number(linked.loop_index) };
    }
  }
  return point;
}

function appendBaselinePoints(points, metricName, groupFilter) {
  const topology = dashboardData.lineage_topology || {};
  const baseline = topology.baseline_snapshot;
  if (!baseline?.metrics || baseline.metrics[metricName] == null) {
    return points;
  }
  const metricValue = Number(baseline.metrics[metricName]);
  if (!Number.isFinite(metricValue)) {
    return points;
  }
  const groupIds = groupFilter === ALL_GROUPS ? visibleGroupIds(points, topology) : [groupFilter];
  const ref = baseline.ref || "main";
  const anchors = groupIds.map((groupId) => ({
    run_id: `baseline:${ref}`,
    group_id: groupId,
    metric_name: metricName,
    metric_value: metricValue,
    loop_index: 0,
    trajectory_x: 0,
    is_baseline_anchor: true,
    outcome: "baseline",
    hypothesis: `Frozen eval anchor (${ref})`,
  }));
  return [...anchors, ...points];
}

function visibleGroupIds(points, topology) {
  const ids = new Set(points.map((point) => point.group_id));
  for (const wave of topology.execution_waves || []) {
    for (const groupId of wave) {
      ids.add(groupId);
    }
  }
  return [...ids].sort();
}

function preferCanonicalRun(candidate, incumbent) {
  const candidateGithub = String(candidate).startsWith("gh_");
  const incumbentGithub = String(incumbent).startsWith("gh_");
  if (candidateGithub && !incumbentGithub) return true;
  if (!candidateGithub && incumbentGithub) return false;
  return String(candidate).localeCompare(String(incumbent)) > 0;
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

function seriesDataForGroup(groupId, rows, trajectoryAxis, grouped) {
  const base = chartSeriesData(rows, trajectoryAxis);
  if (trajectoryAxis.mode !== "trajectory") {
    return base;
  }
  const parentId = dashboardData.lineage_topology?.groups?.[groupId]?.inherit_from;
  if (!parentId) {
    return base;
  }
  const parentRows = (grouped[parentId] || [])
    .filter((point) => !point.is_baseline_anchor)
    .sort((left, right) => Number(left.trajectory_x) - Number(right.trajectory_x));
  if (!parentRows.length) {
    return base;
  }
  const parentLast = parentRows[parentRows.length - 1];
  const parentX = Number(parentLast.trajectory_x);
  if (rows.some((row) => Number(row.trajectory_x) === parentX)) {
    return base;
  }
  return trajectoryAxis.indices.map((trajectoryX) => {
    if (Number(trajectoryX) === parentX) {
      return chartPointDatum({
        ...parentLast,
        group_id: groupId,
        lineage_parent_group_id: parentId,
        is_inheritance_connector: true,
      });
    }
    const point = rows.find((row) => Number(row.trajectory_x) === trajectoryX);
    if (!point) return null;
    return chartPointDatum(point);
  });
}

function chartPointDatum(point) {
  const selected = point.run_id === selectedRunId;
  const isBaseline = Boolean(point.is_baseline_anchor);
  return {
    value: point.metric_value,
    point,
    symbol: isBaseline ? "diamond" : "circle",
    symbolSize: selected ? 13 : isBaseline ? 11 : 9,
    itemStyle: selected ? { borderColor: "#f6f7fb", borderWidth: 3 } : undefined,
  };
}

function enrichMetricPoint(metric, indexes) {
  const run = indexes.runs.get(metric.run_id) || {};
  const outcome = indexes.outcomes.get(metric.run_id) || {};
  const experiment = indexes.experiments.get(metric.run_id) || {};
  const loopIndex =
    experiment.loop_index != null
      ? Number(experiment.loop_index)
      : metric.loop_index != null
        ? Number(metric.loop_index)
        : null;
  return {
    ...metric,
    branch: run.branch || metric.branch || "",
    commit_sha: run.commit_sha || metric.commit_sha || "",
    workflow_run_id: run.workflow_run_id || metric.workflow_run_id || "",
    loop_index: Number.isFinite(loopIndex) && loopIndex > 0 ? loopIndex : null,
    lineage_mode: experiment.lineage_mode || "",
    lineage_parent_group_id: experiment.lineage_parent_group_id || "",
    lineage_anchor_sha: experiment.lineage_anchor_sha || "",
    lineage_anchor_policy: experiment.lineage_anchor_policy || "",
    outcome: outcome.research_outcome || "unknown",
    reason: outcome.reason || "",
    hypothesis: experiment.hypothesis || "",
    planned_code_changes: experiment.planned_code_changes || [],
    metric_value: Number(metric.metric_value),
    trajectory_x:
      metric.trajectory_x != null && metric.trajectory_x !== "" ? Number(metric.trajectory_x) : undefined,
    is_baseline_anchor: Boolean(metric.is_baseline_anchor),
  };
}

function expectationLines({ groupId, metricName }) {
  return (dashboardData.metric_expectations || []).filter(
    (expectation) => (groupId === ALL_GROUPS || expectation.group_id === groupId) && expectation.metric_name === metricName,
  );
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
  const connector =
    point.is_inheritance_connector && point.lineage_parent_group_id
      ? ` · continues from ${point.lineage_parent_group_id}`
      : point.is_inheritance_connector
        ? " · continues from parent"
        : "";
  const lineage =
    point.lineage_mode === "inherit" && point.lineage_parent_group_id
      ? ` · inherit ${point.lineage_parent_group_id}@${shortSha(point.lineage_anchor_sha)}`
      : connector;
  return `
    <div class="tooltip-title">${escapeHtml(point.group_id)} · ${escapeHtml(point.metric_name)} ${formatMetric(point.metric_value)}</div>
    <div class="tooltip-muted">${escapeHtml(trajectoryLabel(point))}${lineage} · ${escapeHtml(shortRunId(point.run_id))} · ${escapeHtml(point.outcome)}</div>
    <div class="tooltip-body">${escapeHtml(shortText(point.hypothesis || point.reason || "No summary recorded.", 190))}</div>
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
function assignTrajectoryPositions(points, topology) {
  const waves = topology?.execution_waves || [];
  if (!waves.length) {
    return points.map((point) => {
      const loopIndex = normalizedLoopIndex(point);
      return { ...point, trajectory_x: loopIndex ?? 0 };
    });
  }
  const groupWave = new Map();
  waves.forEach((wave, waveIndex) => {
    wave.forEach((groupId) => groupWave.set(groupId, waveIndex));
  });
  const depths = waveDepths(points, waves);
  return points.map((point) => {
    if (point.is_baseline_anchor) {
      return { ...point, trajectory_x: 0 };
    }
    const loopIndex = normalizedLoopIndex(point);
    if (loopIndex == null) {
      return { ...point, trajectory_x: 0 };
    }
    const waveIndex = groupWave.get(point.group_id) ?? 0;
    return { ...point, trajectory_x: depths[waveIndex] + loopIndex, lineage_wave: waveIndex };
  });
}

function waveDepths(points, waves) {
  const depths = [];
  let cumulative = 0;
  for (const wave of waves) {
    depths.push(cumulative);
    let maxLoop = 0;
    for (const groupId of wave) {
      for (const point of points) {
        if (point.is_baseline_anchor || point.group_id !== groupId) continue;
        const loopIndex = normalizedLoopIndex(point);
        if (loopIndex != null) {
          maxLoop = Math.max(maxLoop, loopIndex);
        }
      }
    }
    cumulative += maxLoop;
  }
  return depths;
}

function normalizedLoopIndex(point) {
  const loopIndex = Number(point.loop_index);
  if (!Number.isFinite(loopIndex) || loopIndex <= 0) {
    return null;
  }
  return loopIndex;
}

function trajectoryCategoryAxis(points) {
  let indices = uniqueInOrder(
    points
      .map((point) => point.trajectory_x)
      .filter((value) => value != null && value !== "" && Number.isFinite(Number(value)))
      .map((value) => Number(value))
      .sort((left, right) => left - right),
  );
  const hasBaseline =
    points.some((point) => point.is_baseline_anchor) ||
    Boolean(dashboardData.lineage_topology?.baseline_snapshot?.metrics);
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
    const loopLeft = Number((indexes.experiments.get(left.run_id) || {}).loop_index || 0);
    const loopRight = Number((indexes.experiments.get(right.run_id) || {}).loop_index || 0);
    if (loopLeft !== loopRight) return loopLeft - loopRight;
    return String(left.created_at).localeCompare(String(right.created_at));
  });
}

function loopLabel(runOrPoint) {
  const loopIndex = runOrPoint.loop_index ?? (dashboardIndexes().experiments.get(runOrPoint.run_id) || {}).loop_index;
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
    experiments: byRunId(dashboardData.experiments || []),
  };
}

function byRunId(rows) {
  return new Map(rows.map((row) => [row.run_id, row]));
}

async function loadECharts() {
  echartsModule = echartsModule || (await import(ECHARTS_URL));
  return echartsModule;
}

function dedupeExpectationLines(expectations) {
  const lines = expectations.flatMap((expectation) => [
    expectation.min !== null && expectation.min !== undefined
      ? { key: `min:${expectation.min}`, value: Number(expectation.min), label: `target min ${formatMetric(expectation.min)}` }
      : null,
    expectation.max !== null && expectation.max !== undefined
      ? { key: `max:${expectation.max}`, value: Number(expectation.max), label: `target max ${formatMetric(expectation.max)}` }
      : null,
  ]);
  return [...new Map(lines.filter(Boolean).map((line) => [line.key, line])).values()];
}

function metricDomain(points, targetLines) {
  const values = [
    ...points.map((point) => point.metric_value),
    ...targetLines.map((line) => line.value),
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

function normalizeExpectation(row) {
  return {
    group_id: row.group_id,
    metric_name: row.metric_name,
    min: row.min ?? row.min_value ?? null,
    max: row.max ?? row.max_value ?? null,
    source: row.source || "unknown",
  };
}

function parseExperiment(row) {
  return {
    ...row,
    target_files: parseJson(row.target_files_json, row.target_files || []),
    planned_code_changes: parseJson(row.planned_code_changes_json, row.planned_code_changes || []),
  };
}

function coerceBooleans(row) {
  return {
    ...row,
    improved_baseline: asBoolean(row.improved_baseline),
    metrics_ok: asBoolean(row.metrics_ok),
  };
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
  return candidates.filter((metric) => !DISCRETE_LINE_METRICS.has(metric));
}

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

function uniqueInOrder(values) {
  return [...new Set(values.filter(Boolean))];
}

function text(id, value) {
  document.getElementById(id).textContent = value;
}

function outcomeClass(outcome) {
  if (outcome === "improved_baseline") return "good";
  if (outcome === "did_not_improve_baseline") return "warn";
  if (outcome === "execution_blocked") return "bad";
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
  text("dashboard-subtitle", `Dashboard failed to load: ${error.message}`);
});
