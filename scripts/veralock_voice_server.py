"""Runner script for the VeraLock V3 AssemblyAI Voice Agent Server.

Usage:
    python scripts/veralock_voice_server.py [--host 127.0.0.1] [--port 8000] [--fixture]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on sys.path + load .env
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(_ROOT / ".env")

import uvicorn
from intelligence.pipeline import BrainPipeline
from voice.agent_server import create_voice_app


def main():
    parser = argparse.ArgumentParser(description="VeraLock V3 AssemblyAI Voice Agent Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Run Brain pipeline in fixture mode (StubLLM)",
    )
    args = parser.parse_args()

    pipeline = BrainPipeline(fixture_mode=args.fixture)
    app = create_voice_app(pipeline=pipeline)

    mode_str = "FIXTURE (StubLLM)" if args.fixture else "LIVE (Gemini LLM)"
    print("=" * 68)
    print("  VERALOCK V3 ASSEMBLYAI VOICE AGENT SERVER")
    print(f"  Mode   : {mode_str}")
    print(f"  URL    : http://{args.host}:{args.port}")
    print(f"  WS     : ws://{args.host}:{args.port}/ws/voice")
    print("=" * 68)

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
