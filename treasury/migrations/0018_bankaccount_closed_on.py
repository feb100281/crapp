from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0017_cfitem_reversible"),
    ]

    operations = [
        migrations.AddField(
            model_name="bankaccount",
            name="closed_on",
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name="Закрыт с",
                help_text="Дата закрытия. После неё счёт не показывается в остатках, отчётах и боте; "
                          "история до этой даты сохраняется",
            ),
        ),
    ]
