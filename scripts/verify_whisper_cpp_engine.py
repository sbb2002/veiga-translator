"""Self-check for backend/stt/whisper_cpp_engine.py against a fake whisper-server.

Starts stdlib http.server instances that imitate POST /inference, then checks:
  (a) the uploaded WAV is 16 kHz mono PCM16 with the expected frame count
  (b) fast/final use their own URL and beam_size (and prompt only on final)
  (c) verbose_json parsing: with segments, without segments, and confidence filtering
  (d) connection failure raises RuntimeError naming the URL

No GPU, no model files, no torch needed. Run from anywhere:
    python scripts/verify_whisper_cpp_engine.py
Prints PASS/FAIL per check and exits non-zero on any failure.
"""

from __future__ import annotations

import io
import json
import socket
import sys
import threading
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend import config  # noqa: E402

# Keep the embedding-similarity gate (which would import sentence-transformers/torch)
# out of this check. The engine reads this flag at construction time.
config.HALLUCINATION_GATE_ENABLED = False

from backend.stt import whisper_cpp_engine  # noqa: E402
from backend.stt.whisper_cpp_engine import WhisperCppEngine  # noqa: E402

results: list[tuple[str, bool, str]] = []


# Windows consoles default to cp949/cp1252; Japanese test strings must not crash printing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class FakeServer:
    """One fake whisper-server. `reply` is the JSON dict returned for /inference."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.reply: dict = {"text": ""}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                msg = BytesParser(policy=HTTP).parsebytes(
                    b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
                )
                fields: dict[str, str] = {}
                files: dict[str, bytes] = {}
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    payload = part.get_payload(decode=True)
                    if part.get_filename() is not None:
                        files[name] = payload
                    else:
                        fields[name] = payload.decode("utf-8")
                outer.requests.append({"path": self.path, "fields": fields, "files": files})
                out = json.dumps(outer.reply).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args) -> None:  # silence
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def main() -> int:
    fast_srv = FakeServer()
    final_srv = FakeServer()
    try:
        audio = (0.3 * np.sin(2 * np.pi * 220 * np.arange(8000) / 16000)).astype(np.float32)  # 0.5 s

        # --- (a)+(b): split routing, wav format, beam/prompt fields ---
        engine = WhisperCppEngine(fast_url=fast_srv.url, final_url=final_srv.url, timeout_s=5.0)
        fast_srv.reply = {"text": "こんにちは"}
        final_srv.reply = {"text": "こんにちは、世界"}
        r_fast = engine.transcribe(audio, fast=True, previous_context="前の文")
        r_final = engine.transcribe(audio, fast=False, previous_context="前の文")

        check("fast request hits FAST server only",
              len(fast_srv.requests) == 1 and len(final_srv.requests) == 1
              and fast_srv.requests[0]["path"] == "/inference"
              and final_srv.requests[0]["path"] == "/inference",
              f"fast={len(fast_srv.requests)} final={len(final_srv.requests)}")
        check("fast result text", r_fast.text == "こんにちは", repr(r_fast.text))
        check("final result text", r_final.text == "こんにちは、世界", repr(r_final.text))

        f_fast = fast_srv.requests[0]["fields"]
        f_final = final_srv.requests[0]["fields"]
        check("fast beam_size=config.WHISPER_FAST_BEAM_SIZE",
              f_fast.get("beam_size") == str(config.WHISPER_FAST_BEAM_SIZE), str(f_fast.get("beam_size")))
        check("final beam_size=config.WHISPER_FINAL_BEAM_SIZE",
              f_final.get("beam_size") == str(config.WHISPER_FINAL_BEAM_SIZE), str(f_final.get("beam_size")))
        check("language field sent", f_final.get("language") == config.WHISPER_LANGUAGE)
        check("prompt only on final pass",
              "prompt" not in f_fast and f_final.get("prompt") == "前の文",
              f"fast_keys_has_prompt={'prompt' in f_fast} final_prompt={f_final.get('prompt')!r}")
        check("documented fields temperature/temperature_inc/response_format present",
              all(k in f_final for k in ("temperature", "temperature_inc", "response_format")))

        wav_bytes = fast_srv.requests[0]["files"].get("file", b"")
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            ok = (wf.getnchannels() == 1 and wf.getsampwidth() == 2
                  and wf.getframerate() == 16000 and wf.getnframes() == 8000)
            detail = (f"ch={wf.getnchannels()} width={wf.getsampwidth()} "
                      f"rate={wf.getframerate()} frames={wf.getnframes()}")
        check("(a) WAV is 16kHz mono PCM16 with correct frame count", ok, detail)

        # Empty final URL => reuse the fast server, and final beam still applies.
        shared = WhisperCppEngine(fast_url=fast_srv.url, final_url="", timeout_s=5.0)
        before = len(fast_srv.requests)
        shared.transcribe(audio, fast=False)
        new_req = fast_srv.requests[before:]
        check("empty WHISPER_CPP_FINAL_URL reuses fast URL",
              len(new_req) == 1 and new_req[0]["fields"].get("beam_size") == str(config.WHISPER_FINAL_BEAM_SIZE))

        # --- (c): verbose_json parsing ---
        # Segments with confidence: one genuine, one hard-dropped (no_speech 0.9).
        fast_srv.reply = {
            "text": "ignored when segments present",
            "language": "ja",
            "segments": [
                {"text": " ほんとうに ", "no_speech_prob": 0.1, "avg_logprob": -0.2},
                {"text": " 無音 ", "no_speech_prob": 0.9, "avg_logprob": -0.3},
            ],
        }
        r = engine.transcribe(audio, fast=True)
        check("(c1) verbose segments: hard-dropped segment removed",
              r.text == "ほんとうに" and r.dropped_low_confidence is True, repr(r.text))
        check("(c1) kept-segment confidence averaged",
              r.no_speech_prob is not None and abs(r.no_speech_prob - 0.1) < 1e-9
              and r.avg_logprob is not None and abs(r.avg_logprob + 0.2) < 1e-9,
              f"nsp={r.no_speech_prob} alp={r.avg_logprob}")

        # Soft tier: no_speech 0.7 (>= THRESH 0.6) with avg_logprob -1.5 (<= -1.0) => drop.
        fast_srv.reply = {"segments": [{"text": "あ", "no_speech_prob": 0.7, "avg_logprob": -1.5}]}
        r = engine.transcribe(audio, fast=True)
        check("(c1) soft-tier drop when both signals agree", r.text == "" and r.dropped_low_confidence)

        # Segments WITHOUT confidence fields: nothing to filter on, text kept.
        fast_srv.reply = {"segments": [{"text": "テスト"}, {"text": "です"}], "language": "ja"}
        r = engine.transcribe(audio, fast=True)
        check("(c2) segments without confidence fields kept, no confidence reported",
              r.text == "テストです" and r.no_speech_prob is None and r.avg_logprob is None
              and not r.dropped_low_confidence, repr(r.text))

        # No segments at all (plain json-style response).
        fast_srv.reply = {"text": "  テキストだけ  "}
        r = engine.transcribe(audio, fast=True)
        check("(c3) no-segments response uses top-level text",
              r.text == "テキストだけ" and r.no_speech_prob is None, repr(r.text))

        # --- warmup ---
        fast_srv.reply = {"text": ""}
        before = len(fast_srv.requests)
        engine.warmup()
        check("warmup sends one fast 1s-silence request",
              len(fast_srv.requests) == before + 1
              and fast_srv.requests[-1]["fields"].get("beam_size") == str(config.WHISPER_FAST_BEAM_SIZE))

        # --- (d): connection failure ---
        dead_url = closed_port_url()
        dead = WhisperCppEngine(fast_url=dead_url, final_url="", timeout_s=2.0)
        try:
            dead.transcribe(audio, fast=True)
            check("(d) connection failure raises", False, "no exception")
        except RuntimeError as exc:
            check("(d) connection failure raises RuntimeError naming URL",
                  dead_url in str(exc) and "unreachable" in str(exc), str(exc)[:120])

        # encoder sanity: clipping and dtype
        wav = whisper_cpp_engine._encode_wav(np.array([2.0, -2.0, 0.0], dtype=np.float32))
        with wave.open(io.BytesIO(wav), "rb") as wf:
            frames = np.frombuffer(wf.readframes(3), dtype="<i2")
        check("encoder clips to [-1,1] PCM16", frames.tolist() == [32767, -32767, 0], str(frames.tolist()))
    finally:
        fast_srv.close()
        final_srv.close()

    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", "; ".join(failed))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
