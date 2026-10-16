"""
publish_apply — серверная половина publish. Руками не запускается.

Берёт присланный снимок локальной базы (zstd) и подменяет им серверную базу,
но таблицы, которые живут на сервере, переносит из текущей серверной базы:
  - пользователи, группы, права, content types, сессии, журнал админки;
  - все таблицы приложений из --keep-apps (например tgbot).
Старая база уходит в backups/ (храним 2 последние), подмена атомарная.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

ALWAYS_KEEP = [
    "auth_user",
    "auth_user_groups",
    "auth_user_user_permissions",
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "django_content_type",
    "django_session",
    "django_admin_log",
]
BACKUPS_KEEP = 2


def app_tables(label):
    try:
        cfg = apps.get_app_config(label)
    except LookupError:
        raise CommandError(f"нет такого приложения: {label}")
    return [m._meta.db_table for m in cfg.get_models(include_auto_created=True)]


def tables(con, schema):
    return {r[0] for r in con.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(con, schema, table):
    return [r[1] for r in con.execute(f'PRAGMA {schema}.table_info("{table}")')]


class Command(BaseCommand):
    help = "Серверная часть publish: подмена базы снимком с сохранением серверных таблиц"

    def add_arguments(self, parser):
        parser.add_argument("--snapshot", required=True)
        parser.add_argument("--keep-apps", default="")

    def handle(self, *args, **opts):
        import zstandard

        base = Path(settings.BASE_DIR)
        db = Path(settings.DATABASES["default"]["NAME"])
        snap = (base / opts["snapshot"]).resolve()
        if not snap.exists():
            raise CommandError(f"нет снимка {snap}")

        keep_apps = [a.strip() for a in opts["keep_apps"].split(",") if a.strip()]
        keep = list(ALWAYS_KEEP)
        for label in keep_apps:
            keep += [t for t in app_tables(label) if t not in keep]
        self.stdout.write(f"сохраняю с сервера: {len(keep)} таблиц ({', '.join(keep_apps) or 'только auth'})")

        new = snap.with_name("new.sqlite3")
        new.unlink(missing_ok=True)
        with open(snap, "rb") as fi, open(new, "wb") as fo:
            zstandard.ZstdDecompressor().copy_stream(fi, fo)

        con = sqlite3.connect(new)
        con.isolation_level = None
        if db.exists():
            con.execute("ATTACH DATABASE ? AS old", (str(db),))
            old_t, new_t = tables(con, "old"), tables(con, "main")
            con.execute("BEGIN")
            for t in keep:
                if t not in old_t:
                    continue
                if t not in new_t:
                    ddl = con.execute("SELECT sql FROM old.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
                    con.execute(ddl)
                cols = [c for c in columns(con, "main", t) if c in set(columns(con, "old", t))]
                cl = ", ".join(f'"{c}"' for c in cols)
                con.execute(f'DELETE FROM main."{t}"')
                con.execute(f'INSERT INTO main."{t}" ({cl}) SELECT {cl} FROM old."{t}"')
                n = con.execute(f'SELECT count(*) FROM main."{t}"').fetchone()[0]
                self.stdout.write(f"  {t}: {n}")
            con.execute("COMMIT")
            con.execute("DETACH DATABASE old")
        else:
            self.stdout.write("серверной базы нет — ставлю снимок как есть")

        ok = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
        if ok != "ok":
            raise CommandError(f"проверка новой базы не прошла: {ok}")

        if db.exists():
            bdir = base / "backups"
            bdir.mkdir(exist_ok=True)
            bk = bdir / f"db-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
            src, dst = sqlite3.connect(db), sqlite3.connect(bk)
            src.backup(dst)
            dst.close()
            src.close()
            for old in sorted(bdir.glob("db-*.sqlite3"))[:-BACKUPS_KEEP]:
                old.unlink()
            self.stdout.write(f"копия старой базы: {bk.name}")

        os.replace(new, db)
        snap.unlink(missing_ok=True)
        self.stdout.write(f"база подменена: {db.stat().st_size / 2**20:.0f} МБ")
