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
| `src/` | **Agent-owned workspace** — model, training, and tests the agent may edit/restructure |
| `.hiagentresearch/eval/` | **Read-only evaluation zone** — frozen scorer/adapter that produces authoritative metrics (outside this workspace) |

The agent owns everything under `mnist/`. Scoring, model loading, and
preprocessing live in the read-only eval zone; see the generated `AGENTS.md` in
this folder for the exact evaluation command and targets.

## Runnable entrypoints

```bash
# from the repository root
python -m pip install -r mnist/requirements.txt
python mnist/src/train.py --quick --output /tmp/hiagentresearch_mnist_train_metrics.json
python .hiagentresearch/eval/score.py --workdir mnist --quick --metrics /tmp/hiagentresearch_mnist_train_metrics.json
```

Full training (downloads MNIST to `mnist/data/`):

```bash
python mnist/src/train.py --epochs 3
python .hiagentresearch/eval/score.py --workdir mnist --accuracy-min 0.985 --latency-ms-max 13.0
```

Artifacts:

- `mnist/src/checkpoints/mnist_cnn_ensemble.pt` — trained weights
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
