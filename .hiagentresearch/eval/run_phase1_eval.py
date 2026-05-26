#!/usr/bin/env python3
"""Phase-1 MNIST evaluation entrypoint for hiagentresearch.

This script is intentionally lightweight and deterministic:
- runs stable MNIST smoke tests,
- emits structured JSON for registry ingestion,
- preserves stdout/stderr from underlying test runs for visibility.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run phase-1 MNIST eval contract.")
    parser.add_argument("--mnist-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--quick", action="store_true", help="Reserved for compatibility; tests are already lightweight.")
    args = parser.parse_args()

    root = args.mnist_root.resolve()
    baseline_path = root / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}

    # Keep the selection narrow and stable for phase-1 reproducibility.
    selected_tests = ["mnist/pipeline/test_kwta.py"]
    cmd = [sys.executable, "-m", "pytest", "-q", *selected_tests]

    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=root.parents[0],
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - start

    tests_passed, tests_failed = _parse_pytest_counts(proc.stdout)
    passed = proc.returncode == 0 and tests_failed == 0 and tests_passed > 0

    report = {
        "passed": passed,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "duration_sec": round(elapsed, 4),
        "selected_tests": selected_tests,
        "pytest_exit_code": proc.returncode,
        "baseline": baseline,
    }
    print(json.dumps(report, indent=2))
    if proc.stdout:
        print("\n--- pytest stdout ---")
        print(proc.stdout.rstrip())
    if proc.stderr:
        print("\n--- pytest stderr ---", file=sys.stderr)
        print(proc.stderr.rstrip(), file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
