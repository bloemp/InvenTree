# Generated by Django 3.2.18 on 2023-03-30 11:50

from django.db import migrations, models

from order.status_codes import SalesOrderStatus


class Migration(migrations.Migration):

    dependencies = [
        ('order', '0086_auto_20230323_1108'),
    ]

    operations = [
        migrations.AlterField(
            model_name='salesorder',
            name='status',
            field=models.PositiveIntegerField(
                choices=SalesOrderStatus.items(),
                default=SalesOrderStatus.PENDING.value,
                help_text='Sales order status', verbose_name='Status'
            ),
        ),
    ]
