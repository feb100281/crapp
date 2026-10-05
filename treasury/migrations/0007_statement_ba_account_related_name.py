# Ручная миграция: даём Statement.ba_account человеческий related_name
# ("statements"), чтобы со счёта было видно obj.statements, а не
# дефолтный statement_set — и чтобы под счётом в админке была вложенная
# таблица выписок.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0006_bankaccount_ba_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='statement',
            name='ba_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='statements', to='treasury.bankaccount', verbose_name='Счёт'),
        ),
    ]
