#!/usr/bin/env python3
"""ALE-Bench eval CLI client — stdlib only, no ale_bench or requests required.

Sends the submission code to the host-side server and prints the result JSON.

Usage:
    ale-bench-eval --problem-id ahc008 --code-path solution.cpp [--language cpp20]
    ale-bench-eval --problem-id ahc008 --code-path - < solution.cpp

Environment:
    ALE_BENCH_SERVER_URL  (default: http://host.docker.internal:8765)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ALLOWED_LANGUAGES = {"cpp17", "cpp20", "cpp23", "python", "rust"}
DEFAULT_SERVER_URL = "http://host.docker.internal:8765"


def _infer_language(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".py":
        return "python"
    if ext in {".cpp", ".cc", ".cxx"}:
        return "cpp23"
    if ext == ".rs":
        return "rust"
    raise ValueError(
        f"Cannot infer language from extension '{ext}'. "
        "Use --language or name your file with one of: .py / .cpp / .cc / .cxx / .rs"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a submission with ALE-Bench public seeds via the host eval server."
    )
    parser.add_argument("--problem-id", required=True, help="ALE-Bench problem id (e.g. ahc008)")
    parser.add_argument(
        "--code-path",
        required=True,
        help="Path to submission source code, or '-' to read from stdin",
    )
    parser.add_argument(
        "--language",
        choices=sorted(ALLOWED_LANGUAGES),
        default=None,
        help="Submission language. If omitted, inferred from --code-path extension.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write the JSON result",
    )
    return parser.parse_args()


def _read_code(code_path: str) -> tuple[str, Path | None]:
    """Returns (code_text, resolved_path_or_None)."""
    if code_path == "-":
        return sys.stdin.read(), None
    p = Path(code_path)
    return p.read_text(), p


def main() -> None:
    args = _parse_args()

    code, resolved_path = _read_code(args.code_path)

    if args.language:
        language = args.language
    elif resolved_path is not None:
        language = _infer_language(resolved_path)
    else:
        print(
            "error: --language is required when reading code from stdin (--code-path -)",
            file=sys.stderr,
        )
        raise SystemExit(1)

    server_url = os.environ.get("ALE_BENCH_SERVER_URL", DEFAULT_SERVER_URL).rstrip("/")
    url = f"{server_url}/problems/{args.problem_id}/public-eval"

    payload = json.dumps({"code": code, "language": language}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode()
        print(f"error: server returned HTTP {exc.code}: {error_body}", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(
            f"error: could not reach eval server at {url!r}: {exc.reason}\n"
            f"  Is the server running? Set ALE_BENCH_SERVER_URL if needed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    result = json.loads(body)
    # Ensure code_path is included for parity with public_eval_runner.py output schema
    result.setdefault("code_path", args.code_path)

    output = json.dumps(result, indent=2, sort_keys=True)
    print(output)

    if args.output_json is not None:
        Path(args.output_json).write_text(output + "\n")


if __name__ == "__main__":
    main()
