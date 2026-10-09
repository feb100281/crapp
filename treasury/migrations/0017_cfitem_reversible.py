from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0016_cp_resolver"),
    ]

    operations = [
        migrations.AddField(
            model_name="cfitem",
            name="reversible",
            field=models.BooleanField(
                default=False,
                verbose_name="Принимает возвраты",
                help_text="Сюда можно разносить и встречные суммы: например, возврат комиссии банка "
                          "(поступление) в статью «Комиссии банка» уменьшит выплаты по ней. "
                          "У подстатей действует, если включено у статьи",
            ),
        ),
    ]
