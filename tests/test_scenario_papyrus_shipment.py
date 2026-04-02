import os
import shutil
import tempfile
import unittest

from proteus import Model, Report
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
        activate_modules(['papyrus', 'stock'])

        # Create company
        _ = create_company()
        company = get_company()
        tax_identifier = company.party.identifiers.new()
        tax_identifier.type = 'eu_vat'
        tax_identifier.code = 'BE0897290877'
        company.party.save()

        # Create directories
        temp_dir = tempfile.mkdtemp()
        source_dir = os.path.join(temp_dir, 'source')
        storage_dir = os.path.join(temp_dir, 'storage')
        os.mkdir(source_dir)
        os.mkdir(storage_dir)

        # Create party
        Party = Model.get('party.party')
        party = Party(name='Party')
        party.save()

        # Create shipments
        ShipmentIn = Model.get('stock.shipment.in')
        shipment_in = ShipmentIn()
        shipment_in.supplier = party
        shipment_in.save()
        ShipmentOutReturn = Model.get('stock.shipment.out.return')
        shipment_out_return = ShipmentOutReturn()
        shipment_out_return.customer = party
        shipment_out_return.save()

        # Print shipment in papyrus label
        shipment_in_label = Report('stock.shipment.in.papyrus')
        ext, content, _, name = shipment_in_label.execute([shipment_in], {})
        self.assertEqual(ext, 'pdf')
        self.assertTrue(name)

        # Convert PDF to JPG and store it in queue's source directory
        fd, path = tempfile.mkstemp()
        with open(path, 'wb') as f:
            _ = f.write(content)
        os.close(fd)
        image = tools.page_image(path, 0)
        with open(os.path.join(source_dir, 'image1.jpg'), 'wb') as f:
            _ = f.write(image)

        # Print shipment out return papyrus label
        shipment_out_return_label = Report('stock.shipment.out.return.papyrus')
        ext, content, _, name = shipment_out_return_label.execute(
            [shipment_out_return], {})
        self.assertEqual(ext, 'pdf')
        self.assertTrue(name)

        # Convert PDF to JPG and store it in queue's source directory
        fd, path = tempfile.mkstemp()
        with open(path, 'wb') as f:
            _ = f.write(content)
        os.close(fd)
        image = tools.page_image(path, 0)
        with open(os.path.join(source_dir, 'image2.jpg'), 'wb') as f:
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
        self.assertEqual(len(pages), 2)
        page1, page2 = pages
        self.assertEqual(page1.state, 'pending')
        self.assertEqual(page1.filename, 'image1.jpg')
        page1.click('inspect')
        self.assertEqual(page1.state, 'inspected')
        self.assertEqual(len(page1.boxes), 1)
        page1.click('process')
        document1 = page1.document
        self.assertEqual(document1.state, 'inspected')
        document1.click('process')
        self.assertEqual(document1.state, 'processed')
        self.assertEqual(page2.state, 'pending')
        self.assertEqual(page2.filename, 'image2.jpg')
        page2.click('inspect')
        self.assertEqual(page2.state, 'inspected')
        self.assertEqual(len(page2.boxes), 1)
        page2.click('process')
        document2 = page2.document
        self.assertEqual(document2.state, 'inspected')
        document2.click('process')
        self.assertEqual(document2.state, 'processed')

        # Check attachment has been created
        Attachment = Model.get('ir.attachment')
        attachment1, attachment2 = Attachment.find([], order=[('id', 'ASC')])
        self.assertEqual(attachment1.resource, shipment_in)
        self.assertEqual(attachment2.resource, shipment_out_return)

        # Cleanup test directory
        shutil.rmtree(temp_dir)
