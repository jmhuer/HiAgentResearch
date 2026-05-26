const SQL_HTTPVFS_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/+esm";
const SQL_WORKER_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/dist/sqlite.worker.js";
const SQL_WASM_URL = "https://cdn.jsdelivr.net/npm/sql.js-httpvfs/dist/sql-wasm.wasm";
const VEGA_EMBED_URL = "https://cdn.jsdelivr.net/npm/vega-embed/+esm";

let dashboardData = null;
let selectedRunId = null;

async function main() {
  const manifest = await fetchJson("./manifest.json");
  const summary = await fetchJson("./summary.json");
  dashboardData = await loadDashboardData(manifest);
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
  const { createDbWorker } = await import(SQL_HTTPVFS_URL);
  const worker = await createDbWorker(
    [
      {
        from: "inline",
        config: {
          serverMode: "full",
          requestChunkSize: manifest.sqlite?.request_chunk_size || 4096,
          url: manifest.sqlite?.url || manifest.database || "dashboard.db",
          cacheBust: manifest.cache_bust || "",
        },
      },
    ],
    SQL_WORKER_URL,
    SQL_WASM_URL,
    50 * 1024 * 1024,
  );
  const [summary, runs, metrics, outcomes, experiments, artifacts, metricNames] = await Promise.all([
    query(worker, "SELECT * FROM latest_group_summary ORDER BY group_id"),
    query(worker, "SELECT * FROM runs ORDER BY created_at DESC"),
    query(worker, "SELECT * FROM metric_series ORDER BY group_id, created_at, metric_name"),
    query(worker, "SELECT * FROM research_outcomes ORDER BY created_at"),
    query(worker, "SELECT * FROM experiments ORDER BY group_id, loop_index, created_at"),
    query(worker, "SELECT * FROM artifacts ORDER BY run_id, artifact_path"),
    query(worker, "SELECT DISTINCT metric_name FROM metrics ORDER BY metric_name"),
  ]);
  return {
    summary,
    runs,
    metrics,
    metric_names: metricNames.map((row) => row.metric_name),
    research_outcomes: outcomes.map(coerceBooleans),
    experiments: experiments.map(parseExperiment),
    artifacts,
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
  setOptions("group-filter", groups);
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
          <small>${escapeHtml(run.created_at || "")}</small>
        </button>
      `,
    )
    .join("");
  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedRunId = button.dataset.runId;
      renderRuns(dashboardData.runs || []);
      renderRunDetail();
    });
  });
  renderRunDetail();
}

async function renderChart() {
  const groupId = document.getElementById("group-filter").value;
  const metricName = document.getElementById("metric-filter").value;
  const values = (dashboardData.metrics || [])
    .filter((metric) => metric.group_id === groupId && metric.metric_name === metricName)
    .map((metric) => ({
      run_id: metric.run_id,
      created_at: metric.created_at,
      metric_value: Number(metric.metric_value),
      metric_name: metric.metric_name,
    }));
  const container = document.getElementById("metric-chart");
  if (!values.length) {
    container.textContent = "No metric data for this selection.";
    return;
  }
  try {
    const { default: vegaEmbed } = await import(VEGA_EMBED_URL);
    await vegaEmbed(
      container,
      {
        $schema: "https://vega.github.io/schema/vega-lite/v5.json",
        background: "transparent",
        data: { values },
        width: "container",
        height: 300,
        mark: { type: "line", point: true, tooltip: true },
        encoding: {
          x: { field: "created_at", type: "temporal", title: "Run time" },
          y: { field: "metric_value", type: "quantitative", title: metricName },
          color: { value: "#89b4ff" },
          tooltip: [
            { field: "run_id", type: "nominal" },
            { field: "created_at", type: "temporal" },
            { field: "metric_value", type: "quantitative" },
          ],
        },
      },
      { actions: false, theme: "dark" },
    );
  } catch (error) {
    console.warn("Vega-Lite unavailable, using SVG fallback", error);
    renderSvgChart(container, values);
  }
}

function renderRunDetail() {
  const run = (dashboardData.runs || []).find((item) => item.run_id === selectedRunId);
  const outcome = (dashboardData.research_outcomes || []).find((item) => item.run_id === selectedRunId);
  const experiment = (dashboardData.experiments || []).find((item) => item.run_id === selectedRunId);
  const artifacts = (dashboardData.artifacts || []).filter((item) => item.run_id === selectedRunId);
  const metrics = (dashboardData.metrics || []).filter((item) => item.run_id === selectedRunId);
  const container = document.getElementById("run-detail");
  if (!run) {
    container.textContent = "Select a run.";
    return;
  }
  container.innerHTML = `
    <div class="detail-block">
      <strong>${escapeHtml(run.run_id)}</strong>
      ${escapeHtml(run.group_id)} · ${escapeHtml(run.branch)} · ${escapeHtml(run.created_at || "")}
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

function renderSvgChart(container, values) {
  const width = 760;
  const height = 300;
  const padding = 34;
  const ys = values.map((value) => value.metric_value);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = Math.max(max - min, 0.000001);
  const points = values.map((value, index) => {
    const x = padding + (index / Math.max(values.length - 1, 1)) * (width - padding * 2);
    const y = height - padding - ((value.metric_value - min) / span) * (height - padding * 2);
    return `${x},${y}`;
  });
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Metric chart">
      <polyline fill="none" stroke="#89b4ff" stroke-width="3" points="${points.join(" ")}"></polyline>
      ${points
        .map((point, index) => {
          const [x, y] = point.split(",");
          return `<circle cx="${x}" cy="${y}" r="4" fill="#7ee787"><title>${escapeHtml(values[index].run_id)}: ${values[index].metric_value}</title></circle>`;
        })
        .join("")}
    </svg>
  `;
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
    improved_baseline: Boolean(row.improved_baseline),
    metrics_ok: Boolean(row.metrics_ok),
  };
}

function setOptions(id, values) {
  const select = document.getElementById(id);
  select.innerHTML = values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
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
