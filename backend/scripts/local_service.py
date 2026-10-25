import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv


def configure_environment(project_root: Path) -> None:
    load_dotenv(project_root / ".env")
    load_dotenv(project_root / ".env.runtime", override=True)
    backend_dir = project_root / "backend"
    os.chdir(backend_dir)
    sys.path.insert(0, str(backend_dir))


def spawn_detached(specification_path: Path) -> int:
    specification = json.loads(specification_path.read_text(encoding="utf-8-sig"))
    required = ("executable", "arguments", "directory", "stdout", "stderr", "pid_file")
    if not isinstance(specification, dict) or any(
        not isinstance(specification.get(field), str) or not specification[field]
        for field in required
    ):
        raise ValueError("Invalid local process specification")
    command = subprocess.list2cmdline([specification["executable"]]) + " " + specification["arguments"]
    with (
        open(specification["stdout"], "ab") as stdout,
        open(specification["stderr"], "ab") as stderr,
    ):
        child = subprocess.Popen(
            command, cwd=specification["directory"], stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    Path(specification["pid_file"]).write_text(str(child.pid), encoding="ascii")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a configured local project service")
    parser.add_argument("service", choices=("backend", "bridge", "redis-check", "spawn"))
    parser.add_argument("specification", nargs="?")
    arguments = parser.parse_args()
    if arguments.service == "spawn":
        if not arguments.specification or sys.platform != "win32":
            parser.error("spawn requires a process specification on Windows")
        try:
            return spawn_detached(Path(arguments.specification))
        except (OSError, ValueError, TypeError) as exc:
            print(f"Local process spawn failed ({type(exc).__name__})", file=sys.stderr)
            return 1
    configure_environment(Path(__file__).resolve().parents[2])
    if arguments.service == "backend":
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=8002)
    elif arguments.service == "bridge":
        from app.connectors.sina.main import main as bridge_main

        bridge_main()
    else:
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        if os.environ.get("REDIS_HOST") != "127.0.0.1" or os.environ.get("REDIS_PORT") != "6380":
            print("Redis requires the dedicated local endpoint 127.0.0.1:6380", file=sys.stderr)
            return 1
        try:
            with redis.Redis(
                host="127.0.0.1", port=6380,
                db=int(os.environ.get("REDIS_DB", "0")),
                socket_connect_timeout=2, socket_timeout=2,
                protocol=2, retry=Retry(NoBackoff(), 0),
            ) as connection:
                return 0 if connection.ping() else 1
        except (redis.RedisError, ValueError) as exc:
            print(f"Redis check failed ({type(exc).__name__})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
