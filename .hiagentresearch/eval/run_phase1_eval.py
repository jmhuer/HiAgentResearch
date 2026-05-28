#!/usr/bin/env python3
"""Phase-1 MNIST evaluation entrypoint for hiagentresearch.

This adapter is the frozen project authority for the MNIST example. It separates
execution health from research outcome: a valid experiment may fail to improve
baseline while still producing useful evidence for the next loop.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from hiagentresearch.src.core.config import load_config


def _parse_pytest_counts(stdout: str) -> tuple[int, int]:
    passed = 0
    failed = 0
    for line in stdout.splitlines():
        lower = line.lower()
        m_pass = re.search(r"(\d+)\s+passed", lower)
        if m_pass:
            passed = max(passed, int(m_pass.group(1)))
        m_fail = re.search(r"(\d+)\s+failed", lower)
        if m_fail:
            failed = max(failed, int(m_fail.group(1)))
    return passed, failed


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("could not locate JSON payload")
    return json.loads(stdout[start : end + 1])


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _tail(text: str, *, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def _metric_thresholds(group_id: str = "model_architecture") -> dict[str, float | None]:
    config = load_config()
    group = config.group_by_id(group_id)
    eval_config = group.evaluation or config.evaluation
    accuracy = eval_config.targets.get("accuracy")
    latency = eval_config.targets.get("latency_ms")
    return {
        "accuracy_min": accuracy.min if accuracy else None,
        "latency_ms_max": latency.max if latency else None,
    }


@contextlib.contextmanager
def _train_metrics_path() -> Path:
    run_dir = os.environ.get("HIAGENTRESEARCH_RUN_DIR")
    if run_dir:
        path = Path(run_dir) / "train_metrics.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="hiagentresearch-mnist-eval-") as tmp:
        yield Path(tmp) / "train_metrics.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run phase-1 MNIST eval contract.")
    parser.add_argument("--workdir", default="mnist", help="Agent-owned workspace root (relative to repo).")
    parser.add_argument("--group-id", default="model_architecture")
    parser.add_argument("--quick", action="store_true", help="Use quick train/eval settings for loop feedback.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    workdir = args.workdir
    thresholds = _metric_thresholds(args.group_id)

    # Keep the selection narrow and stable for phase-1 reproducibility.
    selected_tests = [f"{workdir}/src/test_kwta.py"]
    start = time.perf_counter()
    test_proc = _run([sys.executable, "-m", "pytest", "-q", *selected_tests], cwd=repo_root)
    with _train_metrics_path() as train_metrics_path:
        train_cmd = [sys.executable, f"{workdir}/src/train.py", "--output", str(train_metrics_path)]
        eval_cmd = [
            sys.executable,
            ".hiagentresearch/eval/score.py",
            "--workdir",
            workdir,
            "--metrics",
            str(train_metrics_path),
        ]
        if thresholds["accuracy_min"] is not None:
            eval_cmd.extend(["--accuracy-min", str(thresholds["accuracy_min"])])
        if thresholds["latency_ms_max"] is not None:
            eval_cmd.extend(["--latency-ms-max", str(thresholds["latency_ms_max"])])
        if args.quick:
            train_cmd.append("--quick")
            eval_cmd.append("--quick")
        train_proc = _run(train_cmd, cwd=repo_root) if test_proc.returncode == 0 else None
        eval_proc = _run(eval_cmd, cwd=repo_root) if train_proc and train_proc.returncode == 0 else None
        train_metrics = json.loads(train_metrics_path.read_text(encoding="utf-8")) if train_metrics_path.exists() else {}
        elapsed = time.perf_counter() - start

        tests_passed, tests_failed = _parse_pytest_counts(test_proc.stdout)
        tests_ok = test_proc.returncode == 0 and tests_failed == 0 and tests_passed > 0
        train_ok = bool(train_proc and train_proc.returncode == 0)

        eval_payload: dict = {}
        eval_parse_error = ""
        if eval_proc is not None:
            try:
                eval_payload = _extract_json(eval_proc.stdout)
            except (json.JSONDecodeError, ValueError) as exc:
                eval_parse_error = str(exc)

    eval_executed = eval_proc is not None and bool(eval_payload) and eval_proc.returncode in {0, 2}
    execution_passed = tests_ok and train_ok and eval_executed
    accuracy = eval_payload.get("accuracy")
    latency_ms = eval_payload.get("latency_ms")
    met_targets = bool(eval_payload.get("passed", False)) if execution_passed else False
    passed = execution_passed and met_targets

    failure_class = "none"
    if not tests_ok or not train_ok:
        failure_class = "code_failure"
    elif not eval_executed:
        error_text = str(eval_payload.get("error", "")).lower()
        failure_class = "code_failure" if "missing checkpoint" in error_text else "eval_failure"

    research_outcome = (
        "met_targets"
        if met_targets
        else ("below_targets" if execution_passed else "execution_blocked")
    )
    report = {
        "passed": passed,
        "execution_passed": execution_passed,
        "failure_class": failure_class,
        "research_outcome": research_outcome,
        "accuracy": accuracy,
        "latency_ms": latency_ms,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "duration_sec": round(elapsed, 4),
        "selected_tests": selected_tests,
        "pytest_exit_code": test_proc.returncode,
        "train_exit_code": train_proc.returncode if train_proc else None,
        "eval_exit_code": eval_proc.returncode if eval_proc else None,
        "eval_parse_error": eval_parse_error,
        "pytest_stdout_tail": _tail(test_proc.stdout),
        "pytest_stderr_tail": _tail(test_proc.stderr),
        "train_stdout_tail": _tail(train_proc.stdout) if train_proc else "",
        "train_stderr_tail": _tail(train_proc.stderr) if train_proc else "",
        "eval_stdout_tail": _tail(eval_proc.stdout) if eval_proc else "",
        "eval_stderr_tail": _tail(eval_proc.stderr) if eval_proc else "",
        "thresholds": thresholds,
        "train_metrics": train_metrics,
        "eval_report": eval_payload,
    }
    print(json.dumps(report, indent=2))
    return 0 if execution_passed else 2


if __name__ == "__main__":
    sys.exit(main())
