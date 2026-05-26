# Targeted Rework

rework_phase: format

Resume at pipeline phase: **format** (pi | atlas | format).

## 1. plan-task-scope
- target_ref: /home/jmhuer/github/HiAgentControl/mnist/state/current/plan.json#tasks/*/scope
- failure_detail: Invalid task scopes: tasks[0] (Evaluate the impact of the ensemble's KW…): CHANGE cites train.py but FILES only lists eval/run_eval.py, pipeline/train.py, pipeline/train_optuna.py
- suggested_action: Run structure retry; fix JSON shape/scope/task count and rerun gate.

