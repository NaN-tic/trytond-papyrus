import os
import shutil
import tempfile
import unittest

from proteus import Model, Report
from trytond.modules.account.tests.tools import create_chart, create_fiscalyear
from trytond.modules.account_invoice.tests.tools import \
    set_fiscalyear_invoice_sequences
from trytond.modules.company.tests.tools import create_company, get_company
from trytond.modules.papyrus import tools
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules


class Test(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):

        # Install account_invoice
        activate_modules(['papyrus', 'account_invoice'])

        # Create company
        _ = create_company()
        company = get_company()
        tax_identifier = company.party.identifiers.new()
        tax_identifier.type = 'eu_vat'
        tax_identifier.code = 'BE0897290877'
        company.party.save()

        # Create fiscal year
        fiscalyear = set_fiscalyear_invoice_sequences(
            create_fiscalyear(company))
        fiscalyear.click('create_period')

        # Create chart of accounts
        _ = create_chart(company)

        # Create party
        Party = Model.get('party.party')
        party = Party(name='Party')
        party.save()

        # Create invoice
        Invoice = Model.get('account.invoice')
        invoice = Invoice()
        invoice.party = party
        invoice.save()

        # Create directories
        temp_dir = tempfile.mkdtemp()
        source_dir = os.path.join(temp_dir, 'source')
        storage_dir = os.path.join(temp_dir, 'storage')
        os.mkdir(source_dir)
        os.mkdir(storage_dir)

        # Print invoice label
        invoice_label = Report('account.invoice.papyrus')
        ext, content, _, name = invoice_label.execute([invoice], {})
        self.assertEqual(ext, 'pdf')
        self.assertTrue(name)

        # Convert PDF to JPG and store it in queue's source directory
        fd, path = tempfile.mkstemp()
        with open(path, 'wb') as f:
            _ = f.write(content)
        os.close(fd)
        image = tools.page_image(path, 0)
        with open(os.path.join(source_dir, 'image.jpg'), 'wb') as f:
            _ = f.write(image)

        # Create papyrus sequences
        Sequence = Model.get('ir.sequence')
        SequenceType = Model.get('ir.sequence.type')
        page_sequence_type, = SequenceType.find([('name', '=', 'Papyrus Page')])
        page_sequence = Sequence()
        page_sequence.name = 'Page Sequence'
        page_sequence.sequence_type = page_sequence_type
        page_sequence.save()
        document_sequence_type, = SequenceType.find([('name', '=',
                                                      'Papyrus Document')])
        document_sequence = Sequence()
        document_sequence.name = 'Document Sequence'
        document_sequence.sequence_type = document_sequence_type
        document_sequence.save()

        # Create page queue
        Queue = Model.get('papyrus.queue')
        queue = Queue()
        queue.type = 'page'
        queue.page_sequence = page_sequence
        queue.document_sequence = document_sequence
        queue.name = 'Page Queue'
        queue.source_directory = source_dir
        queue.storage_directory = storage_dir
        queue.scheduler = True
        queue.save()

        # Process page queue
        queue.click('process')
        Page = Model.get('papyrus.page')
        pages = Page.find([])
        self.assertEqual(len(pages), 1)
        page, = pages
        self.assertEqual(page.state, 'pending')
        self.assertEqual(page.filename, 'image.jpg')
        page.click('inspect')
        self.assertEqual(page.state, 'inspected')
        self.assertEqual(len(page.boxes), 1)
        page.click('process')
        document = page.document
        self.assertEqual(document.state, 'inspected')
        document.click('process')
        self.assertEqual(document.state, 'processed')

        # Check attachment has been created
        Attachment = Model.get('ir.attachment')
        attachment, = Attachment.find([])
        self.assertEqual(attachment.resource, invoice)

        # Cleanup test directory
        shutil.rmtree(temp_dir)
