# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import document
from . import invoice

def register():
    Pool.register(
        document.Queue,
        document.QueueModel,
        document.Document,
        document.Page,
        module='papyrus', type_='model')
    Pool.register(
        invoice.Invoice,
        depends=['account_invoice'],
        module='papyrus', type_='model')
    Pool.register(
        invoice.InvoicePapyrus,
        depends=['account_invoice'],
        module='papyrus', type_='report')
