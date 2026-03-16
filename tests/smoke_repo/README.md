# babel git-branch smoke test

End-to-end smoke test for the git worktree feature. Runs the babel orchestrator
against an isolated repo and verifies branch isolation and merge behavior across
two rounds of workers.

## Setup

Create the target repo (wipes and re-creates if it already exists):

```sh
./tests/smoke_repo/setup.sh           # creates ~/babel-smoke
./tests/smoke_repo/setup.sh /tmp/smoke  # or a custom path
```

## Run

`bab run` uses the current working directory as the workdir, so `cd` into the
smoke repo first, then pass the prompt file as an absolute path:

```sh
cd ~/babel-smoke
bab run \
  --max-rounds 2 \
  --max-workers 2 \
  /path/to/babel-research/tests/prompts/smoke_prompt.md
```

## Expected outcome

| Branch | Contents after run |
|--------|-------------------|
| `main` | Only initial README commit — **no worker commits** |
| `feature/calculator` | `calculator.py` with `add`, `subtract`, `multiply`, `divide` (merged from both round-1 and round-2 workers) |
| `feature/greeter` | `greeter.py` with `greet`, `farewell` only (round-1 merged; round-2 `shout` left on private sub-branch) |

## Verification commands

```sh
cd ~/babel-smoke

# Visual branch graph
git log --oneline --all --graph

# Confirm main is untouched
git log --oneline main

# Confirm calculator has all four functions
git show feature/calculator:calculator.py

# Confirm greeter has only greet/farewell (no shout)
git show feature/greeter:greeter.py

# Confirm shout exists on a private sub-branch but not on feature/greeter
git branch -a | grep greeter
git show feature/greeter--<session_id>:greeter.py  # replace with actual sub-branch name
```

## Scenario summary

```
Round 1 (wave 1 — parallel)
  worker A  feature/calculator--<id>  →  writes calculator.py  →  merges into feature/calculator
  worker B  feature/greeter--<id>     →  writes greeter.py     →  merges into feature/greeter

Round 2 (wave 2 — parallel, after wave 1 completes)
  worker C  feature/calculator--<id>  →  adds divide()         →  merges into feature/calculator  ✓
  worker D  feature/greeter--<id>     →  adds shout()          →  does NOT merge                  ✗
```
