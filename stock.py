# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.model import ModelView
from trytond.pool import Pool
from trytond.modules.jasper_reports.jasper import JasperReport
from trytond.config import config as config_

IN_PREFIX = config_.get('papyrus', 'stock_shipment_in', default='SI-')
OUT_RETURN_PREFIX = config_.get('papyrus', 'stock_shipment_out_return',
    default='SOR-')


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


class ShipmentInPapyrus(JasperReport):
    __name__ = 'stock.shipment.in.papyrus'

    @classmethod
    def execute(cls, ids, data):
        parameters = {
            'prefix': IN_PREFIX,
            }
        if 'parameters' in data:
            data['parameters'].update(parameters)
        else:
            data['parameters'] = parameters
        return super().execute(ids, data)


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


class ShipmentOutReturnPapyrus(JasperReport):
    __name__ = 'stock.shipment.out.return.papyrus'

    @classmethod
    def execute(cls, ids, data):
        parameters = {
            'prefix': OUT_RETURN_PREFIX,
            }
        if 'parameters' in data:
            data['parameters'].update(parameters)
        else:
            data['parameters'] = parameters
        return super().execute(ids, data)


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
