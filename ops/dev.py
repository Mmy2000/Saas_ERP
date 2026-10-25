"""Everything a developer needs running, in one terminal:

    python ops/dev.py                  # web + background worker + CSS watcher
    python ops/dev.py --migrate        # apply new migrations first
    python ops/dev.py --monitor        # also the server monitor (Console → Server)
    python ops/dev.py --whatsapp       # also the WhatsApp gateway (needs WA_GATEWAY_KEY in .env)
    python ops/dev.py --port 8001 --no-css

Run it with the project's virtualenv Python (.\\venv\\Scripts\\python.exe on Windows). Each
line of output is prefixed with the process it came from. Ctrl+C stops them all; if one of them
stops on its own (a port in use, a syntax error), the others are stopped too, so nothing is left
running half-way.

Development settings make the worker safe to run: e-mails are written to var/mail, one-time
codes and WhatsApp messages go to the log.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
WINDOWS = os.name == "nt"
COLOURS = {"web": "36", "worker": "33", "css": "35", "monitor": "32", "whatsapp": "34"}
print_lock = threading.Lock()


def say(name: str, line: str) -> None:
    with print_lock:
        print(f"\033[{COLOURS.get(name, '0')}m{name:>8} |\033[0m {line}", flush=True)


def processes(args) -> dict[str, list[str]]:
    wanted = {
        "web": [PY, "manage.py", "runserver", f"127.0.0.1:{args.port}"],
        "worker": [PY, "manage.py", "run_worker"],
    }
    npm = shutil.which("npm")
    if args.css and npm and (ROOT / "node_modules").is_dir():
        wanted["css"] = [npm, "run", "watch:css"]
    elif args.css:
        say("dev", "CSS watcher skipped: run `npm install` first (Node.js), or pass --no-css.")
    if args.monitor:
        wanted["monitor"] = [PY, "manage.py", "monitor_server"]
    if args.whatsapp:
        node = shutil.which("node")
        if node and (ROOT / "ops" / "wa-gateway" / "node_modules").is_dir():
            wanted["whatsapp"] = [node, "ops/wa-gateway/server.mjs"]
        else:
            say("dev", "WhatsApp gateway skipped: run `npm ci` in ops/wa-gateway first.")
    return wanted


def dotenv(path: Path) -> dict[str, str]:
    """KEY=value lines of .env. Django reads the file itself, but the Node WhatsApp gateway
    only sees its environment (WA_GATEWAY_KEY)."""
    values = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def start(name: str, command: list[str]) -> subprocess.Popen:
    # The shell's own variables win over .env, as they do for Django.
    env = {**dotenv(ROOT / ".env"), **os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "1"}
    # Each in its own process group, so stopping one stops what it started too (runserver's
    # reloader, npm's tailwind) and Ctrl+C reaches only this script.
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS
             else {"start_new_session": True})
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace", bufsize=1,
                               **group)

    def relay():
        for line in process.stdout:
            say(name, line.rstrip())

    threading.Thread(target=relay, daemon=True).start()
    return process


def stop(running: dict[str, subprocess.Popen]) -> None:
    for process in running.values():
        if process.poll() is not None:
            continue
        if WINDOWS:  # the whole tree: runserver's reloader child holds the port otherwise
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGINT)  # let them finish what they are doing
            except ProcessLookupError:
                continue
    deadline = time.monotonic() + 8
    for name, process in running.items():
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if not WINDOWS:
                os.killpg(process.pid, signal.SIGKILL)
        say(name, "stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the development servers together.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-css", dest="css", action="store_false",
                        help="don't run the Tailwind watcher")
    parser.add_argument("--monitor", action="store_true", help="also run monitor_server")
    parser.add_argument("--whatsapp", action="store_true", help="also run the WhatsApp gateway")
    parser.add_argument("--migrate", action="store_true",
                        help="apply migrations (as the owner role) before starting")
    args = parser.parse_args()
    if WINDOWS:
        os.system("")  # turn on colours in the Windows console
    # Output redirected to a file uses the system code page on Windows: a character it lacks
    # (Tailwind prints "≈", logs can hold Arabic) must not stop the relay of a process's output.
    sys.stdout.reconfigure(errors="replace")

    if args.migrate:
        say("dev", "applying migrations…")
        done = subprocess.run([PY, "manage.py", "migrate", "--database", "owner"], cwd=ROOT,
                              check=False)
        if done.returncode != 0:
            return done.returncode

    running = {name: start(name, command) for name, command in processes(args).items()}
    say("dev", f"console: http://admin.localhost:{args.port}/   a shop: "
               f"http://<slug>.localhost:{args.port}/   e-mails: var/mail   (Ctrl+C stops all)")
    try:
        while True:
            ended = {name: p.returncode for name, p in running.items() if p.poll() is not None}
            if ended:
                for name, code in ended.items():
                    say(name, f"exited with code {code}: stopping the others")
                return 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("dev", "stopping…")
        return 0
    finally:
        stop(running)


if __name__ == "__main__":
    sys.exit(main())
