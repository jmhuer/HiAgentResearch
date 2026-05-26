const SQL_HTTPVFS_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/+esm";
const ALL_GROUPS = "__all__";
const SERIES_COLORS = ["#89b4ff", "#7ee787", "#f2cc60", "#ff8b8b", "#c9a8ff", "#77d4ff"];

let dashboardData = null;
let selectedRunId = null;

async function main() {
  const manifest = await fetchJson("./manifest.json");
  const summary = await fetchJson("./summary.json");
  dashboardData = await loadDashboardData(manifest);
  dashboardData.repository = manifest.repository || dashboardData.repository || {};
  dashboardData.summary = dashboardData.summary?.length ? dashboardData.summary : summary.groups;
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
    query(worker, "SELECT * FROM runs ORDER BY created_at DESC"),
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

function renderGroups(groups) {
  const container = document.getElementById("group-cards");
  container.innerHTML = groups
    .map(
      (group) => `
        <article class="card">
          <h3>${escapeHtml(group.group_id || "unknown")}</h3>
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
  const metrics = data.metric_names?.length ? data.metric_names : unique((data.metrics || []).map((metric) => metric.metric_name));
  setOptions("group-filter", groups, { allLabel: "All groups" });
  setOptions("metric-filter", metrics);
  document.getElementById("group-filter").addEventListener("change", renderChart);
  document.getElementById("metric-filter").addEventListener("change", renderChart);
}

function renderRuns(runs) {
  const container = document.getElementById("run-list");
  if (!runs.length) {
    container.textContent = "No runs found.";
    return;
  }
  selectedRunId = selectedRunId || runs[0].run_id;
  container.innerHTML = runs
    .map(
      (run) => `
        <button class="run-button ${run.run_id === selectedRunId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}">
          <strong>${escapeHtml(run.group_id)}</strong>
          <div>${escapeHtml(run.run_id)} · ${escapeHtml(run.failure_class)}</div>
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
  const groupId = document.getElementById("group-filter").value;
  const metricName = document.getElementById("metric-filter").value;
  const values = (dashboardData.metrics || [])
    .filter((metric) => (groupId === ALL_GROUPS || metric.group_id === groupId) && metric.metric_name === metricName)
    .map(enrichMetricPoint);
  const container = document.getElementById("metric-chart");
  if (!values.length) {
    container.textContent = "No metric data for this selection.";
    return;
  }
  const expectations = expectationLines({ groupId, metricName });
  renderSvgChart(container, values, metricName, expectations);
}

function renderRunDetail() {
  const run = (dashboardData.runs || []).find((item) => item.run_id === selectedRunId);
  const outcome = (dashboardData.research_outcomes || []).find((item) => item.run_id === selectedRunId);
  const experiment = (dashboardData.experiments || []).find((item) => item.run_id === selectedRunId);
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

function renderSvgChart(container, values, metricName, expectations) {
  const width = 820;
  const height = 300;
  const padding = 44;
  const ys = [
    ...values.map((value) => value.metric_value),
    ...expectations.flatMap((expectation) => [expectation.min, expectation.max]).filter((value) => value !== null && value !== undefined),
  ];
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = Math.max(max - min, 0.000001);
  const ordered = values.sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)));
  const xForIndex = (index) => padding + (index / Math.max(ordered.length - 1, 1)) * (width - padding * 2);
  const yForValue = (value) => height - padding - ((value - min) / span) * (height - padding * 2);
  const pointRows = ordered.map((value, index) => {
    const x = xForIndex(index);
    const y = yForValue(value.metric_value);
    return { ...value, x, y, index };
  });
  const series = groupBy(pointRows, (value) => value.group_id);
  const polylines = Object.entries(series)
    .map(([groupId, rows], seriesIndex) => {
      const color = SERIES_COLORS[seriesIndex % SERIES_COLORS.length];
      return `<polyline fill="none" stroke="${color}" stroke-width="3" points="${rows.map((row) => `${row.x},${row.y}`).join(" ")}"><title>${escapeHtml(groupId)}</title></polyline>`;
    })
    .join("");
  const expectationLines = expectations
    .flatMap((expectation) => [
      expectation.min !== null && expectation.min !== undefined ? { ...expectation, value: expectation.min, label: "min" } : null,
      expectation.max !== null && expectation.max !== undefined ? { ...expectation, value: expectation.max, label: "max" } : null,
    ])
    .filter(Boolean)
    .map((expectation) => {
      const y = yForValue(Number(expectation.value));
      return `
        <g class="target-line">
          <line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}"></line>
          <text x="${width - padding}" y="${y - 6}" text-anchor="end">${escapeHtml(`${expectation.group_id} ${expectation.metric_name} ${expectation.label} ${formatMetric(expectation.value)}`)}</text>
        </g>
      `;
    })
    .join("");
  const labelStride = Math.max(1, Math.ceil(pointRows.length / 6));
  const labels = pointRows
    .filter((_, index) => index % labelStride === 0 || index === pointRows.length - 1)
    .map((value) => `<text x="${value.x}" y="${height - 8}" text-anchor="middle">${escapeHtml(shortRunId(value.run_id))}</text>`);
  const circles = pointRows
    .map((point) => {
      const seriesIndex = Object.keys(series).indexOf(point.group_id);
      const color = SERIES_COLORS[seriesIndex % SERIES_COLORS.length];
      return `<circle class="metric-point ${point.run_id === selectedRunId ? "active" : ""}" cx="${point.x}" cy="${point.y}" r="5" fill="${color}" data-run-id="${escapeAttribute(point.run_id)}" data-point-index="${point.index}"><title>${escapeHtml(pointTooltipText(point))}</title></circle>`;
    })
    .join("");
  const legend = Object.keys(series)
    .map((groupId, index) => `<span><i style="background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></i>${escapeHtml(groupId)}</span>`)
    .join("");
  container.innerHTML = `
    <div class="chart-legend">${legend}</div>
    <svg class="metric-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(metricName)} metric chart">
      <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
      <line x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}"></line>
      <text x="${padding}" y="18">${escapeHtml(metricName)}: ${formatMetric(min)} to ${formatMetric(max)}</text>
      ${expectationLines}
      ${polylines}
      ${circles}
      ${labels.join("")}
    </svg>
    <div id="chart-tooltip" class="chart-tooltip" hidden></div>
  `;
  attachChartInteractions(container, pointRows);
}

