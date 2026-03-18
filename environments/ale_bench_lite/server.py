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

import os
from typing import Optional

import ale_bench
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="ALE-Bench Eval Server")

# Session cache: problem_id -> ale_bench session
_sessions: dict[str, object] = {}


def _get_session(problem_id: str) -> object:
    if problem_id not in _sessions:
        _sessions[problem_id] = ale_bench.start(
            problem_id=problem_id,
            lite_version=False,
            num_workers=1,
            run_visualization_server=False,
        )
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


@app.post("/problems/{problem_id}/public-eval", response_model=EvalResponse)
async def public_eval(problem_id: str, req: EvalRequest) -> EvalResponse:
    try:
        session = _get_session(problem_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start ALE-Bench session: {exc}") from exc

    try:
        result = session.public_eval(req.code, code_language=req.language)
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
