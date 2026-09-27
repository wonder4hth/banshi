"""Desktop entry point for the packaged BanShi.exe.

Runs the Flask app on a private localhost port in a background thread and
shows it in a native window (pywebview -> Edge WebView2 on Windows).

Differences from `python app.py`:
- single-user mode: no login page, all data belongs to one local user
- data lives in %APPDATA%/BanShi (or ~/.banshi elsewhere), not next to the code
- closing the window quits the app
- if the installer bundled Ollama + a model next to the exe (ollama/, models/),
  a private Ollama server is started on a free port and stopped on exit, so
  the user never has to install or run Ollama themselves
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

# Must be set before importing app/db/llm_engine: they read these at import time.
# Portable package: if a `data` folder sits next to the exe, keep everything
# there so the whole folder can be moved/copied as one unit.
_EXE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
if (_EXE_DIR / "data").is_dir():
    DATA_DIR = _EXE_DIR / "data"
elif os.name == "nt":
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BanShi"
else:
    DATA_DIR = Path.home() / ".banshi"
os.environ.setdefault("BANSHI_DATA_DIR", str(DATA_DIR))
os.environ["BANSHI_SINGLE_USER"] = "1"

BUNDLED_OLLAMA = _EXE_DIR / "ollama" / ("ollama.exe" if os.name == "nt" else "ollama")
BUNDLED_MODELS = _EXE_DIR / "models"
# written by installer/build.ps1 so the exe and the bundled weights can't disagree
BUNDLED_MODEL_FILE = BUNDLED_MODELS / "bundled-model.txt"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """Put the child in a Windows job object that dies with this process, so a
    crash or a Task Manager kill can't leave ollama.exe holding gigabytes of RAM."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                   "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("Basic", BASIC_LIMIT), ("Io", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED_LIMIT()
    info.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
    handle = k32.OpenProcess(0x1F0FFF, False, proc.pid)  # PROCESS_ALL_ACCESS
    k32.AssignProcessToJobObject(job, handle)
    # the job handle is deliberately never closed: it closes when we exit
    _kill_with_parent.job = job


def start_bundled_ollama():
    """Start the installer's own Ollama, if present. Returns the process or None."""
    if not BUNDLED_OLLAMA.is_file():
        return None
    port = _free_port()
    model = BUNDLED_MODEL_FILE.read_text(encoding="utf-8-sig").strip() if BUNDLED_MODEL_FILE.is_file() else "qwen3:4b-instruct"
    env = dict(os.environ,
               OLLAMA_HOST=f"127.0.0.1:{port}",
               OLLAMA_MODELS=str(BUNDLED_MODELS),
               OLLAMA_KEEP_ALIVE="-1")  # the server only lives as long as the window anyway
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DATA_DIR / "ollama.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(BUNDLED_OLLAMA), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _kill_with_parent(proc)
    # what llm_engine will read on import
    os.environ["OLLAMA_HOST"] = f"http://127.0.0.1:{port}"
    os.environ["OLLAMA_MODEL"] = model
    return proc


_ollama_proc = start_bundled_ollama()

import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402

import webview  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import db  # noqa: E402
import llm_engine  # noqa: E402
import mailbox  # noqa: E402
from app import app  # noqa: E402

WINDOW_SIZE = (1024, 768)  # 4:3


def _wait_for_ollama(timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ollama_proc.poll() is not None:
            return False  # crashed on startup; the app falls back to preset lines
        if llm_engine.is_available(timeout=0.5):
            return True
        time.sleep(0.2)
    return False


def _preload_model():
    """Load the weights into memory right away, so the first chat message
    doesn't pay the cold-load wait. An empty prompt only loads the model."""
    try:
        req = urllib.request.Request(
            llm_engine.OLLAMA_HOST + "/api/generate",
            data=json.dumps({"model": llm_engine.MODEL, "prompt": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=300).read()
    except Exception:
        pass


def main():
    db.init_db()
    mailbox.start_scheduler()

    if _ollama_proc is not None and _wait_for_ollama():
        threading.Thread(target=_preload_model, daemon=True).start()

    # port 0 = let the OS pick a free port, so a busy 5000 can't break startup
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window(
        "伴时",
        f"http://127.0.0.1:{server.server_port}/",
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=(640, 480),
    )
    webview.start()  # blocks until the window is closed
    server.shutdown()
    if _ollama_proc is not None:
        _ollama_proc.terminate()
    sys.exit(0)


if __name__ == "__main__":
    main()
