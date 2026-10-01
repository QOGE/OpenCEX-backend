from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cryptocoins', '0004_auto_20230607_0811'),
    ]

    operations = [
        migrations.CreateModel(
            name='QOGEWithdrawalApprove',
            fields=[
            ],
            options={
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('core.withdrawalrequest',),
        ),
    ]
