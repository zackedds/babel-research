This project requires Python 3.11 or newer.

Sync the project environment with `uv`:

```bash
uv sync
```

Run the CLIs through `uv` from the repository root:

```bash
uv run bab PROMPT_FILE
uv run tasks get --id task-3
```

Install the OpenTUI dependencies for the activity feed once with:

```bash
cd src/tui/activity-feed
bun install
```

Then launch the activity feed from the repository root with:

```bash
uv run bab activity
```

Each new orchestration run now gets its own state directory under `.babel-agent/runs/<run-id>/`
with `run.json`, `sessions.json`, and `tasks.json`. The root `.babel-agent/index.json`
tracks the latest/active run so `uv run tasks ...` still resolves to the active run by default.

Run the test suite from the repository root with:

```bash
uv run python -m unittest
```

The `tests/` directory is packaged for default `unittest` discovery, so this command should execute the repository suite rather than returning a misleading `Ran 0 tests`.
