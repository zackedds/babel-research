# Babel Agent Architecture

Babel is a multi-agent research orchestration system. It coordinates three specialized agents — **Planner**, **Worker**, and **Librarian** — in a repeating loop to iteratively explore open-ended research problems.

---

## High-Level Flow

```
                          ┌─────────────────────────────────────┐
                          │           bab CLI (cli.py)          │
                          │  run <prompt_file> | inspect | kill  │
                          └──────────────────┬──────────────────┘
                                             │ spawns subprocess
                                             ▼
                          ┌─────────────────────────────────────┐
                          │      Orchestration Driver           │
                          │         (driver.py)                 │
                          │                                     │
                          │  ┌──────────────────────────────┐  │
                          │  │         State Machine         │  │
                          │  │                               │  │
                          │  │   ┌─────────┐                │  │
                          │  │   │ PLANNER │◄───────────┐   │  │
                          │  │   └────┬────┘            │   │  │
                          │  │        │ tasks "ready"   │   │  │
                          │  │        ▼                 │   │  │
                          │  │  ┌──────────┐            │   │  │
                          │  │  │ WORKERS  │            │   │  │
                          │  │  │(parallel)│            │   │  │
                          │  │  └────┬─────┘            │   │  │
                          │  │       │ tasks "closed"   │   │  │
                          │  │       ▼                  │   │  │
                          │  │  ┌───────────┐           │   │  │
                          │  │  │ LIBRARIAN │           │   │  │
                          │  │  └─────┬─────┘           │   │  │
                          │  │        │ "wiki_ready" ───►┘   │  │
                          │  └────────┼─────────────────┘   │  │
                          │           │ max_rounds reached?  │  │
                          └───────────┼─────────────────────┘  │
                                      ▼ done
```

---

## Orchestration Loop Detail

```
Driver polls every 0.2s
         │
         ▼
┌────────────────────┐
│   PLANNER PHASE    │
│                    │
│  Creates 1 planner │
│  session with:     │
│  - initial prompt  │
│  - task summaries  │◄──── tasks.json (open/closed tasks)
│  - wiki context    │◄──── wiki/ directory (prior findings)
│                    │
│  Planner outputs:  │
│  - new tasks       │────► tasks.json (new "open" tasks)
│  - new branches    │────► git branches
│  - calls `ready`   │
└────────┬───────────┘
         │ task state = "ready"
         ▼
┌────────────────────┐
│   WORKER PHASE     │
│                    │
│  For each ready    │
│  task (up to       │
│  max_workers):     │
│                    │
│  ┌──────────────┐  │
│  │   Worker 1   │  │  each in isolated
│  │  (worktree)  │  │  git worktree on
│  └──────────────┘  │  task branch
│  ┌──────────────┐  │
│  │   Worker 2   │  │
│  │  (worktree)  │  │
│  └──────────────┘  │
│        ...         │
│                    │
│  Workers output:   │
│  - code/research   │────► git commits on task branch
│  - wiki findings   │────► wiki/ files (merged back to main)
│  - calls `close`   │
└────────┬───────────┘
         │ all tasks "closed"
         │ wiki synced from branches → main
         ▼
┌────────────────────┐
│  LIBRARIAN PHASE   │
│                    │
│  Reads all wiki    │
│  files, reorganizes│
│  for discoverability│
│                    │
│  Librarian outputs:│
│  - reorganized wiki│────► wiki/ (cleaned up, deduplicated)
│  - MAIN.md update  │────► leaderboard + reflection
│  - calls           │
│    `wiki_ready`    │
└────────┬───────────┘
         │ loop back or done
```

---

## Component Map

