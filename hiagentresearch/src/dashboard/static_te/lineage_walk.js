// The single render rule of the lineage-DAG model: connect a group's trajectory to its
// NEAREST IN-SCOPE ancestor. The backend emits the whole ancestry (lineage_parents[group].primary,
// nearest-first → the L0 baseline); this walk picks the first hop the active view actually shows —
// the direct parent on a per-area tab, the prior area-result on the Overview. This one function
// subsumes what used to be four pre-baked point types (inheritance-connector, area-spine,
// select-result, collapse-base) plus a visibility tangle.
//
// Pure and dependency-free (no DOM, no module globals) so it is unit-testable under `node --test`.
// Callers inject the view-specific predicates:
//   inScope(groupId)            -> bool : is this ancestor group drawn in the active scope?
//   valueAt(groupId, step, sha) -> number|null : the ancestor's real-node metric value to land on
//   baselineValue()             -> number|null : the L0 baseline value for the metric
//
// Returns {trajectory_x, metric_value, source_group_id, is_baseline} for the point the group's
// trajectory connects back to, or null when nothing resolvable is in scope.
export function walkToNearestInScope(primaryChain, deps) {
  const { inScope, valueAt, baselineValue } = deps;
  for (const hop of primaryChain || []) {
    if (hop.is_baseline) {
      const value = baselineValue();
      return value == null ? null : { trajectory_x: 0, metric_value: value, source_group_id: null, is_baseline: true };
    }
    if (!inScope(hop.group_id)) continue;
    const value = valueAt(hop.group_id, hop.trajectory_step, hop.commit_sha);
    if (value == null) continue;
    return {
      trajectory_x: Number(hop.trajectory_step),
      metric_value: value,
      source_group_id: hop.group_id,
      is_baseline: false,
    };
  }
  return null;
}
