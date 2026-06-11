export function mergeContributionEdges({
  grouped,
  selectIds,
  cycles,
  runsIdx,
  lineageParentsFor,
  walkToNearestInScope,
  deps,
}) {
  const edges = [];
  for (const [mergeId, rows] of Object.entries(grouped)) {
    if (selectIds.has(mergeId)) continue;
    const recordedCycles = (cycles || []).filter(
      (cycle) =>
        String(cycle.group_id) === String(mergeId) &&
        cycle.merge_cycle_provenance,
    );
    if (recordedCycles.length) {
      const provenanceCycles = recordedCycles.filter(
        (cycle) => cycle.merge_cycle_provenance.phase === "fold_in",
      );
      edges.push(
        ..._edgesFromCycleProvenance({
          mergeId,
          rows,
          provenanceCycles,
          lineageParentsFor,
          walkToNearestInScope,
          deps,
        }),
      );
      continue;
    }
    edges.push(
      ..._fallbackEdgesFromMergeTopology({
        mergeId,
        rows,
        runsIdx,
        lineageParentsFor,
        walkToNearestInScope,
        deps,
      }),
    );
  }
  return edges;
}

function _edgesFromCycleProvenance({
  mergeId,
  rows,
  provenanceCycles,
  lineageParentsFor,
  walkToNearestInScope,
  deps,
}) {
  const edges = [];
  for (const cycle of provenanceCycles) {
    const active = cycle.merge_cycle_provenance.active_source || {};
    const sourceGroupId = String(active.source_group_id || active.group_id || "");
    if (!sourceGroupId) continue;
    const targetRow = rows.find((row) => String(row.run_id) === String(cycle.run_id));
    if (!targetRow) continue;
    const origin = walkToNearestInScope(
      [
        { group_id: sourceGroupId, trajectory_step: active.trajectory_step, commit_sha: active.commit_sha },
        ...lineageParentsFor(sourceGroupId).primary,
      ],
      deps,
    );
    if (!origin || origin.source_group_id !== sourceGroupId) continue;
    const from = { x: Number(origin.trajectory_x), y: Number(origin.metric_value) };
    const target = { x: Number(targetRow.trajectory_x), y: Number(targetRow.metric_value) };
    if (!Number.isFinite(from.y) || !Number.isFinite(target.y) || (from.x === target.x && from.y === target.y)) {
      continue;
    }
    edges.push([
      { coord: [from.x, from.y], name: sourceGroupId },
      { coord: [target.x, target.y], name: mergeId },
    ]);
  }
  return edges;
}

function _fallbackEdgesFromMergeTopology({
  mergeId,
  rows,
  runsIdx,
  lineageParentsFor,
  walkToNearestInScope,
  deps,
}) {
  const foldIns = lineageParentsFor(mergeId).secondary || [];
  if (!foldIns.length) return [];
  const mergeRuns = rows.filter((row) => runsIdx.has(row.run_id) && Number.isFinite(Number(row.trajectory_x)));
  if (!mergeRuns.length) return [];
  const firstCycle = mergeRuns.reduce((a, b) => (Number(a.trajectory_x) <= Number(b.trajectory_x) ? a : b));
  const target = { x: Number(firstCycle.trajectory_x), y: Number(firstCycle.metric_value) };
  if (!Number.isFinite(target.y)) return [];

  const edges = [];
  for (const fold of foldIns) {
    const origin = walkToNearestInScope([fold, ...lineageParentsFor(fold.group_id).primary], deps);
    if (!origin || origin.source_group_id !== fold.group_id) continue;
    const from = { x: Number(origin.trajectory_x), y: Number(origin.metric_value) };
    if (!Number.isFinite(from.y) || (from.x === target.x && from.y === target.y)) continue;
    edges.push([
      { coord: [from.x, from.y], name: fold.group_id },
      { coord: [target.x, target.y], name: mergeId },
    ]);
  }
  return edges;
}
