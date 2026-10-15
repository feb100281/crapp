from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cp", "0009_gr_delete_cpgroupcompany_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="cp",
            name="title",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Как контрагент называется в дашбордах, Excel и боте. "
                          "Пусто — заполнится автоматически из наименования.",
                max_length=255,
                verbose_name="Название для отчётов",
            ),
        ),
    ]
