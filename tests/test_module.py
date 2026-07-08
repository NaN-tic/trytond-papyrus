
# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

import unittest
from datetime import date

from trytond.modules.account.tests import create_chart, get_fiscalyear
from trytond.modules.account_invoice.tests import set_invoice_sequences
from trytond.modules.company.tests import CompanyTestMixin
from trytond.modules.company.tests import create_company, set_company
from trytond.pool import Pool
from trytond.tests.test_tryton import ModuleTestCase, with_transaction


class PapyrusCompanyTestMixin(CompanyTestMixin):

    @property
    def _skip_company_rule(self):
        return super()._skip_company_rule | {
            ('papyrus.document', 'document_company'),
            }


class PapyrusTestCase(PapyrusCompanyTestMixin, ModuleTestCase):
    'Test Papyrus module'
    module = 'papyrus'
    extras = [
        'account_invoice', 'attachment_content', 'html_report', 'purchase',
        'stock']

    @with_transaction()
    def test_reports_execute(self):
        pool = Pool()
        Account = pool.get('account.account')
        FiscalYear = pool.get('account.fiscalyear')
        Invoice = pool.get('account.invoice')
        InvoiceReport = pool.get('account.invoice.papyrus', type='report')
        Address = pool.get('party.address')
        Party = pool.get('party.party')
        PartyIdentifier = pool.get('party.identifier')
        PaymentTerm = pool.get('account.invoice.payment_term')
        Purchase = pool.get('purchase.purchase')
        PurchaseReport = pool.get('purchase.papyrus', type='report')
        ShipmentIn = pool.get('stock.shipment.in')
        ShipmentInReport = pool.get('stock.shipment.in.papyrus', type='report')
        ShipmentOutReturn = pool.get('stock.shipment.out.return')
        ShipmentOutReturnReport = pool.get(
            'stock.shipment.out.return.papyrus', type='report')
        Location = pool.get('stock.location')

        company = create_company()
        with set_company(company):
            tax_identifier = PartyIdentifier(
                party=company.party,
                type='eu_vat',
                code='BE0897290877')
            company.party.identifiers = [tax_identifier]
            company.party.save()

            fiscalyear = set_invoice_sequences(get_fiscalyear(company))
            fiscalyear.save()
            FiscalYear.create_period([fiscalyear])
            create_chart(company)

            receivable, = Account.search([
                    ('type.receivable', '=', True),
                    ('closed', '!=', True),
                    ('company', '=', company.id),
                    ], limit=1)
            payable, = Account.search([
                    ('type.payable', '=', True),
                    ('closed', '!=', True),
                    ('company', '=', company.id),
                    ], limit=1)
            warehouse, = Location.search([
                    ('type', '=', 'warehouse'),
                    ], limit=1)

            payment_term, = PaymentTerm.create([{
                        'name': 'Direct',
                        'lines': [('create', [{
                                        'type': 'remainder',
                                        }])],
                        }])

            party = Party(name='Party')
            party.addresses = [Address()]
            party.account_receivable = receivable
            party.account_payable = payable
            party.save()

            purchase = Purchase()
            for key, value in Purchase.default_get(
                    Purchase._fields.keys(), with_rec_name=False).items():
                if value is not None:
                    setattr(purchase, key, value)
            purchase.company = company
            purchase.party = party
            purchase.invoice_address = party.addresses[0]
            purchase.payment_term = payment_term
            purchase.currency = company.currency
            purchase.purchase_date = date.today()
            if hasattr(purchase, 'on_change_party'):
                purchase.on_change_party()
            purchase.save()

            invoice = Invoice()
            invoice.type = 'out'
            invoice.company = company
            invoice.party = party
            invoice.invoice_address = party.addresses[0]
            invoice.payment_term = payment_term
            invoice.currency = company.currency
            invoice.invoice_date = date.today()
            invoice.set_journal()
            invoice.on_change_party()
            invoice.account = receivable
            invoice.save()

            shipment_in, = ShipmentIn.create([{
                        'company': company.id,
                        'supplier': party.id,
                        'planned_date': date.today(),
                        'warehouse': warehouse.id,
                        'warehouse_input': warehouse.input_location.id,
                        'warehouse_storage': warehouse.storage_location.id,
                        }])

            shipment_out_return, = ShipmentOutReturn.create([{
                        'company': company.id,
                        'customer': party.id,
                        'planned_date': date.today(),
                        'warehouse': warehouse.id,
                        'warehouse_input': warehouse.input_location.id,
                        'warehouse_storage': warehouse.storage_location.id,
                        }])

            for Report, record in [
                    (PurchaseReport, purchase),
                    (InvoiceReport, invoice),
                    (ShipmentInReport, shipment_in),
                    (ShipmentOutReturnReport, shipment_out_return),
                    ]:
                ext, content, _, _ = Report.execute([record.id], {})
                self.assertEqual(ext, 'pdf')
                self.assertTrue(content)


class PapyrusWithoutHtmlReportTestCase(PapyrusCompanyTestMixin, ModuleTestCase):
    'Test Papyrus module without html_report'
    module = 'papyrus'
    extras = ['account_invoice', 'purchase', 'stock']

    @unittest.skip("Optional html_report views are not loaded in this variant")
    def test_view(self):
        pass

    @with_transaction()
    def test_papyrus_report_actions_not_loaded(self):
        pool = Pool()
        ActionReport = pool.get('ir.action.report')
        ModelButton = pool.get('ir.model.button')

        for report_name in [
                'account.invoice.papyrus',
                'purchase.papyrus',
                'stock.shipment.in.papyrus',
                'stock.shipment.out.return.papyrus',
                ]:
            with self.subTest(report_name=report_name):
                self.assertFalse(ActionReport.search([
                            ('report_name', '=', report_name),
                            ]))

        for model_name in [
                'account.invoice',
                'purchase.purchase',
                'stock.shipment.in',
                'stock.shipment.out.return',
                ]:
            with self.subTest(model_name=model_name):
                self.assertFalse(ModelButton.search([
                            ('model.name', '=', model_name),
                            ('name', '=', 'papyrus_barcode'),
                            ]))


del ModuleTestCase
