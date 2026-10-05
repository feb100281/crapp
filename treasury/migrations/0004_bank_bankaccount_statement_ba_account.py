# Ручная миграция: справочник банков и банковских счетов переехал в
# приложение treasury, счёт теперь принадлежит собственнику (cp.Gr),
# а не любому контрагенту. Statement получает FK на опознанный счёт.

import cp.models.cp_model
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0003_remove_statement_ba'),
        ('cp', '0009_gr_delete_cpgroupcompany_and_more'),
        ('macro', '0002_fx_numeric_code_alter_fx_code_alter_fx_name_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bank',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bic', models.CharField(db_index=True, max_length=9, unique=True, verbose_name='БИК')),
                ('inn', models.CharField(db_index=True, max_length=12, unique=True, verbose_name='ИНН')),
                ('name', models.CharField(blank=True, max_length=255, null=True, verbose_name='Название')),
                ('avatar', models.FileField(blank=True, null=True, upload_to='avatar/', validators=[cp.models.cp_model.validate_svg], verbose_name='Логотип банка')),
                ('details', models.JSONField(blank=True, null=True, verbose_name='Детали')),
            ],
            options={
                'verbose_name': 'Банк',
                'verbose_name_plural': 'Банки',
                'db_table': 'banks',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='BankAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('number', models.CharField(db_index=True, max_length=32, unique=True, verbose_name='Номер счёта')),
                ('gr', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bank_accounts', to='cp.gr', verbose_name='Владелец')),
                ('bank', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='accounts', to='treasury.bank', verbose_name='Банк')),
                ('currency', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fx_ba', to='macro.fx', verbose_name='Валюта')),
            ],
            options={
                'verbose_name': 'Банковский счёт',
                'verbose_name_plural': 'Банковские счета',
                'ordering': ['number'],
            },
        ),
        migrations.AddField(
            model_name='statement',
            name='ba_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='treasury.bankaccount', verbose_name='Номер счета'),
        ),
    ]
