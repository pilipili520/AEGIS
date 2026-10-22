"""Stream worker output and report long phases without changing evaluation rules."""
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Event, Thread
import time


@contextmanager
def progress_phase(name, emit, interval_s=30):
    started = time.monotonic()
    stopped = Event()
    emit(f"[PHASE_BEGIN] {name}")

    def heartbeat():
        while not stopped.wait(interval_s):
            emit(f"[PHASE_RUNNING] {name}; elapsed={time.monotonic() - started:.0f}s")

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    status = "interrupted"
    try:
        yield
        status = "completed"
    finally:
        stopped.set()
        thread.join(timeout=1)
        emit(f"[PHASE_END] {name}; status={status}; elapsed={time.monotonic() - started:.1f}s")


def run_capture_live(cmd, *, cwd, timeout_s, log_path, emit, heartbeat_s=30):
    """Keep the existing deadline and CompletedProcess contract, with live logs."""
    started = time.monotonic()
    deadline = started + timeout_s
    next_heartbeat = started + heartbeat_s
    chunks = []
    queue = Queue()
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    # Open the small audit log before spawning so failed log creation cannot
    # leave an unowned worker running in the background.
    with Path(log_path).open("w", encoding="utf-8", buffering=1) as log:
        process = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)

        def read_output():
            try:
                for line in process.stdout:
                    queue.put(line)
            finally:
                queue.put(None)

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        ended = False
        try:
            while not (ended and process.poll() is not None):
                now = time.monotonic()
                if now >= deadline:
                    raise subprocess.TimeoutExpired(cmd, timeout_s, output="".join(chunks))
                try:
                    line = queue.get(timeout=min(.2, max(.001, deadline - now)))
                    if line is None:
                        ended = True
                    else:
                        chunks.append(line)
                        log.write(line)
                        log.flush()
                        emit("[CAPTURE] " + line.rstrip("\r\n"))
                except Empty:
                    pass
                now = time.monotonic()
                if now >= next_heartbeat:
                    emit(f"[CAPTURE_RUNNING] pid={process.pid}; elapsed={now - started:.0f}s; log={log_path}")
                    next_heartbeat = now + heartbeat_s
            return subprocess.CompletedProcess(cmd, process.returncode, "".join(chunks))
        finally:
            # Interruptions only affect the worker owned by this call.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            reader.join(timeout=1)
            if not reader.is_alive():
                process.stdout.close()
