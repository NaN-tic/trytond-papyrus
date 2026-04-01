# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.model import ModelView
from trytond.pool import Pool
from trytond.config import config as config_

from .common import PapyrusReportMixin

PURCHASE_PREFIX = config_.get('papyrus', 'purchase', default='P-')


class Purchase(metaclass=PoolMeta):
    __name__ = 'purchase.purchase'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'papyrus_barcode': {
                    'icon': 'tryton-print',
                    },
                })

    @classmethod
    @ModelView.button_action('papyrus.report_purchase_papyrus')
    def papyrus_barcode(cls, purchases):
        pass


class PurchasePapyrus(PapyrusReportMixin):
    __name__ = 'purchase.papyrus'
    prefix = PURCHASE_PREFIX


class Page(metaclass=PoolMeta):
    __name__ = 'papyrus.page'

    @classmethod
    def get_prefixes(cls):
        return super().get_prefixes() + [PURCHASE_PREFIX]


class Document(metaclass=PoolMeta):
    __name__ = 'papyrus.document'

    def get_record(self):
        pool = Pool()
        Purchase = pool.get('purchase.purchase')

        res = super().get_record()
        if res:
            return res
        if self.reference:
            if self.reference.startswith(PURCHASE_PREFIX):
                id = self.reference[len(PURCHASE_PREFIX):]
                try:
                    id = int(id)
                except ValueError:
                    return
                records = Purchase.search([
                        ('id', '=', id),
                        ], limit=1)
                if records:
                    return records[0]
