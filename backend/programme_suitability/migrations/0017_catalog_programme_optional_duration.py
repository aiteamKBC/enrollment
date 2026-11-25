from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0016_cohort_date_overrides')]

    operations = [
        migrations.AlterField(
            model_name='catalogprogramme',
            name='duration_months',
            field=models.PositiveIntegerField(
                null=True, blank=True,
                validators=[MinValueValidator(1), MaxValueValidator(120)],
            ),
        ),
    ]
