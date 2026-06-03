const SQL_HTTPVFS_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/+esm";
const ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.esm.min.js";
const ALL_GROUPS = "__all__";
const SERIES_COLORS = ["#89b4ff", "#7ee787", "#f2cc60", "#ff8b8b", "#c9a8ff", "#77d4ff"];
const THRESHOLD_LINE_COLOR = "#9aa4b2";
const DISCRETE_LINE_METRICS = new Set(["tests_failed"]);

let dashboardData = null;
let selectedRunId = null;
let chartInstance = null;
let echartsModule = null;
let resizeListenerAttached = false;
let chartResizeObserver = null;

async function main() {
  const manifest = await fetchJson("./manifest.json");
  const summary = await fetchJson("./summary.json");
  dashboardData = mergePublishedSnapshot(await loadDashboardData(manifest, summary), summary);
  dashboardData.repository = manifest.repository || dashboardData.repository || {};
  dashboardData.summary = dashboardData.summary?.length ? dashboardData.summary : summary.groups;
  dashboardData.metric_names = chartMetricNames(dashboardData, summary);
  renderShell(manifest, summary);
  renderGroups(dashboardData.summary || []);
  renderFilters(dashboardData);
  renderRuns(dashboardData.runs || []);
  await renderChart();
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
    research_outcomes: outcomes.map((row) => ({
      ...coerceBooleans(row),
      research_outcome: normalizeResearchOutcomeName(row.research_outcome),
    })),
    experiments: experiments.map(parseExperiment),
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
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Failed to fetch ${path}: ${response.status}`);
  return response.json();
}

function renderShell(manifest, summary) {
  document.title = manifest.title || summary.title || "HiAgentResearch Dashboard";
  text("dashboard-title", manifest.title || summary.title || "HiAgentResearch");
  text("dashboard-tagline", "Experiment trajectory and research outcomes.");
  text("source-label", manifest.source || "dashboard");
  text("schema-label", `dashboard v${manifest.dashboard_schema_version || "?"}`);
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

function renderGroups(groups) {
  const container = document.getElementById("group-cards");
  container.innerHTML = groups
    .map(
      (group) => `
        <article class="card">
          <header class="card-header">
            <h3>${escapeHtml(group.group_id || "unknown")}</h3>
          </header>
          <div class="card-body">
            <div class="metric-row"><span>Lineage</span><strong>${escapeHtml(groupLineageLabel(group.group_id))}</strong></div>
            <div class="metric-row"><span>Failure class</span><strong>${escapeHtml(group.failure_class || "unknown")}</strong></div>
            <div class="metric-row"><span>Accuracy</span><strong>${formatMetric(group.accuracy)}</strong></div>
            <div class="metric-row"><span>Latency</span><strong>${formatMetric(group.latency_ms)} ms</strong></div>
            <div class="metric-row"><span>Next action</span><strong>${escapeHtml(group.next_action || "")}</strong></div>
          </div>
          <footer class="card-footer">
            <span class="card-footer-label">Outcome</span>
            <span class="badge ${outcomeClass(group.research_outcome)}">${escapeHtml(displayResearchOutcome(group.research_outcome))}</span>
          </footer>
        </article>
      `,
    )
    .join("");
}

function runCountLabel(groupId) {
  const runs = dashboardData.runs || [];
  if (groupId === ALL_GROUPS) {
    const groupCount = new Set(runs.map((run) => run.group_id).filter(Boolean)).size;
    return `${runs.length} runs · ${groupCount} groups`;
  }
  const filtered = runs.filter((run) => run.group_id === groupId);
  return `${filtered.length} runs · ${groupId}`;
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
    const groupId = document.getElementById("group-filter").value;
    const metricName = document.getElementById("metric-filter").value;
    updateChartRunSummary(groupId);
    const indexes = dashboardIndexes();
    const filtered = (dashboardData.metrics || []).filter(
      (metric) => (groupId === ALL_GROUPS || metric.group_id === groupId) && metric.metric_name === metricName,
    );
    let values = chartMetricPoints(filtered, indexes);
    values = appendBaselinePoints(values, metricName, groupId);
    values = assignTrajectoryPositions(values, dashboardData.lineage_topology);
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
  const experiment = indexes.experiments.get(selectedRunId);
  const artifacts = (dashboardData.artifacts || []).filter((item) => item.run_id === selectedRunId);
  const metrics = (dashboardData.metrics || []).filter((item) => item.run_id === selectedRunId);
  const fallbackExperiment = {
    hypothesis: `Direct eval fallback for ${run?.group_id || "unknown"} (${run?.run_id || "unknown"}): experiment manifest metadata was not uploaded.`,
    planned_code_changes: ["No experiment_manifest.json found for this run; showing eval-only provenance."],
  };
  const effectiveExperiment = experiment || fallbackExperiment;
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
      ${escapeHtml(displayResearchOutcome(outcome?.research_outcome))} — ${escapeHtml(outcome?.reason || "")}
    </div>
    <div class="detail-block">
      <strong>Hypothesis</strong>
      ${escapeHtml(effectiveExperiment.hypothesis || "No experiment manifest recorded.")}
    </div>
    <div class="detail-block">
      <strong>Planned Changes</strong>
      ${(effectiveExperiment.planned_code_changes || []).map((item) => `<div>${escapeHtml(item)}</div>`).join("") || "None recorded."}
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
        backgroundColor: "rgba(9, 11, 18, 0.92)",
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
  return { color: "#9aa4b2", fontSize: 12 };
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
  chartInstance = chartInstance && !chartInstance.isDisposed?.() ? chartInstance : echarts.init(canvas, "dark");
  chartInstance.off("click");
  attachChartResizeObserver(container);

  const positioned = assignTrajectoryPositions(values, dashboardData.lineage_topology);
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
    const seriesData = seriesDataForGroup(groupId, rows, trajectoryAxis, grouped, metricName);
    const visiblePoints = seriesData.filter((entry) => entry != null).length;
    const hasConnector = seriesData.some(
      (entry) => entry?.point?.is_inheritance_connector || entry?.point?.is_baseline_anchor,
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
  const series = referenceSeries ? [...trajectorySeries, referenceSeries] : trajectorySeries;
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
        textStyle: { color: "#9aa4b2", fontSize: 12 },
        pageIconColor: THRESHOLD_LINE_COLOR,
        pageIconInactiveColor: "rgba(154, 164, 178, 0.45)",
        pageTextStyle: { color: THRESHOLD_LINE_COLOR },
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
      xAxis: useValueAxis
        ? {
            type: "value",
            min: trajectoryMin,
            max: trajectoryMax === trajectoryMin ? trajectoryMax + 1 : trajectoryMax,
            minInterval: 1,
            splitLine: { show: false },
            axisLine: { onZero: true, lineStyle: { color: "rgba(255,255,255,0.18)" } },
            axisTick: { show: false },
            axisLabel: {
              color: "#9aa4b2",
              formatter: (value) => `L${value}`,
              hideOverlap: true,
            },
            ...axisName,
          }
        : {
            type: "category",
            data: categories,
            boundaryGap: true,
            axisLine: { lineStyle: { color: "rgba(255,255,255,0.18)" } },
            axisTick: { show: false },
            axisLabel: {
              color: "#9aa4b2",
              formatter: (value) => value,
              hideOverlap: true,
            },
            ...axisName,
          },
      yAxis: {
        type: "value",
        min: domain.min,
        max: domain.max,
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
        axisLabel: { color: "#9aa4b2", formatter: formatMetric },
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
                borderColor: "rgba(255,255,255,0.12)",
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
  updateChartRunSummary(groupId);
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

function groupLineageMode(groupId) {
  return dashboardData.lineage_topology?.groups?.[groupId]?.mode || "baseline";
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
  const groupIds = (groupFilter === ALL_GROUPS ? visibleGroupIds(points, topology) : [groupFilter]).filter(
    (groupId) => groupLineageMode(groupId) === "baseline",
  );
  if (!groupIds.length) {
    return points;
  }
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

function seriesDataForGroup(groupId, rows, trajectoryAxis, grouped, metricName) {
  const lineageRows =
    groupLineageMode(groupId) === "inherit" ? rows.filter((row) => !row.is_baseline_anchor) : rows;
  let series = chartSeriesData(lineageRows, trajectoryAxis);
  if (trajectoryAxis.mode !== "trajectory") {
    return series;
  }
  const topology = dashboardData.lineage_topology || {};
  const anchorMeta = topology.inherit_anchors?.[groupId] || {};
  const parentId = topology.groups?.[groupId]?.inherit_from;
  if (!parentId) {
    return withTrajectoryAnchor(series, trajectoryAxis, 0, () =>
      resolveBaselineAnchorPoint(groupId, metricName, lineageRows),
    );
  }
  // The anchor commit can live on an ancestor (a grandparent peak the immediate
  // parent never beat), so draw the connector from its true source group.
  const sourceGroup = anchorMeta.anchor_source_group || parentId;
  const parentAnchor = resolveInheritAnchorPoint(groupId, sourceGroup, grouped);
  if (!parentAnchor) {
    return series;
  }
  const parentX = Number(parentAnchor.trajectory_x);
  if (lineageRows.some((row) => Number(row.trajectory_x) === parentX && !row.is_baseline_anchor)) {
    return series;
  }
  return withTrajectoryAnchor(series, trajectoryAxis, parentX, () => ({
    ...parentAnchor,
    group_id: groupId,
    lineage_parent_group_id: sourceGroup,
    is_inheritance_connector: true,
  }));
}

function resolveInheritAnchorPoint(groupId, sourceGroup, grouped) {
  const anchorMeta = dashboardData.lineage_topology?.inherit_anchors?.[groupId] || {};
  const parentStep = anchorMeta.parent_trajectory_step ?? anchorMeta.parent_anchor_loop_index;
  const sourceRows = grouped[sourceGroup] || [];
  if (parentStep != null && parentStep !== "") {
    if (Number(parentStep) === 0) {
      const baselinePoint = sourceRows.find((row) => row.is_baseline_anchor);
      if (baselinePoint) {
        return baselinePoint;
      }
    }
    const atStep = sourceRows.find((row) => Number(row.trajectory_x) === Number(parentStep));
    if (atStep) {
      return atStep;
    }
  }
  const candidateRows = sourceRows
    .filter((point) => !point.is_baseline_anchor)
    .sort((left, right) => Number(left.trajectory_x) - Number(right.trajectory_x));
  const anchorSha = anchorMeta.commit_sha || inheritAnchorShaForGroup(groupId);
  if (anchorSha) {
    const matched = candidateRows.find((row) => shaMatches(row.commit_sha, anchorSha));
    if (matched) {
      return matched;
    }
  }
  if (!candidateRows.length) {
    return null;
  }
  if (Number(parentStep) === 0) {
    const baselinePoint = sourceRows.find((row) => row.is_baseline_anchor);
    if (baselinePoint) {
      return baselinePoint;
    }
  }
  return candidateRows[candidateRows.length - 1];
}

function inheritAnchorShaForGroup(groupId) {
  const topology = dashboardData.lineage_topology || {};
  const fromTopology = topology.inherit_anchors?.[groupId]?.commit_sha;
  if (fromTopology) {
    return String(fromTopology);
  }
  const experiments = (dashboardData.experiments || [])
    .filter((row) => row.group_id === groupId && row.lineage_anchor_sha)
    .sort((left, right) => Number(left.loop_index || 0) - Number(right.loop_index || 0));
  return experiments[0]?.lineage_anchor_sha ? String(experiments[0].lineage_anchor_sha) : "";
}

function shaMatches(left, right) {
  if (!left || !right) {
    return false;
  }
  const normalizedLeft = String(left).trim().toLowerCase();
  const normalizedRight = String(right).trim().toLowerCase();
  return (
    normalizedLeft === normalizedRight ||
    normalizedLeft.startsWith(normalizedRight) ||
    normalizedRight.startsWith(normalizedLeft)
  );
}

function withTrajectoryAnchor(series, trajectoryAxis, anchorX, resolveAnchor) {
  const anchor = resolveAnchor();
  if (!anchor) {
    return series;
  }
  return trajectoryAxis.indices.map((trajectoryX, index) => {
    if (Number(trajectoryX) !== Number(anchorX)) {
      return series[index] ?? null;
    }
    const existing = series[index];
    if (existing?.point?.group_id === anchor.group_id && !existing.point.is_inheritance_connector) {
      return existing;
    }
    return chartPointDatum(anchor);
  });
}

function resolveBaselineAnchorPoint(groupId, metricName, rows) {
  const own = rows.find((point) => point.is_baseline_anchor && point.group_id === groupId);
  if (own) {
    return own;
  }
  const topology = dashboardData.lineage_topology || {};
  const baseline = topology.baseline_snapshot;
  if (!baseline?.metrics || baseline.metrics[metricName] == null) {
    return null;
  }
  const metricValue = Number(baseline.metrics[metricName]);
  if (!Number.isFinite(metricValue)) {
    return null;
  }
  const ref = baseline.ref || "main";
  return {
    run_id: `baseline:${ref}`,
    group_id: groupId,
    metric_name: metricName,
    metric_value: metricValue,
    trajectory_x: 0,
    loop_index: 0,
    is_baseline_anchor: true,
    outcome: "baseline",
    hypothesis: `Frozen eval anchor (${ref})`,
  };
}

function chartPointDatum(point) {
  const selected = point.run_id === selectedRunId;
  const isWinner = point.is_lineage_winner || point.is_group_policy_winner || point.is_inherit_anchor;
  const symbol = isWinner ? "star" : "circle";
  const baseSize = isWinner ? 12 : 8;
  return {
    value: point.metric_value,
    point,
    symbol,
    symbolSize: selected ? baseSize + 2 : baseSize,
    itemStyle: selected
      ? { borderColor: "#f6f7fb", borderWidth: 2 }
      : isWinner
        ? { borderColor: "#f2cc60", borderWidth: 1.5 }
        : { borderWidth: 0 },
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
  const winnerHint = point.is_lineage_winner
    ? " · lineage winner"
    : point.is_group_policy_winner
      ? " · trajectory winner"
      : "";
  const connector = point.is_baseline_anchor
    ? " · frozen baseline anchor"
    : point.is_inheritance_connector && point.lineage_parent_group_id
      ? ` · continues from ${point.lineage_parent_group_id}`
      : point.is_inheritance_connector
        ? " · continues from parent"
        : "";
  const lineage =
    point.lineage_mode === "inherit" && point.lineage_parent_group_id
      ? ` · inherit ${point.lineage_parent_group_id}@${shortSha(point.lineage_anchor_sha)}${winnerHint}${inheritAnchorHint}`
      : connector + winnerHint + inheritAnchorHint;
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
  const groupMeta = topology?.groups || {};
  const inheritAnchors = topology?.inherit_anchors || {};
  return points.map((point) => {
    if (point.is_baseline_anchor) {
      return { ...point, trajectory_x: 0 };
    }
    const loopIndex = normalizedLoopIndex(point);
    if (loopIndex == null) {
      return { ...point, trajectory_x: 0 };
    }
    const mode = groupMeta[point.group_id]?.mode || "baseline";
    if (mode === "inherit") {
      const anchor = inheritAnchors[point.group_id] || {};
      const parentLoops = Number(anchor.parent_trajectory_step ?? anchor.parent_anchor_loop_index ?? 0);
      return { ...point, trajectory_x: parentLoops + loopIndex };
    }
    return { ...point, trajectory_x: loopIndex };
  });
}

function normalizedLoopIndex(point) {
  const loopIndex = Number(point.loop_index);
  if (!Number.isFinite(loopIndex) || loopIndex <= 0) {
    return null;
  }
  return loopIndex;
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

function parseExperiment(row) {
  return {
    ...row,
    target_files: parseJson(row.target_files_json, row.target_files || []),
    planned_code_changes: parseJson(row.planned_code_changes_json, row.planned_code_changes || []),
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
  return candidates.filter((metric) => !DISCRETE_LINE_METRICS.has(metric));
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
