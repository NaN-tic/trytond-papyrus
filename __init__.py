# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import common
from . import document
from . import invoice
from . import attachment
from . import purchase
from . import stock
from . import ir
from . import electronic_mail

module = 'papyrus'

def register():
    Pool.register(
        attachment.Attachment,
        document.Queue,
        document.Document,
        document.Page,
        document.DocumentBox,
        document.PageBox,
        document.DocumentSplitPage,
        document.DocumentSplitStart,
        ir.Cron,
        electronic_mail.ElectronicMail,
        module=module, type_='model')
    Pool.register(
        document.DocumentSplit,
        module=module, type_='wizard')
    if common.PapyrusReportMixin:
        Pool.register(
            invoice.Invoice,
            invoice.Page,
            invoice.Document,
            depends=['account_invoice', 'html_report'],
            module=module, type_='model')
        Pool.register(
            invoice.InvoicePapyrus,
            depends=['account_invoice', 'html_report'],
            module=module, type_='report')
        Pool.register(
            purchase.Purchase,
            purchase.Page,
            purchase.Document,
            depends=['purchase', 'html_report'],
            module=module, type_='model')
        Pool.register(
            purchase.PurchasePapyrus,
            depends=['purchase', 'html_report'],
            module=module, type_='report')
        Pool.register(
            stock.ShipmentIn,
            stock.ShipmentOutReturn,
            depends=['stock', 'html_report'],
            module=module, type_='model')
        Pool.register(
            stock.Page,
            stock.Document,
            depends=['stock', 'html_report'],
            module=module, type_='model')
        Pool.register(
            stock.ShipmentInPapyrus,
            stock.ShipmentOutReturnPapyrus,
            depends=['stock', 'html_report'],
            module=module, type_='report')