```
babel-research/
├── src/
│   ├── bab_cli/
│   │   └── cli.py              ← CLI entry point (bab run/inspect/kill)
│   │
│   ├── orchestration/
│   │   ├── driver.py           ← Main orchestration loop (subprocess)
│   │   ├── sessions.py         ← Session creation & lifecycle management
│   │   ├── runs.py             ← OrchestrationRun persistence (RunStore)
│   │   └── state_paths.py      ← .babel-agent/ directory layout
│   │
│   ├── roles/
│   │   ├── planner.yaml        ← Planner prompt template + model config
│   │   ├── worker.yaml         ← Worker prompt template + model config
│   │   ├── librarian.yaml      ← Librarian prompt template + model config
│   │   └── registry.py         ← Loads role definitions from YAML
│   │
│   ├── runtime/
│   │   ├── runtimes.py         ← Runtime definitions (codex, claude-code)
│   │   ├── tmux.py             ← Tmux session management & monitoring
│   │   ├── watcher.py          ← Per-session completion watcher (subprocess)
│   │   ├── activity.py         ← Activity snapshot for TUI
│   │   └── inspect.py          ← Run inspection display
│   │
│   ├── tasks/
│   │   ├── store.py            ← JSON task store with file locking
│   │   └── tasks_cli.py        ← `bab-tasks` CLI used by agents
│   │
│   └── utils/
│       ├── config.py           ← .babel-agent/config.toml loader
│       ├── models.py           ← Pydantic data models
│       └── json_store.py       ← Atomic JSON writes with locking
│
└── environments/               ← Task-specific evaluation environments
```

---

## Session Lifecycle

Each agent (planner, worker, librarian) runs as a **tmux session** managed by the orchestrator:

```
Orchestrator.create_session(spec)
        │
        ├─► render Jinja2 prompt template
        │       (injects tasks, wiki, run_id, etc.)
        │
        ├─► create tmux session
        │       startup_command: e.g. `claude` or `codex`
        │
        ├─► wait for ready signal
        │       (detects banner text or idle prompt)
        │
        ├─► paste rendered prompt into tmux pane
        │
        └─► spawn Watcher subprocess
                │
                ├─► polls tmux pane content + task state
                ├─► 10-min inactivity timeout
                │       workers: marked failed
                │       others:  killed
                ├─► detects completion by task state:
                │       worker    → task "closed"
                │       planner   → task state "ready"
                │       librarian → task state "wiki_ready"
                └─► kills tmux session, cleans up worktree
```

---

## State & Persistence

Everything lives under `.babel-agent/` in the working directory:

```
.babel-agent/
├── config.toml                     ← Runtime, model, and role overrides
├── index.json                      ← Index of all runs (for fast lookup)
└── runs/
    └── {run_id}/
        ├── run.json                ← OrchestrationRun state
        │                             (phase, rounds, session IDs, etc.)
        ├── sessions.json           ← AgentSession records
        │                             (status, outcome, timestamps)
        ├── tasks.json              ← Task graph
        │                             (open → ready → assigned → closed)
        └── logs/
            ├── driver.log          ← Orchestration loop log
            ├── {session_id}.log    ← Per-session watcher log
            └── ...
```

---

## Task States

Tasks are the contract between Planner and Workers:

```
  (planner creates)
        │
        ▼
      open
        │
        │ dependencies satisfied
        ▼
      ready ──────────────────────► [driver assigns to worker]
        │                                     │
        │                                     ▼
        │                                 assigned
        │                                     │
        │                         worker completes + closes
        │                                     ▼
        └──────────────────────────────►   closed
```

Tasks can also have **blockers** (dependency edges):
- A task with unresolved blockers stays `open`, not `ready`
- Planner manages the dependency graph

---

## Git & Worktree Model

```
main branch
    │
    ├── wiki/           ← shared knowledge base (all agents read/write)
    └── ...             ← source code, experiments
    │
    ├── task/branch-A   ← worker A's isolated worktree
    │       └── wiki/   ← wiki changes synced → main after close
    │
    └── task/branch-B   ← worker B's isolated worktree
            └── wiki/   ← wiki changes synced → main after close
```

- **Planner** works on main branch directly
- **Workers** each get a `git worktree` on their task branch — fully isolated
- After worker completion, driver cherry-picks/merges wiki files back to main
- **Librarian** works on main, reorganizes the merged wiki

---

## Agent Runtimes

The runtime is configurable via `config.toml` (`agent_runtime = "codex"` or `"claude-code"`):

| Runtime | Startup Command | Ready Detection |
|---------|----------------|-----------------|
| `codex` | `codex` | CLI banner text |
| `claude-code` | `claude` | Shell prompt prefix |

Both runtimes support:
- Auto-responding to confirmation prompts
- Model selection via flag
- Session log capture

---

## Configuration

`.babel-agent/config.toml`:

```toml
agent_runtime = "claude-code"   # or "codex"

[roles.planner]
model = "claude-opus-4-6"
thinking = true

[roles.worker]
model = "claude-sonnet-4-6"
pre_prompt_commands = ["git fetch --all"]
```
