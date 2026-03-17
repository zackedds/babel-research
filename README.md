## Setup
This project requires Python 3.11 or newer.

Sync the project environment with `uv`:
```bash
uv sync
```

Install OpenTUI dependencies:
```bash
bun install
```

To make the `bab` cli tool accessible, symlink `bab` onto your PATH so it's accessible from any directory:
```bash
ln -s "$(pwd)/.venv/bin/bab" ~/.local/bin/bab
```
Assumes that ~/.local/bin is on your $PATH (standard on Linux/macOS; add export PATH="$HOME/.local/bin:$PATH" to your shell rc if not) 
   
## Using bab cli
Launch a run in any working directory with a prompt file:
```bash
bab --max-rounds 2 --max-workers 5 prompt.md
```

Launch the activity feed TUI with:
```bash
bab activity
```

Launch a grid of active tmux sessions with (wip currently point in time):
```bash
bab grid
```

Kill a run with:
```bash
bab kill [run_id]
```

## Testing
Primary end-to-end smoke test:
- Create smoke test directory: ```./tests/smoke_repo/setup.sh```
- Run smoke test: ```cd ~/babel-smoke && bab run --max-rounds 2 --max-workers 2 path_to//babel-research/tests/prompts/smoke_prompt.md```

## Run and session tracking
Each new orchestration run gets its own state directory under `.babel-agent/runs/<run-id>/` with `run.json`, `sessions.json`, and `tasks.json`. The root `.babel-agent/index.json` tracks the latest/active run.
