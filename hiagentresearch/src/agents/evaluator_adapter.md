# Evaluator Adapter Skeleton

## Inputs

- eval stdout/stderr
- process exit code
- parser profile (`mnist_json_stdout`)

## Outputs

- normalized `metrics.json`
- `failure_class.json`
- `parsed_eval.json`

## Failure classes

- `none`
- `infra_failure`
- `code_failure`
- `eval_failure`
