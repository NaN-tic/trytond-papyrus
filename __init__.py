# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from trytond.pool import Pool
from . import document
from . import invoice
from . import attachment
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
        ir.Cron,
        electronic_mail.ElectronicMail,
        module=module, type_='model')
    Pool.register(
        attachment.PapyrusAttachment,
        module=module, type_='wizard')
    Pool.register(
        invoice.Invoice,
        invoice.Page,
        invoice.Document,
        depends=['account_invoice'],
        module=module, type_='model')
    Pool.register(
        invoice.InvoicePapyrus,
        depends=['account_invoice'],
        module=module, type_='report')
    Pool.register(
        stock.ShipmentIn,
        stock.ShipmentOutReturn,
        stock.Page,
        stock.Document,
        depends=['stock'],
        module=module, type_='model')
    Pool.register(
        stock.ShipmentInPapyrus,
        stock.ShipmentOutReturnPapyrus,
        depends=['stock'],
        module=module, type_='report')
