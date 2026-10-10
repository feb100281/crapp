import django.db.models.deletion
from django.db import migrations, models

import tgbot.models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TgUser",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tg_id", models.BigIntegerField(unique=True, verbose_name="Telegram ID")),
                ("first_name", models.CharField(blank=True, max_length=200, verbose_name="Имя")),
                ("username", models.CharField(blank=True, max_length=200, verbose_name="Логин")),
                ("is_active", models.BooleanField(default=True, verbose_name="Доступ")),
                ("note", models.CharField(blank=True, max_length=200, verbose_name="Кто это")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Подключён")),
                ("last_seen", models.DateTimeField(blank=True, null=True, verbose_name="Последний раз")),
            ],
            options={"verbose_name": "Пользователь бота", "verbose_name_plural": "Пользователи бота",
                     "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="TgInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(default=tgbot.models._code, max_length=40, unique=True, verbose_name="Код")),
                ("note", models.CharField(blank=True, max_length=200, verbose_name="Для кого")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("expires_at", models.DateTimeField(verbose_name="Действует до")),
                ("used_at", models.DateTimeField(blank=True, null=True, verbose_name="Использовано")),
                ("used_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name="invites", to="tgbot.tguser", verbose_name="Кто пришёл")),
            ],
            options={"verbose_name": "Приглашение в бот", "verbose_name_plural": "Приглашения в бот",
                     "ordering": ["-created_at"]},
        ),
    ]
