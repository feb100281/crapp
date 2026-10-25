from django.db import migrations

SOURCE = "/Users/daria/Desktop/COSMO/07 КАССА"


def add_job(apps, schema_editor):
    Jobs = apps.get_model("core", "Jobs")
    Jobs.objects.get_or_create(
        command="cash_desk",
        defaults={
            "name": "Касса (карточки 50)",
            "jobtype": "DATA",
            "scope": "LOCAL",
            "param": {"source": SOURCE},
            "description": (
                "<h2><u>Касса</u></h2>"
                "<p>Карточки счёта 50 из 1С (Excel) → data/parquet/cash/cash_desk.parquet "
                "и cash_desk_files.parquet (итоги файлов для сверки). Подпапки допускаются, "
                "пересекающиеся периоды не задваиваются.</p>"
                "<pre>Параметры:\nsource: папка с выгрузками кассы</pre>"
            ),
        },
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(add_job, migrations.RunPython.noop)]
