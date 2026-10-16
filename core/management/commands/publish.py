"""
publish — публикация проекта с локалки на сервер.

Запускается ТОЛЬКО на маке (из админки: Список команд → «Опубликовать на сервер»).

Шаги:
  1. SSH-ключ: при первом запуске создаёт ~/.ssh/crapp_deploy и кладёт его на сервер
     (один раз заходит по паролю SERVER_PASSWORD из .env).
  2. git: pip freeze > requirements.txt → git add -A → commit → push.
  3. Снимок базы (sqlite backup) → сжатие zstd.
  4. rsync снимка на сервер (докачка при обрыве) + rsync media/ без логов задач.
  5. На сервере: git pull → pip install → publish_apply (подмена базы с сохранением
     серверных таблиц) → migrate → collectstatic → restart crapp.

Параметры задачи (Jobs.param):
    {"apps": ["tgbot"]}   — приложения, данные которых живут на сервере и не перезаписываются.
Пользователи, группы, права, сессии и журнал админки на сервере не перезаписываются никогда.
"""

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ...models.jobs import Jobs

LINE = "=" * 60

KEY_PATH = Path.home() / ".ssh" / "crapp_deploy"
LOCAL_TMP = Path(settings.BASE_DIR) / ".tmp" / "publish"
SNAP_NAME = "db.sqlite3.zst"


