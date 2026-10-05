# Ручная миграция: убираем FK на cp.BankAccount — эта модель удаляется.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0002_alter_statement_options_statement_ba_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='statement',
            name='ba',
        ),
    ]
