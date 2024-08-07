# Generated by Django 3.0.5 on 2020-04-23 09:56

import InvenTree.fields
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion

from order.status_codes import PurchaseOrderStatus


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0031_auto_20200422_0209'),
        ('order', '0027_auto_20200422_0236'),
    ]

    operations = [
        migrations.AlterField(
            model_name='purchaseorder',
            name='status',
            field=models.PositiveIntegerField(
                choices=PurchaseOrderStatus.items(),
                default=PurchaseOrderStatus.PENDING.value,
                help_text='Purchase order status',
                verbose_name='Status',
            ),
        ),
        migrations.AlterField(
            model_name='salesorder',
            name='status',
            field=models.PositiveIntegerField(choices=[(10, 'Pending'), (20, 'Shipped'), (40, 'Cancelled'), (50, 'Lost'), (60, 'Returned')], default=10, help_text='Purchase order status'),
        ),
        migrations.AlterField(
            model_name='salesorderallocation',
            name='item',
            field=models.ForeignKey(help_text='Select stock item to allocate', limit_choices_to={'part__salable': True}, on_delete=django.db.models.deletion.CASCADE, related_name='sales_order_allocations', to='stock.StockItem'),
        ),
        migrations.AlterField(
            model_name='salesorderallocation',
            name='quantity',
            field=InvenTree.fields.RoundingDecimalField(decimal_places=5, default=1, help_text='Enter stock allocation quantity', max_digits=15, validators=[django.core.validators.MinValueValidator(0)]),
        ),
    ]
