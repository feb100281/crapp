"""
SSH-туннель до прокси-сервера для бота.

В .env:
    TELEGRAM_TUNNEL=root@130.17.1.35          # куда вести туннель (user@host или user@host:port)
    TELEGRAM_PROXY=socks5h://127.0.0.1:1081   # локальный порт туннеля

Бот сам поднимает `ssh -D` и перезапускает его, если связь оборвалась.
Сменить страну — поменять TELEGRAM_TUNNEL и перезапустить бота.
Вход только по SSH-ключу (ssh-copy-id), пароль ssh здесь не спрашивает.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_proc: subprocess.Popen | None = None


def _target() -> tuple[str, str] | None:
    raw = os.getenv("TELEGRAM_TUNNEL", "").strip()
    if not raw:
        return None
    dest, _, port = raw.rpartition(":") if raw.count(":") == 1 and "@" in raw else (raw, "", "")
    return dest, port or "22"


def _local_port() -> int:
    url = urlparse(os.getenv("TELEGRAM_PROXY", "").strip() or "socks5h://127.0.0.1:1080")
    return url.port or 1080


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure() -> None:
    """Поднимает туннель, если он задан в .env и ещё не работает."""
    global _proc
    target = _target()
    if target is None:
        return
    if _proc is not None and _proc.poll() is None:
        return
    dest, ssh_port = target
    port = _local_port()
    if _listening(port):
        raise RuntimeError(
            f"порт {port} уже занят другим туннелем; остановите его "
            f"(lsof -nP -iTCP:{port} -sTCP:LISTEN, затем kill PID) или укажите другой порт в TELEGRAM_PROXY"
        )
    cmd = ["ssh", "-N", "-D", f"127.0.0.1:{port}", "-p", ssh_port,
           "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
           "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
           "-o", "StrictHostKeyChecking=accept-new", dest]
    log.info("туннель: %s → 127.0.0.1:%s", dest, port)
    _proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for _ in range(20):
        if _proc.poll() is not None:
            err = (_proc.stderr.read() or "").strip()
            _proc = None
            raise RuntimeError(f"ssh до {dest} не поднялся: {err or 'нет ответа'}. "
                               f"Нужен вход по ключу: ssh-copy-id -p {ssh_port} {dest}")
        if _listening(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"туннель до {dest} не открыл порт {port}")


def stop() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
    _proc = None
