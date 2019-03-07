# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import PoolMeta
from trytond.model import ModelView
from trytond.pyson import Eval
from trytond.modules.jasper_reports.jasper import JasperReport
from trytond.config import config as config_

PREFIX = config_.get('papyrus', 'account_invoice', default='AI')

__all__ = ['Invoice', 'InvoicePapyrus']


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'

    @classmethod
    def __setup__(cls):
        super(Invoice, cls).__setup__()
        cls._buttons.update({
                'barcode': {},
                })

    @classmethod
    @ModelView.button_action('papyrus.report_account_invoice_papyrus')
    def barcode(cls, invoices):
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
        return super(InvoicePapyrus, cls).execute(ids, data)
