import glob
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from proteus import Model
from trytond.modules.company.tests.tools import create_company, get_company
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
        activate_modules('papyrus')

        # Create sequences
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

        # Create directories
        temp_dir = tempfile.mkdtemp()
        source_dir = os.path.join(temp_dir, 'source')
        os.mkdir(source_dir)

        # Create company
        _ = create_company()
        company = get_company()

        # Add documents to source
        current_dir = os.path.dirname(os.path.realpath(__file__))
        examples_dir = os.path.join(current_dir, 'examples')
        files = glob.glob(os.path.join(examples_dir, '*'))
        for file in files:
            _ = shutil.copy(file, source_dir)
        data = Path(examples_dir, 'one.pdf').read_bytes()

        # Create page queue
        Queue = Model.get('papyrus.queue')
        queue = Queue()
        queue.type = 'page'
        queue.page_sequence = page_sequence
        queue.document_sequence = document_sequence
        queue.name = 'Page Queue'
        queue.source_directory = source_dir
        queue.scheduler = True
        queue.save()

        # Process page queue
        queue.click('process')
        Page = Model.get('papyrus.page')
        pages = Page.find([])
        self.assertEqual(len(pages), 1)

        page, = pages
        self.assertEqual(page.state, 'pending')
        self.assertEqual(page.filename, 'one.pdf')
        self.assertEqual(page.data, data)
        self.assertEqual(os.listdir(source_dir), [])

        page.click('inspect')
        self.assertEqual(page.state, 'inspected')
        self.assertEqual('Sample Papyrus Document' in page.text, True)

        page.click('pending')
        self.assertEqual(page.state, 'pending')
        page.text
        page.click('inspect')
        self.assertEqual(page.state, 'inspected')

        # Delete pages
        Page.delete(pages)

        # Add documents to source
        current_dir = os.path.dirname(os.path.realpath(__file__))
        examples_dir = os.path.join(current_dir, 'examples')
        files = glob.glob(os.path.join(examples_dir, '*'))
        for file in files:
            _ = shutil.copy(file, source_dir)

        # Create document queue
        Queue = Model.get('papyrus.queue')
        queue = Queue()
        queue.company = company
        queue.type = 'document'
        queue.document_sequence = document_sequence
        queue.name = 'Document Queue'
        queue.source_directory = source_dir
        queue.scheduler = True
        queue.save()

        # Process document queue
        queue.click('process')
        Document = Model.get('papyrus.document')
        documents = Document.find([])
        self.assertEqual(len(documents), 1)
        document, = documents
        self.assertEqual(document.state, 'pending')
        self.assertEqual(document.filename, 'one.pdf')
        self.assertEqual(document.data, data)
        self.assertEqual(document.page_count, 1)
        self.assertEqual(os.listdir(source_dir), [])
        document.click('inspect')
        self.assertEqual(document.state, 'inspected')
        self.assertEqual('Sample Papyrus Document' in document.text, True)
        document.click('pending')
        self.assertEqual(document.state, 'pending')
        document.text
        document.click('inspect')
        self.assertEqual(document.state, 'inspected')

        # Delete documents
        Document.delete(documents)

        # Cleanup test directory
        shutil.rmtree(temp_dir)