class Command(BaseCommand):
    help = "Публикация на сервер: git, база (кроме пользователей и исключённых приложений), media"

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    # ------------------------------------------------------------------ helpers
    def log(self, text=""):
        line = f"{timezone.localtime():%H:%M:%S}  {text}" if text else ""
        self._log.write(line + "\n")
        self._log.flush()
        self.stdout.write(line)

    def run(self, cmd, cwd=None, check=True):
        """Локальная команда, вывод — в лог."""
        self.log("$ " + (cmd if isinstance(cmd, str) else " ".join(map(str, cmd))))
        p = subprocess.run(
            cmd, cwd=cwd or settings.BASE_DIR, shell=isinstance(cmd, str),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for ln in p.stdout.splitlines():
            self.log("    " + ln)
        if check and p.returncode != 0:
            raise CommandError(f"команда завершилась с кодом {p.returncode}")
        return p

    @property
    def remote(self):
        return f"{self.user}@{self.host}"

    def ssh_base(self):
        return [
            "ssh", "-i", str(KEY_PATH),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=30",
            self.remote,
        ]

    def ssh(self, script, check=True):
        return self.run(self.ssh_base() + [script], check=check)

    def rsync(self, src, dst, extra=()):
        ssh_e = f"ssh -i {KEY_PATH} -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30"
        cmd = ["rsync", "-a", "--partial", "--timeout=120", "-e", ssh_e, *extra, str(src), dst]
        for attempt in range(1, 6):                     # плохая связь: до 5 попыток с докачкой
            p = self.run(cmd, check=False)
            if p.returncode == 0:
                return
            self.log(f"rsync оборвался (код {p.returncode}), попытка {attempt}/5 — докачиваю")
            time.sleep(5)
        raise CommandError("rsync не прошёл за 5 попыток")

    # ------------------------------------------------------------------ steps
    def step_ssh_key(self):
        self.log("1. SSH-ключ")
        if not KEY_PATH.exists():
            KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
            self.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "crapp-publish", "-f", str(KEY_PATH)])
        if self.ssh("true", check=False).returncode == 0:
            self.log("   вход по ключу работает")
            return

        self.log("   ключа на сервере нет — ставлю по паролю из .env (один раз)")
        password = settings.SERVER_PASSWORD
        if not password:
            raise CommandError("нет SERVER_PASSWORD в .env — положите ключ вручную: "
                               f"ssh-copy-id -i {KEY_PATH} {self.remote}")
        try:
            import paramiko
        except ImportError:
            raise CommandError("pip install paramiko (нужен один раз, для установки ключа)")

        pub = KEY_PATH.with_suffix(".pub").read_text().strip()
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(self.host, username=self.user, password=password, timeout=30,
                    look_for_keys=False, allow_agent=False)
        cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
            "chmod 600 ~/.ssh/authorized_keys && "
            f"(grep -qxF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys)"
        )
        _, out, err = cli.exec_command(cmd)
        rc = out.channel.recv_exit_status()
        cli.close()
        if rc != 0:
            raise CommandError("не удалось положить ключ: " + err.read().decode())
        if self.ssh("true", check=False).returncode != 0:
            raise CommandError("ключ положен, но вход по нему не работает")
        self.log("   ключ установлен, дальше вход без пароля")

    def step_git(self):
        self.log("2. git: freeze → commit → push")
        self.run(f'"{sys.executable}" -m pip freeze > requirements.txt')
        self.run(["git", "add", "-A"])
        staged = self.run(["git", "diff", "--cached", "--name-status"])
        if staged.stdout.strip():
            self.run(["git", "commit", "-m", f"publish {timezone.localtime():%Y-%m-%d %H:%M}"])
        else:
            self.log("   изменений нет, коммит не нужен")
        self.run(["git", "push"])

    def step_snapshot(self):
        self.log("3. Снимок базы → zstd")
        try:
            import zstandard
        except ImportError:
            raise CommandError("pip install zstandard")
        LOCAL_TMP.mkdir(parents=True, exist_ok=True)
        db = Path(settings.DATABASES["default"]["NAME"])
        raw = LOCAL_TMP / "db.sqlite3"
        zst = LOCAL_TMP / SNAP_NAME
        raw.unlink(missing_ok=True)

        src = sqlite3.connect(db)
        dst = sqlite3.connect(raw)
        src.backup(dst)                    # консистентная копия, даже если runserver открыт
        dst.close()
        src.close()

        cctx = zstandard.ZstdCompressor(level=10, threads=-1)
        with open(raw, "rb") as fi, open(zst, "wb") as fo:
            cctx.copy_stream(fi, fo)
        raw.unlink()
        self.log(f"   база {db.stat().st_size / 2**20:.0f} МБ → архив {zst.stat().st_size / 2**20:.0f} МБ")
        return zst

    def step_upload(self, zst):
        self.log("4. Отправка на сервер")
        self.ssh(f"mkdir -p {self.app_dir}/.tmp/publish {self.app_dir}/media/jobs/logs")
        self.rsync(zst, f"{self.remote}:{self.app_dir}/.tmp/publish/{SNAP_NAME}")
        self.log("   media/ (без логов задач)")
        media = Path(settings.MEDIA_ROOT)
        if media.exists():
            self.rsync(f"{media}/", f"{self.remote}:{self.app_dir}/media/",
                       extra=["-z", "--exclude=jobs/logs/"])

    def step_remote(self, keep_apps):
        self.log("5. Сервер: pull → pip → база → migrate → static → restart (сайт + бот)")
        keep = ",".join(keep_apps)
        script = f"""
set -e
cd {self.app_dir}
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python manage.py publish_apply --snapshot .tmp/publish/{SNAP_NAME} --keep-apps "{keep}"
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput -v 0
sudo -n systemctl restart crapp
if [ -f /etc/systemd/system/crapp-bot.service ]; then sudo -n systemctl restart crapp-bot; fi
sleep 2
systemctl is-active crapp
"""
        self.ssh(script)

    # ------------------------------------------------------------------ main
    def handle(self, *args, **options):
        job = Jobs.objects.get(pk=options["job_id"])
        params = job.param or {}
        keep_apps = [a for a in (params.get("apps") or []) if a]

        with open(job.logfile.path, "w", encoding="utf-8") as self._log:
            self.log(LINE)
            self.log(f"Start {job.command} ({job.name})")
            self.log(f"Параметры: {params}")
            self.log(LINE)

            if sys.platform != "darwin":
                raise CommandError("publish запускается только на локалке (мак), не на сервере")

            self.host = settings.SERVER_HOST
            self.user = settings.SERVER_USER
            self.app_dir = os.getenv("SERVER_APP_DIR", f"/home/{self.user}/crapp")
            if not (self.host and self.user):
                raise CommandError("в .env нет SERVER_HOST / SERVER_USER")
            self.log(f"Сервер: {self.remote}:{self.app_dir}")
            self.log(f"Не трогаем на сервере: пользователи, группы, сессии + {keep_apps or '—'}")

            t0 = time.time()
            try:
                self.step_ssh_key()
                self.step_git()
                zst = self.step_snapshot()
                self.step_upload(zst)
                self.step_remote(keep_apps)
            except Exception as exc:
                self.log(LINE)
                self.log(f"ОШИБКА: {exc}")
                raise
            finally:
                (LOCAL_TMP / SNAP_NAME).unlink(missing_ok=True)

            self.log(LINE)
            self.log(f"Готово за {time.time() - t0:.0f} c")
