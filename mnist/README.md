# MNIST improvement target

Example project workspace for HiAgentControl hierarchical planning.

## Target Metrics

Gate thresholds live in the root `config.yaml` so the framework has one source
of truth for research outcomes:

- accuracy: 0.985
- latency_ms: 13.0

## Layout

| Area | Role |
| --- | --- |
| `pipeline/` | **Executable code** — training, builds, experiments that will be run |
| `eval/` | **Verification** — scripts and checks that test whether pipeline output meets targets |

## Runnable entrypoints

```bash
cd mnist
python -m pip install -r requirements.txt
python pipeline/train.py --quick --output /tmp/hiagentresearch_mnist_train_metrics.json
python eval/run_eval.py --quick --metrics /tmp/hiagentresearch_mnist_train_metrics.json
```

Full training (downloads MNIST to `data/`):

```bash
python pipeline/train.py --epochs 3
python eval/run_eval.py --accuracy-min 0.985 --latency-ms-max 13.0
```

Artifacts:

- `pipeline/checkpoints/mnist_cnn_ensemble.pt` — trained weights
- train metrics are printed to stdout or written to the explicit `--output` path

## Goal

Improve test accuracy above the configured target without increasing inference latency beyond 13 ms.

## HiAgentResearch loop

From the repository root, run one or more real-agent research loops against this workspace:

```bash
hiagentresearch run-group --group-id model_architecture --workdir . --quick
hiagentresearch loops --group-id model_architecture --branch research/model-architecture --loops 3 --quick
```

Runtime artifacts are written under `.hiagentresearch/runs/`; the local registry lives in `.hiagentresearch/state/evals.db`.
