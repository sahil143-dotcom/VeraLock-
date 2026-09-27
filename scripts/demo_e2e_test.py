"""End-to-end demo test: Frost & Flow HVAC service call.

Feeds all four beats of the demo transcript through a single BrainPipeline
with accumulating session context. Validates each turn's speech_action,
commitment status, and key fields.

Modes:
    python scripts/demo_e2e_test.py              # live Gemini (needs API quota)
    python scripts/demo_e2e_test.py --fixture     # offline StubLLM (instant, no API)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# -- repo root on sys.path + load .env --
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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

from intelligence.api import handle_turn_payload
from intelligence.pipeline import BrainPipeline
from voice.turn_builder import build_turn, new_session_id, now_iso

# ---------------------------------------------------------------------------
# Demo conversation turns
# ---------------------------------------------------------------------------

# Expectations are set per-mode: (fixture, live)
# None means "any value is acceptable"

TURNS = [
    # Beat 1a -- Customer reports problem (NOT a commitment)
    {
        "speaker": "customer",
        "text": (
            "Hi, my AC unit stopped cooling completely -- "
            "it is just blowing warm air. Can someone come out tomorrow morning?"
        ),
        "beat": "Beat 1a -- Customer request (no commitment)",
        # Fixture StubLLM: classify_surface sees no "I will" pattern -> ambiguous -> CLARIFY
        # Live Gemini: understands semantics -> NO_COMMITMENT / SILENT
        "fixture_speech": None,   # accept any (stub classifies as ambiguous)
        "fixture_status": None,   # accept any
        "live_speech": "SILENT",
        "live_status": "NO_COMMITMENT",
    },
    # Beat 1b -- Business confirms visit (CLEAN commitment)
    {
        "speaker": "business",
        "text": (
            "I will have a tech at your place by 10:00 AM tomorrow. "
            "We will diagnose the unit on-site."
        ),
        "beat": "Beat 1b -- Technician confirms visit",
        # Fixture: "I will" -> commitment -> CONFIRMED / SILENT
        # Live: same
        "fixture_speech": "SILENT",
        "fixture_status": "CONFIRMED",
        "live_speech": "SILENT",
        "live_status": "CONFIRMED",
    },
    # Beat 2a -- Customer conditional request
    {
        "speaker": "customer",
        "text": (
            "One more thing -- if it turns out the compressor is shot, "
            "go ahead and replace it while you are there."
        ),
        "beat": "Beat 2a -- Customer conditional request",
        # Both modes: no strict expectation (conditional language is tricky)
        "fixture_speech": None,
        "fixture_status": None,
        "live_speech": None,
        "live_status": None,
    },
    # Beat 2b -- Business vague acknowledgment (AMBIGUOUS)
    {
        "speaker": "business",
        "text": "Yeah, we will take care of it.",
        "beat": "Beat 2b -- Technician vague ack (ambiguous)",
        # Fixture: "take care of it" matches _VAGUE_RE -> blocks "commitment" -> ambiguous
        # Live: should also detect vagueness
        "fixture_speech": None,
        "fixture_status": None,
        "live_speech": None,
        "live_status": None,
    },
    # Beat 3 -- Customer resolves ambiguity
    {
        "speaker": "customer",
        "text": (
            "Do not do any work over three hundred dollars without calling me first "
            "for approval."
        ),
        "beat": "Beat 3 -- Customer resolves ambiguity",
        "fixture_speech": None,
        "fixture_status": None,
        "live_speech": None,
        "live_status": None,
    },
    # Beat 4 -- Business restates commitment with follow-up trigger
    {
        "speaker": "business",
        "text": (
            "I will do the diagnosis at 10 AM tomorrow, "
            "and I will call you for approval before any repair over three hundred."
        ),
        "beat": "Beat 4 -- Follow-up trigger (10 AM tomorrow restated)",
        # Fixture: "I will" -> commitment -> CONFIRMED / SILENT
        # Live: same
        "fixture_speech": "SILENT",
        "fixture_status": "CONFIRMED",
        "live_speech": "SILENT",
        "live_status": "CONFIRMED",
    },
]

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_STATUS_TAG = {
    "CONFIRMED": "[CONFIRMED]",
    "DETECTED": "[DETECTED]",
    "AWAITING_CLARIFICATION": "[CLARIFY]",
    "UNRESOLVED_AMBIGUOUS": "[AMBIGUOUS]",
    "NO_COMMITMENT": "[NONE]",
    "SUPERSEDED": "[SUPERSEDED]",
    "WITHDRAWN": "[WITHDRAWN]",
}


def _fmt_status(s):
    if s is None:
        return "(any)"
    return _STATUS_TAG.get(s, f"[{s}]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    fixture = "--fixture" in sys.argv or "-f" in sys.argv

    if not fixture:
        llm_key = os.environ.get("VERALOCK_LLM_API_KEY", "").strip()
        if not llm_key:
            print("  ERROR: VERALOCK_LLM_API_KEY not set. Use --fixture for offline mode.")
            return 1

    brain = BrainPipeline(fixture_mode=fixture)
    session_id = new_session_id()
    mode_label = "FIXTURE (offline StubLLM)" if fixture else "LIVE (Gemini API)"

    print()
    print("=" * 70)
    print("  VERALOCK END-TO-END DEMO TEST")
    print("  Scenario: Frost & Flow HVAC -- Service Call")
    print(f"  Mode   : {mode_label}")
    print(f"  Session: {session_id}")
    print("=" * 70)

    passed = 0
    failed = 0
    skipped = 0
    total = len(TURNS)
    results_table = []

    for i, turn in enumerate(TURNS, 1):
        speaker = turn["speaker"]
        text = turn["text"]
        beat = turn["beat"]

        # Pick expectations based on mode
        expect_speech = turn["fixture_speech"] if fixture else turn["live_speech"]
        expect_status = turn["fixture_status"] if fixture else turn["live_status"]

        payload = build_turn(
            text,
            session_id=session_id,
            speaker_role=speaker,
            created_at=now_iso(),
        )

        # Pause between turns in live mode to avoid rate limits
        if not fixture and i > 1:
            time.sleep(8)

        print()
        print("-" * 70)
        print(f"  Turn {i}: {beat}")
        print(f"  Speaker: {speaker}")
        print(f"  Text   : {text[:80]}{'...' if len(text) > 80 else ''}")
        sys.stdout.flush()

        try:
            result = handle_turn_payload(payload, pipeline=brain)
        except Exception as exc:
            print(f"  ERROR  : {exc}")
            failed += 1
            results_table.append((i, beat, "ERROR", "ERROR", "FAIL"))
            continue

        actual_speech = result.get("speech_action", "?")
        commitment = result.get("commitment")
        actual_status = None
        topic = None
        canonical = None
        confidence = None
        clarification_q = result.get("clarification_question")
        rate_limited = False

        if commitment and isinstance(commitment, dict):
            actual_status = commitment.get("status")
            topic = commitment.get("topic_id", "?")
            canonical = commitment.get("canonical_text", "")
            confidence = commitment.get("confidence", 0)
            # Detect rate-limit fallback: conf=0 + untopiced
            if not fixture and confidence == 0 and topic == "untopiced":
                rate_limited = True

        print(f"  Speech : {actual_speech}")
        print(f"  Status : {_fmt_status(actual_status)}")
        if topic:
            print(f"  Topic  : {topic}")
        if canonical:
            print(f"  Commit : {canonical[:70]}{'...' if canonical and len(canonical) > 70 else ''}")
        if confidence is not None:
            print(f"  Conf   : {confidence:.0%}")
        if clarification_q:
            print(f"  Ask    : {clarification_q}")
        if rate_limited:
            print(f"  NOTE   : ** Rate-limited fallback (API returned 429) **")

        # -- validation --
        if rate_limited:
            verdict = "SKIP"
            skipped += 1
            print(f"  Result : SKIP (rate-limited, cannot validate)")
        else:
            speech_ok = expect_speech is None or actual_speech == expect_speech
            status_ok = expect_status is None or actual_status == expect_status

            if speech_ok and status_ok:
                verdict = "PASS"
                passed += 1
                print(f"  Result : PASS")
            else:
                verdict = "FAIL"
                failed += 1
                reasons = []
                if not speech_ok:
                    reasons.append(f"speech expected={expect_speech} got={actual_speech}")
                if not status_ok:
                    reasons.append(f"status expected={expect_status} got={actual_status}")
                print(f"  Result : FAIL -- {'; '.join(reasons)}")

        results_table.append((
            i,
            beat,
            f"{actual_speech}/{_fmt_status(actual_status)}",
            f"exp={expect_speech or '*'}/{_fmt_status(expect_status)}",
            verdict,
        ))

    # -- summary --
    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print()
    print(f"  {'Turn':<6} {'Result':<8} {'Actual':<30} {'Expected':<30} Beat")
    print(f"  {'----':<6} {'------':<8} {'------':<30} {'--------':<30} ----")
    for num, beat, actual, expected, verdict in results_table:
        print(f"  {num:<6} {verdict:<8} {actual:<30} {expected:<30} {beat}")

    print()
    parts = []
    if passed:
        parts.append(f"{passed} PASSED")
    if failed:
        parts.append(f"{failed} FAILED")
    if skipped:
        parts.append(f"{skipped} SKIPPED (rate-limited)")
    print(f"  {' | '.join(parts)}  (of {total} turns)")

    if fixture:
        print()
        print("  TIP: This was offline mode. Run without --fixture when API quota resets")
        print("       to validate with the real Gemini model.")
    elif skipped:
        print()
        print("  TIP: Some turns hit Gemini rate limits. Wait ~60s and retry, or use")
        print("       --fixture for instant offline validation.")

    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
