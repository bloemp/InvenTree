# Generated by Django 3.2.19 on 2023-06-04 17:43

import django.core.validators
from django.db import migrations, models
import stock.status_codes


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0101_stockitemtestresult_metadata'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockitem',
            name='status',
            field=models.PositiveIntegerField(choices=stock.status_codes.StockStatus.items(), default=10, validators=[django.core.validators.MinValueValidator(0)]),
        ),
    ]
