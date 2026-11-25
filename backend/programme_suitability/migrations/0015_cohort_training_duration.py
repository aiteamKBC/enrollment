from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0014_catalog_revisions')]

    operations = [
        migrations.AddField(
            model_name='catalogcohort',
            name='training_duration_months',
            field=models.PositiveIntegerField(
                null=True, blank=True, editable=False,
                validators=[MinValueValidator(1), MaxValueValidator(120)],
            ),
        ),
    ]
