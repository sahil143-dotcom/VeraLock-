"""VeraLock end-to-end live runner.

Audio or text → AssemblyAI ASR → Gemini Brain → SQLite Vault → printed report.

Usage (interactive text mode):
    python scripts/veralock_run.py

Usage (audio file mode):
    python scripts/veralock_run.py path/to/audio.wav

Usage (one-shot text):
    python scripts/veralock_run.py --text "I will send the report by Friday"

The script loads .env automatically. No extra pip installs beyond requirements.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── resolve repo root so imports work whether run as a module or directly ──
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── load .env ──────────────────────────────────────────────────────────────
def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(_ROOT / ".env")

# ── deferred imports (need env vars set first) ─────────────────────────────
from intelligence.api import handle_turn_payload
from intelligence.pipeline import BrainPipeline
from voice.turn_audio import turn_audio_adapter_from_env
from voice.turn_builder import build_turn, new_session_id, now_iso


# ── Vault (SQLite) setup ───────────────────────────────────────────────────
def _make_vault(db_path: Path):
    """Open (or create) the SQLite vault and return a persist sink."""
    import sqlite3

    from storage.db import connect, init_schema
    from storage.persist_sink import SqlitePersistSink

    conn = connect(str(db_path))
    init_schema(conn)
    return SqlitePersistSink(conn), conn


# ── Brain pipeline (Gemini, live mode) ────────────────────────────────────
def _make_brain(persist):
    return BrainPipeline(fixture_mode=False, persist=persist)


# ── Formatting helpers ─────────────────────────────────────────────────────
_STATUS_EMOJI = {
    "CONFIRMED": "[CONFIRMED]",
    "DETECTED": "[DETECTED]",
    "AWAITING_CLARIFICATION": "[CLARIFY]",
    "UNRESOLVED_AMBIGUOUS": "[AMBIGUOUS]",
    "NO_COMMITMENT": "[NONE]",
    "SUPERSEDED": "[SUPERSEDED]",
    "WITHDRAWN": "[WITHDRAWN]",
}


def _print_result(text: str, result: dict) -> None:
    print()
    print("=" * 60)
    print(f"  TEXT   : {text}")
    speech = result.get("speech_action", "?")
    print(f"  BRAIN  : {speech}", end="")

    commitment = result.get("commitment")
    if commitment and isinstance(commitment, dict):
        status = commitment.get("status", "?")
        tag = _STATUS_EMOJI.get(status, f"[{status}]")
        topic = commitment.get("topic_id", "?")
        canonical = commitment.get("canonical_text", "")
        confidence = commitment.get("confidence", 0)
        print(f"  |  {tag}")
        print(f"  TOPIC  : {topic}")
        print(f"  COMMIT : {canonical}")
        print(f"  CONF   : {confidence:.0%}")
    else:
        print("  |  (no commitment)")

    if result.get("clarification_question"):
        print(f"  ASK    : {result['clarification_question']}")

    skipped = result.get("skipped_by_filter")
    if skipped:
        print(f"  FILTER : {result.get('filter_reason', 'skipped')}")

    print("=" * 60)


# ── Transcription (AssemblyAI or text passthrough) ─────────────────────────
def _transcribe(source: str | Path | None) -> str:
    """Transcribe an audio file. Returns raw text."""
    asr = turn_audio_adapter_from_env()
    backend = type(asr).__name__
    print(f"  ASR backend : {backend}")
    text = asr.transcribe(source)
    return text.strip()


# ── Main loop ──────────────────────────────────────────────────────────────
def run_interactive(brain: BrainPipeline) -> None:
    session_id = new_session_id()
    print()
    print("  VeraLock is listening. Type a sentence and press Enter.")
    print("  Commands: 'quit' or Ctrl+C to exit, 'audio <path>' to transcribe a file.")
    print()

    while True:
        try:
            raw = input("  You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye!")
            break

        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q"):
            print("  Bye!")
            break

        # Audio file shortcut
        if raw.lower().startswith("audio "):
            path = Path(raw[6:].strip())
            if not path.is_file():
                print(f"  File not found: {path}")
                continue
            print(f"  Transcribing {path.name} via AssemblyAI...")
            try:
                text = _transcribe(path)
            except Exception as exc:
                print(f"  Transcription failed: {exc}")
                continue
            print(f"  Transcript: {text!r}")
        else:
            text = raw

        if not text:
            print("  (empty - skipped)")
            continue

        payload = build_turn(text, session_id=session_id, speaker_role="user", created_at=now_iso())
        try:
            result = handle_turn_payload(payload, pipeline=brain)
        except Exception as exc:
            print(f"  Brain error: {exc}")
            continue

        _print_result(text, result)


def run_audio_file(brain: BrainPipeline, audio_path: Path) -> int:
    print(f"  Transcribing {audio_path.name} via AssemblyAI...")
    try:
        text = _transcribe(audio_path)
    except Exception as exc:
        print(f"  Transcription failed: {exc}")
        return 1

    print(f"  Transcript: {text!r}")
    session_id = new_session_id()
    payload = build_turn(text, session_id=session_id, speaker_role="user", created_at=now_iso())
    result = handle_turn_payload(payload, pipeline=brain)
    _print_result(text, result)
    return 0


def run_text(brain: BrainPipeline, text: str) -> int:
    session_id = new_session_id()
    payload = build_turn(text, session_id=session_id, speaker_role="user", created_at=now_iso())
    result = handle_turn_payload(payload, pipeline=brain)
    _print_result(text, result)
    return 0


# ── Entry point ────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="VeraLock live runner: audio/text -> AssemblyAI -> Gemini Brain -> Vault"
    )
    parser.add_argument(
        "audio",
        nargs="?",
        help="Path to an audio file to transcribe (WAV, MP3, M4A, ...)",
    )
    parser.add_argument(
        "--text",
        "-t",
        help="One-shot text turn (skips ASR)",
    )
    parser.add_argument(
        "--db",
        default="vault.db",
        help="SQLite database path (default: vault.db in repo root)",
    )
    parser.add_argument(
        "--no-vault",
        action="store_true",
        help="Skip SQLite persistence (useful for quick tests)",
    )
    args = parser.parse_args()

    db_path = _ROOT / args.db

    print()
    print("  +-----------------------------------+")
    print("  |   VeraLock - Commitment Detector  |")
    print("  +-----------------------------------+")

    # Show active configuration
    provider = os.environ.get("VERALOCK_LLM_PROVIDER", "openai").upper()
    model = os.environ.get("VERALOCK_LLM_MODEL", "gpt-4o-mini")
    asr_backend = os.environ.get("VERALOCK_VOICE_ASR", "stub").upper()
    print(f"  LLM    : {provider}  ({model})")
    print(f"  ASR    : {asr_backend}")
    if not args.no_vault:
        print(f"  Vault  : {db_path}")

    # Check required keys
    llm_key = os.environ.get("VERALOCK_LLM_API_KEY", "").strip()
    if not llm_key:
        print()
        print("  ERROR: VERALOCK_LLM_API_KEY not set. Check your .env file.")
        return 1

    # Wire up vault
    persist = None
    conn = None
    if not args.no_vault:
        try:
            persist, conn = _make_vault(db_path)
            print("  Vault  : ready")
        except Exception as exc:
            print(f"  Vault init failed: {exc} (continuing without persistence)")
            persist = None

    # Build brain
    try:
        brain = _make_brain(persist)
        print("  Brain  : ready (live mode — Gemini)")
    except Exception as exc:
        print(f"  Brain init failed: {exc}")
        return 1

    # Route to the right mode
    if args.text:
        return run_text(brain, args.text)
    elif args.audio:
        return run_audio_file(brain, Path(args.audio))
    else:
        run_interactive(brain)
        return 0


if __name__ == "__main__":
    sys.exit(main())
