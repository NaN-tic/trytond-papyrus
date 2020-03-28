# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.model import ModelView
from trytond.pool import Pool
from trytond.modules.jasper_reports.jasper import JasperReport
from trytond.config import config as config_

PREFIX = config_.get('papyrus', 'account_invoice', default='AI-')


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'papyrus_barcode': {
                    'icon': 'tryton-print',
                    },
                })

    @classmethod
    @ModelView.button_action('papyrus.report_account_invoice_papyrus')
    def papyrus_barcode(cls, invoices):
        pass


class InvoicePapyrus(JasperReport):
    __name__ = 'account.invoice.papyrus'

    @classmethod
    def execute(cls, ids, data):
        parameters = {
            'prefix': PREFIX,
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
        return super().get_prefixes() + [PREFIX]


class Document(metaclass=PoolMeta):
    __name__ = 'papyrus.document'

    def get_record(self):
        Invoice = Pool().get('account.invoice')
        res = super().get_record()
        if res:
            return res
        if self.reference and self.reference.startswith(PREFIX):
            id = self.reference[len(PREFIX):]
            records = Invoice.search([
                    ('id', '=', id),
                    ], limit=1)
            if records:
                return records[0]
