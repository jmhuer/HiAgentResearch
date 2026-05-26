# HiAgentControl research plan pipeline

Run the full PI → Atlas → format → gate loop:

```
/ulw-loop "Use skill hac-plan-pipeline. Produce a research plan with N tasks (user specifies N). After each iteration run the gate script; stop only when stdout contains exactly <promise>DONE</promise>."
```

Replace `N` with the desired task count (e.g. 5).
