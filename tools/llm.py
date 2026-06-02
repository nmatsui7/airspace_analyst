"""
tools/llm.py — llama.cpp OpenAI-compatible client.

Auto-starts llama-server if not already running — no manual step needed.
Configure via .env file or environment variables.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent


def _load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()

LLAMA_BIN   = os.environ.get("LLAMA_BIN", "/usr/local/bin/llama-server-mcp")
MODEL_PATH  = os.environ.get("MODEL_PATH",
    str(Path.home() / ".lmstudio/models/lmstudio-community/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q8_0.gguf"))
CHAT_TEMPLATE = os.environ.get("CHAT_TEMPLATE",
    str(ROOT / "gemma4_official.jinja"))
PORT        = int(os.environ.get("LLAMA_PORT", "8082"))
LLAMA_URL   = f"http://localhost:{PORT}/v1/chat/completions"
CTX_SIZE    = int(os.environ.get("LLAMA_CTX_SIZE", "8192"))
THREADS     = int(os.environ.get("LLAMA_THREADS", "6"))
MODEL       = os.environ.get("LLAMA_MODEL", "gemma-4-E4B-it")
TIMEOUT     = int(os.environ.get("LLAMA_TIMEOUT", "180"))

_server_proc: subprocess.Popen | None = None


def _is_server_running() -> bool:
    try:
        r = httpx.get(f"http://localhost:{PORT}", timeout=2)
        return True
    except Exception:
        return False


def _start_server():
    global _server_proc

    if _is_server_running():
        return

    bin_path = Path(LLAMA_BIN)
    if not bin_path.exists():
        raise RuntimeError(
            f"llama-server not found at {LLAMA_BIN}. "
            "Install llama.cpp or set LLAMA_BIN in .env"
        )

    model_path = Path(MODEL_PATH)
    if not model_path.exists():
        raise RuntimeError(
            f"Model not found at {MODEL_PATH}. "
            "Download a Gemma 4 4B GGUF and set MODEL_PATH in .env"
        )

    template_path = Path(CHAT_TEMPLATE)
    template_args = ["--chat-template-file", str(template_path)] if template_path.exists() else []

    print(f"[llm] Starting llama-server (port {PORT})...", file=sys.stderr)

    _server_proc = subprocess.Popen(
        [
            str(bin_path),
            "--model", str(model_path),
            "--port", str(PORT),
            "--host", "127.0.0.1",
            "-c", str(CTX_SIZE),
            "--threads", str(THREADS),
            "--no-webui",
            *template_args,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to become ready
    deadline = time.time() + 120
    while time.time() < deadline:
        if _is_server_running():
            print(f"[llm] Server ready on port {PORT}.", file=sys.stderr)
            return
        time.sleep(2)

    raise RuntimeError(
        f"llama-server failed to start within 120s on port {PORT}. "
        f"Check {model_path} exists and is a valid GGUF."
    )


def _ensure_server():
    if not _is_server_running():
        _start_server()


def _stop_server():
    global _server_proc
    if _server_proc is not None:
        _server_proc.terminate()
        _server_proc.wait(timeout=10)
        _server_proc = None


def call_llm(system: str, user: str, temperature: float = 0.1) -> str:
    _ensure_server()
    try:
        r = httpx.post(
            LLAMA_URL,
            json={
                "model": MODEL,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach llama-server at {LLAMA_URL}. "
            "Check MODEL_PATH in .env points to a valid GGUF file."
        )


def call_llm_json(system: str, user: str) -> dict | list:
    """Call LLM and parse response as JSON. Strips markdown fences if present."""
    raw = call_llm(system, user, temperature=0.0)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[1:end])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"[llm] JSON parse failed. Raw:\n{raw[:400]}")
        return {}


# Clean up server on normal exit
atexit = None
try:
    import atexit
    atexit.register(_stop_server)
except ImportError:
    pass
