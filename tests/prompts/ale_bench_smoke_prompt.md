# ALE-Bench Smoke Test — {{ problem_id }}

This is a smoke test run. The goal is NOT to improve the score — it is to verify
the evaluation infrastructure is working end-to-end.

## Your Instructions (Planner)

Dispatch **exactly 2 tasks** with no dependencies between them:

**Task 1 — eval-baseline-1**
Description: Run ale-bench-eval on baseline.cpp and record the result in wiki/eval_task1.md

**Task 2 — eval-baseline-2**
Description: Run ale-bench-eval on baseline.cpp and record the result in wiki/eval_task2.md

After creating both tasks, mark them ready immediately. Do NOT create additional tasks.

## Worker Instructions

For each task assigned to you:
1. Run the evaluation:
   ```
   ale-bench-eval --problem-id {{ problem_id }} --code-path /testbed/workspace/baseline.cpp
   ```
2. Write a markdown file in `wiki/` with the problem id, the score, and the judge result.
   Name it `wiki/eval_task1.md` or `wiki/eval_task2.md` based on your task name.
3. Report completion.

The known baseline score is {{ initial_score }}. The evaluation should return a similar value.
Do NOT modify any source files. Do NOT attempt to improve the solution.
