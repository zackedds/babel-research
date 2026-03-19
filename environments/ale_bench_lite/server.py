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
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import ale_bench
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ALE-Bench Eval Server")

# Thread pool for offloading blocking eval calls (5 = max concurrent agents)
_executor = ThreadPoolExecutor(max_workers=5)

# Session cache: problem_id -> ale_bench session
_sessions: dict[str, object] = {}

# Per-session locks to serialize concurrent evals on the same session
_session_locks: dict[str, asyncio.Lock] = {}

# Module-level lock for safe session initialization
_init_lock: asyncio.Lock | None = None

KNOWN_PROBLEM_IDS = [
    "ahc008", "ahc011", "ahc015", "ahc016",
    "ahc024", "ahc025", "ahc026", "ahc027", "ahc039", "ahc046",
]


def _get_init_lock() -> asyncio.Lock:
    """Return the module-level init lock, creating it lazily (must be called from async context)."""
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


def _get_session_lock(problem_id: str) -> asyncio.Lock:
    if problem_id not in _session_locks:
        _session_locks[problem_id] = asyncio.Lock()
    return _session_locks[problem_id]


async def _get_or_create_session(problem_id: str) -> object:
    if problem_id in _sessions:
        return _sessions[problem_id]
    async with _get_init_lock():
        if problem_id not in _sessions:
            _sessions[problem_id] = await asyncio.to_thread(
                ale_bench.start,
                problem_id=problem_id,
                lite_version=True,
                num_workers=8,
                run_visualization_server=False,
            )
        if problem_id not in _session_locks:
            _session_locks[problem_id] = asyncio.Lock()
    return _sessions[problem_id]


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
    tasks = [_get_or_create_session(pid) for pid in KNOWN_PROBLEM_IDS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for pid, r in zip(KNOWN_PROBLEM_IDS, results):
        if isinstance(r, Exception):
            print(f"[startup] WARNING: failed to preload {pid}: {r}")
        else:
            print(f"[startup] preloaded session for {pid}")


@app.post("/problems/{problem_id}/public-eval", response_model=EvalResponse)
async def public_eval(problem_id: str, req: EvalRequest) -> EvalResponse:
    try:
        session = await _get_or_create_session(problem_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start ALE-Bench session: {exc}") from exc

    try:
        async with _get_session_lock(problem_id):
            result = await asyncio.to_thread(
                session.public_eval, req.code, code_language=req.language
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"public_eval failed: {exc}") from exc

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
