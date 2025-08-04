
# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.

from trytond.modules.company.tests import CompanyTestMixin
from trytond.tests.test_tryton import ModuleTestCase


class PapyrusCompanyTestMixin(CompanyTestMixin):

    @property
    def _skip_company_rule(self):
        return super()._skip_company_rule | {
            ('papyrus.document', 'document_company'),
            }


class PapyrusTestCase(PapyrusCompanyTestMixin, ModuleTestCase):
    'Test Papyrus module'
    module = 'papyrus'
    extras = ['account_invoice', 'purchase', 'stock']


del ModuleTestCase
