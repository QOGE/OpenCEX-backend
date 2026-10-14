from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cryptocoins', '0005_qogewithdrawalapprove'),
    ]

    operations = [
        migrations.AddField(
            model_name='lastprocessedblock',
            name='block_hash',
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
    ]
