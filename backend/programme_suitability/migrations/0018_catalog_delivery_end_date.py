from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0017_catalog_programme_optional_duration')]

    operations = [
        migrations.AddField(
            model_name='catalogdelivery',
            name='end_date_override',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='catalogdelivery',
            constraint=models.CheckConstraint(
                condition=models.Q(end_date_override__isnull=True) | models.Q(end_date_override__gte=models.F('start_date')),
                name='catalog_delivery_date_order',
            ),
        ),
    ]
