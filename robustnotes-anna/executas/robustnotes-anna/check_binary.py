"""Smoke-check a packaged RobustNotes Executa with multiple JSON-RPC calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python check_binary.py <executa-binary>", file=sys.stderr)
        return 2

    binary = Path(sys.argv[1]).resolve()
    requests = [
        {"jsonrpc": "2.0", "id": "describe-check", "method": "describe"},
        {
            "jsonrpc": "2.0",
            "id": "invoke-check",
            "method": "invoke",
            "params": {
                "tool": "analyze_note",
                "arguments": {"text": "# Lecture\nRobustNotes helps students."},
            },
        },
    ]
    with tempfile.TemporaryDirectory(prefix="robustnotes-executa-", dir=binary.parent) as temp_dir:
        environment = os.environ.copy()
        environment.update({"TEMP": temp_dir, "TMP": temp_dir, "TMPDIR": temp_dir})
        result = subprocess.run(
            [str(binary)],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=environment,
        )
    if result.returncode != 0:
        print(f"Executable exited {result.returncode}: {result.stderr}", file=sys.stderr)
        return 1

    try:
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        describe, invoke = responses
        manifest = describe["result"]
        data = invoke["result"]["data"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        print(f"Invalid JSON-RPC output: {error}\n{result.stdout}", file=sys.stderr)
        return 1

    if len(responses) != 2 or describe.get("id") != "describe-check" or invoke.get("id") != "invoke-check":
        print("Executable did not preserve request IDs or process both requests.", file=sys.stderr)
        return 1
    if manifest.get("runtime", {}).get("type") != "binary":
        print("describe did not declare the binary runtime.", file=sys.stderr)
        return 1
    if data.get("words") != 4 or data.get("reading_minutes") != 1 or data.get("headings") != ["Lecture"]:
        print(f"Unexpected analyze_note response: {data!r}", file=sys.stderr)
        return 1
    print(f"OK: {binary.name} answered describe and analyze_note over JSON-RPC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
