"""python manage.py fx_today — курсы ЦБ задачей fxparser и витрина для бота (бот делает это сам)."""

from django.core.management.base import BaseCommand

from dashboard.services import fx_feed


class Command(BaseCommand):
    help = "Курсы ЦБ на сегодня и завтра в витрину dashboard_fx_rate"

    def handle(self, *args, **options):
        n = fx_feed.update()
        self.stdout.write(f"Курсов в витрине: {n}")
