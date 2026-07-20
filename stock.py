# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.model import ModelView
from trytond.pool import Pool
import trytond.config as config_
from .common import PapyrusReportMixin

IN_PREFIX = config_.get('papyrus', 'stock_shipment_in', default='SI-')
OUT_RETURN_PREFIX = config_.get('papyrus', 'stock_shipment_out_return',
    default='SOR-')


if PapyrusReportMixin:
    class ShipmentIn(metaclass=PoolMeta):
        __name__ = 'stock.shipment.in'

        @classmethod
        def __setup__(cls):
            super().__setup__()
            cls._buttons.update({
                    'papyrus_barcode': {
                        'icon': 'tryton-print',
                        },
                    })

        @classmethod
        @ModelView.button_action('papyrus.report_stock_shipment_in_papyrus')
        def papyrus_barcode(cls, invoices):
            pass


    class ShipmentInPapyrus(PapyrusReportMixin):
        __name__ = 'stock.shipment.in.papyrus'
        prefix = IN_PREFIX


    class ShipmentOutReturn(metaclass=PoolMeta):
        __name__ = 'stock.shipment.out.return'

        @classmethod
        def __setup__(cls):
            super().__setup__()
            cls._buttons.update({
                    'papyrus_barcode': {
                        'icon': 'tryton-print',
                        },
                    })

        @classmethod
        @ModelView.button_action('papyrus.report_stock_shipment_out_return_papyrus')
        def papyrus_barcode(cls, invoices):
            pass


    class ShipmentOutReturnPapyrus(PapyrusReportMixin):
        __name__ = 'stock.shipment.out.return.papyrus'
        prefix = OUT_RETURN_PREFIX


    class Page(metaclass=PoolMeta):
        __name__ = 'papyrus.page'

        @classmethod
        def get_prefixes(cls):
            return super().get_prefixes() + [IN_PREFIX, OUT_RETURN_PREFIX]


    class Document(metaclass=PoolMeta):
        __name__ = 'papyrus.document'

        def get_record(self):
            pool = Pool()
            ShipmentIn = pool.get('stock.shipment.in')
            ShipmentOutReturn = pool.get('stock.shipment.out.return')

            res = super().get_record()
            if res:
                return res
            if self.reference:
                if self.reference.startswith(IN_PREFIX):
                    id = self.reference[len(IN_PREFIX):]
                    try:
                        id = int(id)
                    except ValueError:
                        return
                    records = ShipmentIn.search([
                            ('id', '=', id),
                            ], limit=1)
                    if records:
                        return records[0]
                if self.reference.startswith(OUT_RETURN_PREFIX):
                    id = self.reference[len(OUT_RETURN_PREFIX):]
                    try:
                        id = int(id)
                    except ValueError:
                        return
                    records = ShipmentOutReturn.search([
                            ('id', '=', id),
                            ], limit=1)
                    if records:
                        return records[0]
