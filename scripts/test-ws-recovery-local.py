"""Run WebSocket subscriber recovery against an isolated temporary Redis on 6381."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api import ws


HOST = "127.0.0.1"
PORT = 6381
REDIS_SERVER = Path("C:/Program Files/Redis/redis-server.exe")


class RecordingSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


class RecoveryLogs(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def check(name, condition, **details):
    print(json.dumps({"check": name, "passed": bool(condition), **details}), flush=True)
    if not condition:
        raise RuntimeError(name)


def port_is_available():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return False
    return True


def port_has_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((HOST, PORT)) == 0


async def wait_until(probe, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await probe():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("Isolated Redis integration condition timed out")


async def pubsub_ids(redis_conn):
    return {
        str(client["id"])
        for client in await redis_conn.client_list()
        if int(client.get("sub", 0)) + int(client.get("psub", 0)) > 0
    }


async def publish_phase(redis_conn, sockets, phase):
    fixtures = [
        (ws._NOTIFY_CHANNEL, {
            "user_id": "qa-target",
            "payload": {"type": "notification", "data": {"id": phase}},
        }),
        ("ws:big-order:live", {"type": "big_order_tick", "data": {"id": phase}}),
        (ws._OPS_ALERT_CHANNEL, {"type": "ops_alert", "data": {"id": phase}}),
    ]
    for channel, payload in fixtures:
        recipients = await redis_conn.publish(channel, json.dumps(payload))
        check(f"{phase}_{channel}_has_one_subscriber", recipients == 1)

    expected = [
        ["notification", "big_order_tick", "ops_alert"],
        ["big_order_tick", "ops_alert"],
        ["notification", "ops_alert"],
    ]

    async def delivered():
        return all(
            len([message for message in recorder.messages if message["data"]["id"] == phase])
            >= len(types)
            for recorder, types in zip(sockets, expected)
        )

    await wait_until(delivered)
    for index, (recorder, types) in enumerate(zip(sockets, expected)):
        actual = sorted(
            message["type"]
            for message in recorder.messages
            if message["data"]["id"] == phase
        )
        check(f"{phase}_recipient_{index}_routing", actual == sorted(types))


async def main():
    check("isolated_port_6381_available", port_is_available())
    check("native_redis_binary_available", REDIS_SERVER.is_file())
    temporary = tempfile.TemporaryDirectory(prefix="ws-recovery-6381-")
    temporary_path = Path(temporary.name)
    process = None
    redis_conn = None
    tasks = []
    owned_server = False
    original_clients = list(ws._clients)
    logger = logging.getLogger(ws.__name__)
    logs = RecoveryLogs()
    original_level = logger.level
    logger.addHandler(logs)
    logger.setLevel(logging.WARNING)
    try:
        config = temporary_path / "redis.conf"
        config.write_text(
            f'bind {HOST}\nport {PORT}\nsave ""\nappendonly no\ndaemonize no\n'
            f'dir "{temporary_path.as_posix()}"\n'
            f'logfile "{(temporary_path / "redis.log").as_posix()}"\n',
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [str(REDIS_SERVER), str(config)],
            cwd=temporary_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        redis_conn = Redis(
            host=HOST,
            port=PORT,
            protocol=2,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=2.0,
            retry=Retry(NoBackoff(), 0),
            max_connections=8,
        )

        async def ready():
            if process.poll() is not None:
                raise RuntimeError("Temporary native Redis exited before readiness")
            try:
                return await redis_conn.ping()
            except (ConnectionError, OSError):
                return False

        await wait_until(ready)
        info = await redis_conn.info("server")
        owned_server = int(info["process_id"]) == process.pid
        check("server_process_owned_by_this_test", owned_server, pid=process.pid)
        check("persistence_disabled", (await redis_conn.config_get("save"))["save"] == "")
        check("appendonly_disabled", (await redis_conn.config_get("appendonly"))["appendonly"] == "no")

        sockets = [RecordingSocket() for _ in range(3)]
        ws._clients[:] = [
            ws._Client(ws=sockets[0], trading_mode="live", user_id="qa-target"),
            ws._Client(ws=sockets[1], trading_mode="live", user_id="qa-other"),
            ws._Client(ws=sockets[2], trading_mode="virtual", user_id="qa-target"),
        ]
        subscribers = [
            (ws._NOTIFY_CHANNEL, ws._handle_notify_message, False),
            (ws._BIG_ORDER_CHANNEL_PATTERN, ws._handle_big_order_message, True),
            (ws._OPS_ALERT_CHANNEL, ws._handle_ops_alert_message, False),
        ]
        tasks = [
            asyncio.create_task(ws._subscriber_loop(redis_conn, channel, handler, pattern=pattern))
            for channel, handler, pattern in subscribers
        ]

        async def three_subscriptions():
            return len(await pubsub_ids(redis_conn)) == 3

        try:
            await wait_until(three_subscriptions)
        except TimeoutError:
            clients = await redis_conn.client_list()
            print(json.dumps({
                "diagnostic": "isolated_subscription_readiness",
                "clients": [
                    {key: client.get(key) for key in ("flags", "sub", "psub", "cmd")}
                    for client in clients
                ],
                "subscriber_logs": [record.getMessage() for record in logs.records],
                "tasks_done": [task.done() for task in tasks],
            }), flush=True)
            raise
        await publish_phase(redis_conn, sockets, "before_disconnect")
        initial_ids = await pubsub_ids(redis_conn)
        disconnected_at = time.monotonic()
        killed = await redis_conn.client_kill_filter(_type="pubsub")
        check("only_owned_6381_pubsub_connections_killed", killed == 3, killed=killed)

        async def reconnected():
            current_ids = await pubsub_ids(redis_conn)
            return len(current_ids) == 3 and not current_ids.intersection(initial_ids)

        await wait_until(reconnected)
        reconnect_seconds = time.monotonic() - disconnected_at
        warnings = [record for record in logs.records if "retry_seconds=" in record.getMessage()]
        check("all_three_subscribers_report_connection_recovery", len(warnings) == 3)
        check("reconnect_observes_backoff", reconnect_seconds >= 0.9, seconds=round(reconnect_seconds, 3))
        check("connection_failures_have_no_traceback", all(record.exc_info is None for record in warnings))
        await publish_phase(redis_conn, sockets, "after_reconnect")

        recovery_ids = await pubsub_ids(redis_conn)
        for channel in [ws._NOTIFY_CHANNEL, "ws:big-order:live", ws._OPS_ALERT_CHANNEL]:
            await redis_conn.publish(channel, "{invalid-fixture-json")
        await publish_phase(redis_conn, sockets, "after_invalid_message")
        check("invalid_messages_keep_healthy_connections", await pubsub_ids(redis_conn) == recovery_ids)
        check("invalid_messages_have_no_traceback", all(record.exc_info is None for record in logs.records))

        cancellation_started = time.monotonic()
        for task in tasks:
            task.cancel()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)
        check("all_subscriber_cancellations_propagate", all(isinstance(result, asyncio.CancelledError) for result in results))
        check("cancellation_completes_promptly", time.monotonic() - cancellation_started < 2.0)
        check("cancelled_subscribers_release_real_sockets", not await pubsub_ids(redis_conn))
        check("pattern_subscription_released", await redis_conn.pubsub_numpat() == 0)
    finally:
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3.0)
        finally:
            ws._clients[:] = original_clients
            logger.removeHandler(logs)
            logger.setLevel(original_level)
            shutdown_requested = False
            termination_fallback = False
            try:
                if redis_conn is not None:
                    try:
                        if owned_server and process.poll() is None:
                            shutdown_requested = True
                            try:
                                await asyncio.wait_for(redis_conn.shutdown(nosave=True), timeout=3.0)
                            except (ConnectionError, TimeoutError):
                                pass
                    finally:
                        await redis_conn.aclose()
            finally:
                try:
                    if process is not None:
                        if process.poll() is None:
                            try:
                                await asyncio.to_thread(process.wait, timeout=1.0)
                            except subprocess.TimeoutExpired:
                                termination_fallback = True
                                process.terminate()
                        try:
                            await asyncio.to_thread(process.wait, timeout=3.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=3.0)
                        check(
                            "temporary_server_process_stopped",
                            process.poll() is not None,
                            pid=process.pid,
                            server_exit_code=process.returncode,
                            shutdown_requested=shutdown_requested,
                            termination_fallback=termination_fallback,
                        )
                        if owned_server:
                            check("temporary_port_has_no_listener", not port_has_listener())
                finally:
                    temporary.cleanup()
                    check("temporary_directory_removed", not temporary_path.exists())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__, "check": str(exc)}), flush=True)
        raise SystemExit(1)
    print(json.dumps({"result": "passed", "exit_code": 0}), flush=True)
