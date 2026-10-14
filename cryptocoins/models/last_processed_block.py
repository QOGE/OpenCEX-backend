from django.db import models

from core.currency import CurrencyModelField
from exchange.models import BaseModel


class LastProcessedBlock(BaseModel):
    currency = CurrencyModelField(unique=True, db_index=True)
    block_id = models.BigIntegerField(default=0)
    block_hash = models.CharField(max_length=80, null=True, blank=True)

    def __str__(self):
        digest = (self.block_hash or '')[:12]
        if digest:
            return f'{self.currency}: {self.block_id} {digest}'
        return f'{self.currency}: {self.block_id}'
