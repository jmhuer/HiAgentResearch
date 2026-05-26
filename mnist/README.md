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

## HiAgentResearch loop

From the repository root, run one or more real-agent research loops against this workspace:

```bash
scripts/run_phase1_group.sh model_architecture .
scripts/run_three_loops.sh model_architecture research/model-architecture 3
```

Runtime artifacts are written under `.hiagentresearch/runs/` and `.hiagentresearch/state/`.
