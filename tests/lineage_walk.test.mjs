// Unit tests for the lineage-DAG render walk (the single rule that replaced the connector
// point-type taxonomy). Run with:  node --test tests/lineage_walk.test.mjs
//
// Pure-function tests — no DOM, no browser. The walk's view-specific inputs (scope predicate,
// value lookup, baseline value) are injected, so each scenario is exercised directly.
import { test } from "node:test";
import assert from "node:assert/strict";
import { walkToNearestInScope } from "../hiagentresearch/src/dashboard/static/lineage_walk.js";

// A chain mirroring the hard case: an optimization leaf inheriting from a SELECT collapse whose
// adopted commit belongs to a hidden leaf. nearest-first → L0.
const chain = [
  { group_id: "architecture__collapse", trajectory_step: 4, commit_sha: "archsha" },
  { group_id: "architecture__a2", trajectory_step: 4, commit_sha: "archsha" }, // the hidden adopted leaf
  { group_id: "polish", trajectory_step: 3, commit_sha: "polishsha" },
  { group_id: null, trajectory_step: 0, commit_sha: "base0", is_baseline: true },
];

const values = {
  architecture__collapse: { 4: 0.92 },
  architecture__a2: { 4: 0.92 },
  polish: { 3: 0.909 },
};
const valueAt = (gid, step) => (values[gid] && values[gid][step] != null ? values[gid][step] : null);
const baselineValue = () => 0.8;

test("connects to the direct parent when it is in scope", () => {
  const inScope = (gid) => gid === "architecture__collapse"; // per-area tab: ancestor result in scope
  const origin = walkToNearestInScope(chain, { inScope, valueAt, baselineValue });
  assert.equal(origin.source_group_id, "architecture__collapse");
  assert.equal(origin.trajectory_x, 4);
  assert.equal(origin.metric_value, 0.92);
});

test("never lands on the hidden adopted leaf — walks past it to the in-scope collapse", () => {
  // Both the collapse and the hidden leaf sit at step 4; only the collapse is ever in scope.
  const inScope = (gid) => gid === "architecture__collapse" || gid === "polish";
  const origin = walkToNearestInScope(chain, { inScope, valueAt, baselineValue });
  assert.equal(origin.source_group_id, "architecture__collapse");
});

test("Overview: collapse out of scope → walks to the prior area-result (polish)", () => {
  // On a view where neither the collapse nor the hidden leaf is shown, fall through to polish.
  const inScope = (gid) => gid === "polish";
  const origin = walkToNearestInScope(chain, { inScope, valueAt, baselineValue });
  assert.equal(origin.source_group_id, "polish");
  assert.equal(origin.trajectory_x, 3);
  assert.equal(origin.metric_value, 0.909);
});

test("nothing in scope → terminates at the L0 baseline", () => {
  const origin = walkToNearestInScope(chain, { inScope: () => false, valueAt, baselineValue });
  assert.equal(origin.source_group_id, null);
  assert.equal(origin.is_baseline, true);
  assert.equal(origin.trajectory_x, 0);
  assert.equal(origin.metric_value, 0.8);
});

test("skips an in-scope hop whose value cannot be resolved", () => {
  // architecture__collapse is in scope but has no resolvable value → fall through to polish.
  const inScope = (gid) => gid === "architecture__collapse" || gid === "polish";
  const noCollapseValue = (gid, step) => (gid === "architecture__collapse" ? null : valueAt(gid, step));
  const origin = walkToNearestInScope(chain, { inScope, valueAt: noCollapseValue, baselineValue });
  assert.equal(origin.source_group_id, "polish");
});

test("baseline with no value returns null (nothing to connect to)", () => {
  const origin = walkToNearestInScope(chain, { inScope: () => false, valueAt, baselineValue: () => null });
  assert.equal(origin, null);
});

test("empty chain returns null", () => {
  assert.equal(walkToNearestInScope([], { inScope: () => true, valueAt, baselineValue }), null);
  assert.equal(walkToNearestInScope(undefined, { inScope: () => true, valueAt, baselineValue }), null);
});
