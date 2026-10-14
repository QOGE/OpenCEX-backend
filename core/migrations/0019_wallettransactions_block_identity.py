from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_wallettransactions_unique_chain_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='wallettransactions',
            name='block_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='wallettransactions',
            name='block_hash',
            field=models.CharField(blank=True, max_length=80, null=True),
        ),
    ]
