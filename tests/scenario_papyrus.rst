================
Papyrus Scenario
================

Imports::

    >>> import os
    >>> import glob
    >>> import datetime
    >>> import tempfile
    >>> import shutil
    >>> from dateutil.relativedelta import relativedelta
    >>> from operator import attrgetter
    >>> from proteus import Model, Wizard
    >>> from trytond.tests.tools import activate_modules
    >>> today = datetime.date.today()
    >>> from trytond.modules.company.tests.tools import create_company, \
    ...     get_company
    >>> from trytond.modules.jasper_reports.JasperReports.JasperServer import JasperServer

Install account_invoice::

    >>> config = activate_modules('papyrus')

Create sequences::

    >>> Sequence = Model.get('ir.sequence')
    >>> page_sequence = Sequence()
    >>> page_sequence.name = 'Page Sequence'
    >>> page_sequence.code = 'papyrus.page'
    >>> page_sequence.save()

    >>> document_sequence = Sequence()
    >>> document_sequence.name = 'Document Sequence'
    >>> document_sequence.code = 'papyrus.document'
    >>> document_sequence.save()

Create directories::

    >>> temp_dir = tempfile.mkdtemp()
    >>> source_dir = os.path.join(temp_dir, 'source')
    >>> storage_dir = os.path.join(temp_dir, 'storage')
    >>> os.mkdir(source_dir)
    >>> os.mkdir(storage_dir)

Create company::

    >>> _ = create_company()
    >>> company = get_company()

Add documents to source::

    >>> current_dir = os.path.dirname(os.path.realpath(__file__))
    >>> examples_dir = os.path.join(current_dir, 'examples')
    >>> files = glob.glob(os.path.join(examples_dir, '*'))
    >>> for file in files:
    ...     _ = shutil.copy(file, source_dir)

Create page queue::

    >>> Queue = Model.get('papyrus.queue')
    >>> queue = Queue()
    >>> queue.type = 'page'
    >>> queue.page_sequence = page_sequence
    >>> queue.document_sequence = document_sequence
    >>> queue.name = 'Page Queue'
    >>> queue.source_directory = source_dir
    >>> queue.storage_directory = storage_dir
    >>> queue.scheduler = True
    >>> queue.save()

Process page queue::

    >>> queue.click('process')
    >>> Page = Model.get('papyrus.page')
    >>> pages = Page.find([])
    >>> len(pages)
    1
    >>> page, = pages
    >>> page.state
    'pending'
    >>> page.filename
    'one.pdf'
    >>> page.click('inspect')
    >>> page.state
    'inspected'
    >>> 'Sample Papyrus Document' in page.text
    True
    >>> page.click('pending')
    >>> page.state
    'pending'
    >>> page.text
    >>> page.click('inspect')
    >>> page.state
    'inspected'

Delete pages::

    >>> Page.delete(pages)
    >>> queue.click('clean')
    >>> glob.glob(os.path.join(queue.storage_directory, '*'))
    []

Add documents to source::

    >>> current_dir = os.path.dirname(os.path.realpath(__file__))
    >>> examples_dir = os.path.join(current_dir, 'examples')
    >>> files = glob.glob(os.path.join(examples_dir, '*'))
    >>> for file in files:
    ...     _ = shutil.copy(file, source_dir)

Create document queue::

    >>> Queue = Model.get('papyrus.queue')
    >>> queue = Queue()
    >>> queue.company = company
    >>> queue.type = 'document'
    >>> queue.document_sequence = document_sequence
    >>> queue.name = 'Document Queue'
    >>> queue.source_directory = source_dir
    >>> queue.storage_directory = storage_dir
    >>> queue.scheduler = True
    >>> queue.save()

Process document queue::

    >>> queue.click('process')
    >>> Document = Model.get('papyrus.document')
    >>> documents = Document.find([])
    >>> len(documents)
    1
    >>> document, = documents
    >>> document.state
    'pending'
    >>> document.filename
    'one.pdf'
    >>> document.click('inspect')
    >>> document.state
    'inspected'
    >>> 'Sample Papyrus Document' in document.text
    True
    >>> document.click('pending')
    >>> document.state
    'pending'
    >>> document.text
    >>> document.click('inspect')
    >>> document.state
    'inspected'

Delete documents::

    >>> Document.delete(documents)
    >>> queue.click('clean')
    >>> glob.glob(os.path.join(queue.storage_directory, '*'))
    []

Cleanup test directory::

    >>> shutil.rmtree(temp_dir)
    >>> JasperServer.stop()
