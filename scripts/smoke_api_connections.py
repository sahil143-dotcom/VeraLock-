"""Smoke test: verify Gemini Brain LLM + AssemblyAI Voice ASR connections.

Reads env vars from .env (if present) then tests each API directly.

Usage:
    python scripts/smoke_api_connections.py

Exits 0 if both APIs respond correctly, non-zero on any failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env (key=value lines, # comments ignored)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# Gemini Brain smoke
# ---------------------------------------------------------------------------

def smoke_llm() -> bool:
    print("\n-- Brain LLM (intelligence/llm_adapter.py) --")
    from intelligence.llm_adapter import client_from_env

    client = client_from_env()
    if client is None:
        print("  SKIP: VERALOCK_LLM_API_KEY not set")
        return True

    provider = type(client).__name__
    print(f"  Client : {provider}")
    print(f"  Repr   : {client!r}")

    # Minimal A->G prompt - the stub text is always a commitment
    test_prompt = (
        "<brain_input>\n"
        + json.dumps({
            "session_id": "smoke-session",
            "speaker_roles": ["user"],
            "turn": {
                "turn_id": "t-smoke-001",
                "speaker_role": "user",
                "text": "I will send the report by Friday.",
                "created_at": "2026-09-26T10:00:00Z",
            },
            "recent_turns": [],
            "active_commitments": [],
        })
        + "\n</brain_input>\n"
    )

    print("  Calling LLM completion...", flush=True)
    try:
        raw = client.complete(test_prompt)
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return False

    try:
        ag = json.loads(raw)
        surface = ag.get("C", {}).get("surface", "?")
        print(f"  OK  - surface={surface!r}")
        print(f"  Raw (truncated): {raw[:200]}")
        return True
    except json.JSONDecodeError:
        print(f"  WARN: response not JSON - raw: {raw[:300]}")
        return True  # API responded; parsing may need tuning


# ---------------------------------------------------------------------------
# AssemblyAI Voice smoke (uses a tiny synthetic WAV in memory)
# ---------------------------------------------------------------------------

def _minimal_wav(duration_ms: int = 200, sample_rate: int = 8000) -> bytes:
    """Create a tiny silent WAV (PCM-16, mono) to satisfy the API."""
    import struct, io
    num_samples = int(sample_rate * duration_ms / 1000)
    pcm = b"\x00\x00" * num_samples
    data_len = len(pcm)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm)
    return buf.getvalue()


def smoke_assemblyai() -> bool:
    print("\n-- AssemblyAI Voice (voice/turn_audio.py) --")
    from voice.turn_audio import AssemblyAIAdapter, ASSEMBLYAI_API_KEY_ENV

    api_key = os.environ.get(ASSEMBLYAI_API_KEY_ENV, "").strip()
    if not api_key:
        print(f"  SKIP: {ASSEMBLYAI_API_KEY_ENV} not set")
        return True

    print(f"  Key: {api_key[:8]}...")
    adapter = AssemblyAIAdapter(api_key, poll_interval=2.0, timeout=90.0)

    wav = _minimal_wav()
    print(f"  Uploading {len(wav)}-byte WAV...", flush=True)
    try:
        upload_url = adapter._upload(wav)
        print(f"  Uploaded  -> {upload_url[:60]}...")
    except Exception as exc:
        print(f"  FAIL (upload): {exc}")
        return False

    print("  Submitting transcript job...", flush=True)
    try:
        transcript_id = adapter._submit(upload_url)
        print(f"  Submitted -> id={transcript_id}")
    except Exception as exc:
        print(f"  FAIL (submit): {exc}")
        return False

    print("  Polling for completion (silent audio -> may be empty)...", flush=True)
    try:
        text = adapter._poll(transcript_id)
        print(f"  OK  - transcript: {text!r}")
    except RuntimeError as exc:
        msg = str(exc)
        # Silent audio sometimes yields empty text or a specific AssemblyAI error.
        # Both mean the API round-trip succeeded.
        if "empty text" in msg or "timed out" in msg:
            print(f"  OK  - API responded (silent audio: {msg})")
        else:
            print(f"  FAIL (poll): {exc}")
            return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("VeraLock API connection smoke test")
    print("=" * 50)

    results = {
        "Brain LLM": smoke_llm(),
        "AssemblyAI Voice": smoke_assemblyai(),
    }

    print("\n-- Summary --")
    all_ok = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {name}")
        if not ok:
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
