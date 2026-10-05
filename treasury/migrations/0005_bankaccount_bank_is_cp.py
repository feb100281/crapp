# Ручная миграция: банк — это тоже CP (счёт "Банк" в CPRole), отдельный
# справочник treasury.Bank больше не нужен.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0004_bank_bankaccount_statement_ba_account'),
        ('cp', '0009_gr_delete_cpgroupcompany_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='bankaccount',
            name='bank',
        ),
        migrations.DeleteModel(
            name='Bank',
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='bank',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='accounts', to='cp.cp', verbose_name='Банк'),
        ),
    ]
