# MNIST improvement target

Example project workspace for HiAgentControl hierarchical planning.

## Baseline

See `baseline.json`:

- accuracy: 0.985
- latency_ms: 13.0

## Layout

| Area | Role |
| --- | --- |
| `pipeline/` | **Executable code** — training, builds, experiments that will be run |
| `eval/` | **Verification** — scripts and checks that test whether pipeline output meets targets |
| `baseline.json` | Gate thresholds for accuracy and latency |

## Runnable entrypoints

```bash
cd mnist
python -m pip install -r requirements.txt
python pipeline/train.py --quick          # fast smoke train
python eval/run_eval.py --quick           # re-measure and gate-check
```

Full training (downloads MNIST to `data/`):

```bash
python pipeline/train.py --epochs 3
python eval/run_eval.py
```

Artifacts:

- `pipeline/checkpoints/mnist_cnn.pt` — trained weights
- `pipeline/last_train_metrics.json` — metrics written by training

## Goal

Improve test accuracy above the baseline without increasing inference latency beyond 13 ms.

## Planning loop

From the repo root, run the gated plan-only loop against this workspace:

```bash
cd ~/github/HiAgentControl
cp -n mnist/.opencode/oh-my-openagent.jsonc.example mnist/.opencode/oh-my-openagent.jsonc
PYTHONPATH=. python -m hiagentcontrol.tools.run_plan_only_loop \
  --workdir mnist \
  --num-tasks 3
```

Deliverables land under `state/current/` (gitignored). See the root [README](../README.md).

## Access control

File access for agents is enforced by OpenCode permissions in `.opencode/`, not by ad-hoc path rules in planning prompts.

Copy `oh-my-openagent.jsonc.example` → `oh-my-openagent.jsonc` before the first planning run.
