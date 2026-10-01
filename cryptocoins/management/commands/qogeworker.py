from cryptocoins.utils.base_worker import BaseWorker
from cryptocoins.coins.qoge.service import QOGECoinService


class Command(BaseWorker):
    SERVICE_CLASS = QOGECoinService
