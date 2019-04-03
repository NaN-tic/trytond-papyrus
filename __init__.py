# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import document
from . import invoice
from . import attachment

def register():
    Pool.register(
        document.Queue,
        document.Document,
        document.Page,
        document.DocumentBox,
        document.PageBox,
        attachment.Attachment,
        module='papyrus', type_='model')
    Pool.register(
        invoice.Invoice,
        invoice.Page,
        invoice.Document,
        depends=['account_invoice'],
        module='papyrus', type_='model')
    Pool.register(
        invoice.InvoicePapyrus,
        depends=['account_invoice'],
        module='papyrus', type_='report')