function enrichMetricPoint(metric) {
  const run = (dashboardData.runs || []).find((item) => item.run_id === metric.run_id) || {};
  const outcome = (dashboardData.research_outcomes || []).find((item) => item.run_id === metric.run_id) || {};
  const experiment = (dashboardData.experiments || []).find((item) => item.run_id === metric.run_id) || {};
  return {
    ...metric,
    branch: run.branch || metric.branch || "",
    commit_sha: run.commit_sha || metric.commit_sha || "",
    workflow_run_id: run.workflow_run_id || metric.workflow_run_id || "",
    outcome: outcome.research_outcome || "unknown",
    reason: outcome.reason || "",
    hypothesis: experiment.hypothesis || "",
    planned_code_changes: experiment.planned_code_changes || [],
    metric_value: Number(metric.metric_value),
  };
}

function expectationLines({ groupId, metricName }) {
  return (dashboardData.metric_expectations || []).filter(
    (expectation) => (groupId === ALL_GROUPS || expectation.group_id === groupId) && expectation.metric_name === metricName,
  );
}

function attachChartInteractions(container, points) {
  const tooltip = container.querySelector("#chart-tooltip");
  container.querySelectorAll(".metric-point").forEach((point) => {
    point.addEventListener("mousemove", (event) => {
      const row = points[Number(point.dataset.pointIndex)];
      tooltip.innerHTML = pointTooltipHtml(row);
      tooltip.hidden = false;
      tooltip.style.left = `${event.offsetX + 18}px`;
      tooltip.style.top = `${event.offsetY + 18}px`;
    });
    point.addEventListener("mouseleave", () => {
      tooltip.hidden = true;
    });
    point.addEventListener("click", () => {
      selectRun(point.dataset.runId, { scroll: true });
      renderChart();
    });
  });
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
  return `
    <strong>${escapeHtml(point.group_id)} · ${escapeHtml(point.metric_name)} ${formatMetric(point.metric_value)}</strong>
    <span>${escapeHtml(shortRunId(point.run_id))} · ${escapeHtml(point.outcome)}</span>
    <span>${escapeHtml(shortText(point.hypothesis || point.reason || "No summary recorded.", 150))}</span>
    ${(point.planned_code_changes || []).slice(0, 2).map((item) => `<span>${escapeHtml(shortText(item, 110))}</span>`).join("")}
  `;
}

function pointTooltipText(point) {
  return `${point.group_id} ${point.metric_name}=${formatMetric(point.metric_value)} · ${point.outcome} · ${shortText(point.hypothesis || point.reason || "", 140)}`;
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

function groupBy(values, keyFn) {
  return values.reduce((groups, value) => {
    const key = keyFn(value);
    groups[key] = groups[key] || [];
    groups[key].push(value);
    return groups;
  }, {});
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

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort();
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
