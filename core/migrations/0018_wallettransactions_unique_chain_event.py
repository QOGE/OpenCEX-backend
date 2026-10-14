from django.db import migrations, models


def lowercase_tx_hashes(apps, schema_editor):
    WalletTransactions = apps.get_model('core', 'WalletTransactions')
    for row in WalletTransactions.objects.exclude(tx_hash='').exclude(tx_hash__isnull=True).iterator():
        lowered = row.tx_hash.lower()
        if lowered != row.tx_hash:
            row.tx_hash = lowered
            row.save(update_fields=['tx_hash'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_new_pair_params'),
    ]

    operations = [
        migrations.RunPython(lowercase_tx_hashes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='wallettransactions',
            constraint=models.UniqueConstraint(
                fields=('wallet', 'tx_hash'),
                name='core_wallettransactions_wallet_tx_hash_uniq',
            ),
        ),
    ]
