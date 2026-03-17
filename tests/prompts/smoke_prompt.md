You are setting up a two-round git-branch smoke test for the babel orchestrator.
The target repo is a small Python project. Create exactly 4 tasks in two waves as described below.

---

## Wave 1 — two independent tasks, no dependencies

### Task 1
- Title: `Create calculator module`
- Branch: `feature/calculator`
- Description:
  Create `calculator.py` in the repo root with the following three functions and a one-line module docstring:
  ```
  """Simple arithmetic functions."""

  def add(a, b): return a + b
  def subtract(a, b): return a - b
  def multiply(a, b): return a * b
  ```
  After writing the file, stage and commit it on `feature/calculator`.
  Then create `wiki/calculator.md` documenting the module: its purpose, the three functions, and their signatures. Stage and commit the wiki file.

### Task 2
- Title: `Create greeter module`
- Branch: `feature/greeter`
- Description:
  Create `greeter.py` in the repo root with the following two functions and a one-line module docstring:
  ```
  """Greeting utilities."""

  def greet(name): return f"Hello, {name}!"
  def farewell(name): return f"Goodbye, {name}!"
  ```
  After writing the file, stage and commit it on `feature/greeter`.
  Then create `wiki/greeter.md` documenting the module: its purpose, the two functions, and their signatures. Stage and commit the wiki file.

---

## Wave 2 — two tasks that build on the wave-1 branches

These tasks depend on their wave-1 counterparts (use `dep-add` to link them).

### Task 3
- Title: `Add division to calculator`
- Branch: `feature/calculator`
- Depends on: task-1
- Description:
  Read `calculator.py` on this branch. Add a `divide(a, b)` function that returns `a / b` and raises `ValueError("Cannot divide by zero")` when `b == 0`. Stage and commit.
  Then update `wiki/calculator.md` to document the new `divide` function. Stage and commit the wiki update.

### Task 4
- Title: `Add shout to greeter`
- Branch: `feature/greeter`
- Depends on: task-2
- Description:
  Read `greeter.py` on this branch. Add a `shout(name)` function that returns `f"HELLO, {name.upper()}!"`. Stage and commit.
  Then update `wiki/greeter.md` to document the new `shout` function. Stage and commit the wiki update.

---

## Planner instructions

1. Create all four tasks with the exact titles, branches, and descriptions above.
   Use `--branch feature/calculator` or `--branch feature/greeter` on every `tasks create` call.
   **Never use `--branch main`.**

2. After creating all four tasks, link wave-2 dependencies:
   ```
   tasks --run-id <run-id> dep-add --blocker-id task-1 --blocked-id task-3
   tasks --run-id <run-id> dep-add --blocker-id task-2 --blocked-id task-4
   ```

3. Mark the run ready:
   ```
   tasks --run-id <run-id> ready
   ```

Do not create any additional tasks or modify any files in the repo.
