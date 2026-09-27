"""AssemblyAI Voice Agent integration layer for VeraLock V3.

Connects:
  Browser Microphone
  → AssemblyAI WebSocket (Real-time / Voice Agent API)
  → session.ready event
  → Final Transcript event
  → existing VeraLock /v1/turn pipeline (handle_turn_payload)
  → TurnResult (SILENT or CLARIFY)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# Repo root import resolution
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from intelligence.api import handle_turn_payload
from intelligence.pipeline import BrainPipeline
from voice.turn_builder import build_turn, new_session_id

ASSEMBLYAI_WS_URL = "wss://streaming.assemblyai.com/v3/ws?sample_rate=16000"


class VoiceAgentBridge:
    """Bridges browser microphone WebSockets with AssemblyAI API and VeraLock pipeline."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        pipeline: Optional[BrainPipeline] = None,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.client_ws = websocket
        self.pipeline = pipeline or BrainPipeline(fixture_mode=False)
        self.session_id = session_id or new_session_id()
        self.api_key = (api_key or os.environ.get("ASSEMBLYAI_API_KEY", "")).strip()
        self.aai_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False

    async def start(self) -> None:
        """Start the voice agent session."""
        self.running = True
        await self.client_ws.accept()

        if self.api_key:
            try:
                headers = {"Authorization": self.api_key}
                try:
                    self.aai_ws = await websockets.connect(
                        ASSEMBLYAI_WS_URL,
                        additional_headers=headers,
                    )
                except TypeError:
                    self.aai_ws = await websockets.connect(
                        ASSEMBLYAI_WS_URL,
                        extra_headers=headers,
                    )
            except Exception as exc:
                print(f"[VoiceAgent] AssemblyAI connection failed: {exc}")
                await self.client_ws.send_json(
                    {"type": "error", "message": f"AssemblyAI connection failed: {exc}"}
                )
                return

            # Start listening to AssemblyAI events in background
            asyncio.create_task(self._receive_from_assemblyai())
        else:
            # Stub/Mock Mode when no key is set
            print("[VoiceAgent] Running in Stub/Mock ASR mode (no AssemblyAI key)")
            await self.client_ws.send_json(
                {"type": "session.ready", "session_id": self.session_id, "mode": "stub"}
            )

        # Listen to client microphone stream
        await self._receive_from_client()

    async def _receive_from_assemblyai(self) -> None:
        """Listen for AssemblyAI events: session.ready / Begin / FinalTranscript."""
        if not self.aai_ws:
            return
        try:
            async for raw in self.aai_ws:
                if not self.running:
                    break
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                msg_type = msg.get("message_type") or msg.get("type") or "Unknown"
                text = (msg.get("text") or msg.get("transcript") or "").strip()
                print(f"[AssemblyAI Event] type={msg_type} text='{text}'")

                # 1. session.ready / Begin / SessionBegins
                if msg_type in ("Begin", "SessionBegins", "session.ready"):
                    print(f"[VoiceAgent] session.ready received from AssemblyAI (session={self.session_id})")
                    await self.client_ws.send_json(
                        {"type": "session.ready", "session_id": self.session_id}
                    )

                # 2. Final User Transcript
                elif text and (msg_type in ("FinalTranscript", "transcript.user", "Turn", "Transcript") or msg.get("is_final")):
                    print(f"\n[VoiceAgent] FINAL TRANSCRIPT: '{text}'")
                    await self.client_ws.send_json({"type": "final_transcript", "text": text, "speaker_role": "customer"})
                    await self._process_veralock_turn(text, speaker_role="customer")

        except websockets.ConnectionClosed:
            print("[VoiceAgent] AssemblyAI WebSocket closed")
        except Exception as exc:
            print(f"[VoiceAgent] Error in AssemblyAI listener: {exc}")

    async def _receive_from_client(self) -> None:
        """Listen for incoming browser WebSocket messages (audio_chunk)."""
        chunk_count = 0
        try:
            while self.running:
                data = await self.client_ws.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "audio_chunk":
                    pcm_b64 = msg.get("pcm")
                    if pcm_b64:
                        raw_pcm = base64.b64decode(pcm_b64)
                        chunk_count += 1
                        if chunk_count % 30 == 0:
                            print(f"[VoiceAgent Mic] Received audio chunk #{chunk_count} ({len(raw_pcm)} bytes)")

                        if self.aai_ws:
                            # Forward raw PCM binary audio chunk to AssemblyAI v3
                            await self.aai_ws.send(raw_pcm)
                        else:
                            # Stub/Fixture Mode - process mic audio after 30 chunks (~3s)
                            if chunk_count % 30 == 0:
                                stub_text = "I will have a tech at your place by 10:00 AM tomorrow."
                                print(f"\n[VoiceAgent StubMic] Mic audio received -> emitting turn: '{stub_text}'")
                                await self.client_ws.send_json({"type": "final_transcript", "text": stub_text, "speaker_role": "customer"})
                                await self._process_veralock_turn(stub_text, speaker_role="customer")

                elif msg_type == "test_transcript":
                    # Direct test transcript trigger for manual testing
                    text = (msg.get("text") or "").strip()
                    speaker_role = (msg.get("speaker_role") or "customer").strip()
                    if text:
                        await self.client_ws.send_json({"type": "final_transcript", "text": text, "speaker_role": speaker_role})
                        await self._process_veralock_turn(text, speaker_role=speaker_role)

        except WebSocketDisconnect:
            print(f"[VoiceAgent] Client disconnected (session={self.session_id})")
        finally:
            await self.close()

    async def _process_veralock_turn(self, text: str, speaker_role: str = "customer") -> None:
        """Pass final user transcript into existing VeraLock pipeline."""
        turn_payload = build_turn(
            text,
            session_id=self.session_id,
            speaker_role=speaker_role,
        )
        print(f"[VeraLock Pipeline] Processing turn ({speaker_role}): '{text}'...")
        turn_result = handle_turn_payload(turn_payload, pipeline=self.pipeline)
        
        action = turn_result.get("speech_action")
        commitment = turn_result.get("commitment", {})
        status = commitment.get("status")
        conf = commitment.get("confidence", 0)
        
        print(f"[VeraLock Result] SpeechAction: {action} | Status: {status} | Conf: {conf * 100:.0f}%")
        if action == "CLARIFY":
            print(f"[VeraLock Clarify Prompt] {turn_result.get('spoken_clarification')}")

        await self.client_ws.send_json(
            {
                "type": "turn_result",
                "text": text,
                "speaker_role": speaker_role,
                "result": turn_result,
            }
        )

    async def close(self) -> None:
        """Clean up WebSocket connections."""
        self.running = False
        if self.aai_ws:
            await self.aai_ws.close()
            self.aai_ws = None


def create_voice_app(pipeline: Optional[BrainPipeline] = None) -> FastAPI:
    """Build FastAPI application with voice WebSocket integration & static frontend."""
    app = FastAPI(title="VeraLock V3 Voice Agent", version="0.3.0")
    app.state.pipeline = pipeline or BrainPipeline(fixture_mode=False)

    @app.get("/", response_class=HTMLResponse)
    def index():
        frontend_path = _ROOT / "frontend" / "index.html"
        if not frontend_path.is_file():
            return HTMLResponse("<h2>frontend/index.html not found</h2>", status_code=404)
        return HTMLResponse(frontend_path.read_text(encoding="utf-8"))

    @app.get("/health")
    def health():
        return {"status": "ok", "component": "voice_agent"}

    @app.post("/v1/turn")
    def turn(body: dict[str, Any]):
        return handle_turn_payload(body, pipeline=app.state.pipeline)

    @app.websocket("/ws/voice")
    async def voice_websocket(websocket: WebSocket):
        bridge = VoiceAgentBridge(websocket, pipeline=app.state.pipeline)
        await bridge.start()

    return app

