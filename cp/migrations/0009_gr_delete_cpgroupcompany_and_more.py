# Ручная миграция:
# - удаляем прокси CPGroupCompany (заменена реальной моделью Gr);
# - удаляем BankAccount и Bank (больше не нужны — Павел решил отказаться
#   от отдельного справочника банковских счетов);
# - создаём Gr — реальную модель компаний группы, зеркало CP по полям.
#
# Зависит от treasury.0003, которая сначала убирает FK Statement.ba
# на cp.BankAccount — иначе Django не даст удалить BankAccount, пока
# на неё есть ссылка из другого приложения.

import cp.models.cp_model
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cp', '0008_cpgroupcompany'),
        ('treasury', '0003_remove_statement_ba'),
    ]

    operations = [
        migrations.DeleteModel(
            name='CPGroupCompany',
        ),
        migrations.DeleteModel(
            name='BankAccount',
        ),
        migrations.DeleteModel(
            name='Bank',
        ),
        migrations.CreateModel(
            name='Gr',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('inn', models.CharField(db_index=True, max_length=12, unique=True, verbose_name='ИНН')),
                ('ogrn', models.CharField(blank=True, db_index=True, max_length=15, null=True, verbose_name='ОГРН / ОГРНИП')),
                ('name', models.CharField(blank=True, db_index=True, max_length=500, null=True, verbose_name='Наименование')),
                ('address', models.TextField(blank=True, null=True, verbose_name='Адрес')),
                ('registration_date', models.DateField(blank=True, null=True, verbose_name='Дата регистрации')),
                ('manager_name', models.CharField(blank=True, max_length=500, null=True, verbose_name='Руководитель')),
                ('country', models.CharField(blank=True, max_length=100, null=True, verbose_name='Страна')),
                ('region', models.CharField(blank=True, max_length=255, null=True, verbose_name='Регион')),
                ('avatar', models.FileField(blank=True, null=True, upload_to='avatar/', validators=[cp.models.cp_model.validate_svg], verbose_name='Аватар')),
                ('status', models.CharField(choices=[('ACTIVE', 'Действующий'), ('LIQUIDATING', 'Ликвидируется'), ('LIQUIDATED', 'Ликвидирован'), ('BANKRUPT', 'Банкротство'), ('REORGANIZING', 'Реорганизация'), ('NA', 'Нет данных')], db_index=True, default='NA', max_length=30, verbose_name='Статус')),
                ('updated_at', models.DateTimeField(blank=True, null=True, verbose_name='Обновлено из источника')),
                ('payload', models.JSONField(blank=True, default=dict, null=True, verbose_name='Данные')),
                ('groups', models.ManyToManyField(blank=True, related_name='grs', to='cp.cpgroup', verbose_name='Группы')),
            ],
            options={
                'verbose_name': 'Компания',
                'verbose_name_plural': 'Компании',
                'ordering': ['name'],
            },
        ),
    ]
