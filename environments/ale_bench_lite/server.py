#!/usr/bin/env python3
"""HOST-side HTTP server that exposes ALE-Bench public evaluation over HTTP.

Run with:
    python server.py
or:
    uvicorn server:app --port 8765

The agent container calls:
    POST /problems/{problem_id}/public-eval
    Body: {"code": "...", "language": "cpp20"}

Response:
    {"problem_id", "language", "overall_judge_result",
     "overall_absolute_score", "overall_relative_score"}
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import ale_bench
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ALE-Bench Eval Server")

# Maximum number of concurrent sessions per problem.
# ale_bench.Session is NOT thread-safe (mutates _current_resource_usage,
# _action_log, _last_public_eval_time), so each concurrent eval needs its own
# session instance.  This pool size controls how many evals can run in parallel
# per problem; excess requests queue until a session is available.
POOL_SIZE = int(os.environ.get("ALE_BENCH_POOL_SIZE", "10"))

KNOWN_PROBLEM_IDS = [
    "ahc008", "ahc011", "ahc015", "ahc016",
    "ahc024", "ahc025", "ahc026", "ahc027", "ahc039", "ahc046",
]

# Per-problem queues of idle sessions.
_session_pools: dict[str, asyncio.Queue] = {}
# Total sessions created per problem (idle + checked-out).
_pool_created: dict[str, int] = {}
# Per-problem lock to serialise pool expansion (not eval itself).
_pool_locks: dict[str, asyncio.Lock] = {}

# Module-level lock for safe pool-structure initialisation.
_init_lock: asyncio.Lock | None = None


def _get_init_lock() -> asyncio.Lock:
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


async def _ensure_pool_structures(problem_id: str) -> None:
    """Create pool data-structures for problem_id if they don't exist yet."""
    if problem_id not in _session_pools:
        async with _get_init_lock():
            if problem_id not in _session_pools:
                _session_pools[problem_id] = asyncio.Queue()
                _pool_created[problem_id] = 0
                _pool_locks[problem_id] = asyncio.Lock()


async def _checkout_session(problem_id: str) -> object:
    """Return an idle session, creating a new one if the pool can still grow."""
    await _ensure_pool_structures(problem_id)
    pool = _session_pools[problem_id]

    # Fast path: grab an already-idle session.
    try:
        return pool.get_nowait()
    except asyncio.QueueEmpty:
        pass

    # Try to expand the pool up to POOL_SIZE.
    should_create = False
    async with _pool_locks[problem_id]:
        if _pool_created[problem_id] < POOL_SIZE:
            _pool_created[problem_id] += 1
            should_create = True

    if should_create:
        try:
            return await asyncio.to_thread(
                ale_bench.start,
                problem_id=problem_id,
                lite_version=False,
                num_workers=8,
                run_visualization_server=False,
            )
        except Exception:
            async with _pool_locks[problem_id]:
                _pool_created[problem_id] -= 1
            raise

    # Pool is at max capacity; block until a session is returned.
    return await pool.get()


def _checkin_session(problem_id: str, session: object) -> None:
    """Return a session to the idle pool."""
    _session_pools[problem_id].put_nowait(session)


class EvalRequest(BaseModel):
    code: str
    language: str = "cpp20"


class EvalResponse(BaseModel):
    problem_id: str
    language: str
    overall_judge_result: str
    overall_absolute_score: int
    overall_relative_score: Optional[int]


@app.on_event("startup")
async def preload_sessions() -> None:
    tasks = [_checkout_session(pid) for pid in KNOWN_PROBLEM_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for pid, r in zip(KNOWN_PROBLEM_IDS, results):
        if isinstance(r, Exception):
            print(f"[startup] WARNING: failed to preload {pid}: {r}")
        else:
            _checkin_session(pid, r)
            print(f"[startup] preloaded session for {pid}")


@app.post("/problems/{problem_id}/public-eval", response_model=EvalResponse)
async def public_eval(problem_id: str, req: EvalRequest) -> EvalResponse:
    if problem_id not in KNOWN_PROBLEM_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown problem_id: {problem_id!r}")

    try:
        session = await _checkout_session(problem_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to acquire session: {exc}") from exc

    try:
        result = await asyncio.to_thread(
            session.public_eval, req.code, code_language=req.language
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"public_eval failed: {exc}") from exc
    finally:
        _checkin_session(problem_id, session)

    return EvalResponse(
        problem_id=problem_id,
        language=req.language,
        overall_judge_result=str(result.overall_judge_result),
        overall_absolute_score=int(result.overall_absolute_score),
        overall_relative_score=(
            int(result.overall_relative_score)
            if result.overall_relative_score is not None
            else None
        ),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("ALE_BENCH_SERVER_PORT", "8765"))
    uvicorn.run(app, host="0.0.0.0", port=port)
