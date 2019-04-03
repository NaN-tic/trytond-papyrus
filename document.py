# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import glob
import os
import os.path
import shutil
import subprocess
from trytond.model import (ModelSQL, ModelView, Workflow, fields,
    sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If
from trytond.i18n import gettext
from trytond.exceptions import UserError
from trytond.transaction import Transaction
from . import tools
from .datamanager import FileDataManager

__all__ = ['Queue', 'Document', 'Page', 'DocumentBox', 'PageBox']


class Queue(ModelSQL, ModelView):
    'Document Queue'
    __name__ = 'papyrus.queue'
    name = fields.Char('Name', required=True)
    page_sequence = fields.Many2One('ir.sequence', 'Page Sequence',
        domain=[
            ('code', '=', 'papyrus.page'),
            ], states={
            'required': Eval('type') == 'page',
            }, depends=['type'])
    document_sequence = fields.Many2One('ir.sequence', 'Document Sequence',
        domain=[
            ('code', '=', 'papyrus.document'),
            ], required=True)
    source_directory = fields.Char('Source Directory', required=True,
        help='Absolute path directory')
    storage_directory = fields.Char('Storage Directory',
        help='Absolute path directory')
    scheduler = fields.Boolean('Scheduler')
    type = fields.Selection([
            ('document', 'Document'),
            ('page', 'Page'),
            ], 'Type', required=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'process': {
                    'icon': 'tryton-ok',
                    },
                'clean': {
                    'icon': 'tryton-clear',
                    },
                })

    @staticmethod
    def default_scheduler():
        return True

    def get_page(queue, filename):
        pool = Pool()
        Page = pool.get('papyrus.page')
        Sequence = pool.get('ir.sequence')

        page = Page()
        page.queue = queue
        page.filename = filename
        page.sequence = Sequence.get_id(queue.page_sequence.id)
        return page

    def get_document(queue, filename):
        pool = Pool()
        Document = pool.get('papyrus.document')

        document = Document()
        document.queue = queue
        document.filename = filename
        return document

    def store_file(self, datamanager, filename):
        'Moves the given file name from source directory to storage directory '
        'using a FileDataManager so we ensure it is moved just after the '
        'transaction has been commited.'
        source = os.path.join(self.source_directory, filename)
        destination = os.path.join(self.storage_directory, filename)
        datamanager.put(source, destination)

    @classmethod
    @ModelView.button
    def process(cls, queues):
        pool = Pool()
        Document = pool.get('papyrus.document')
        Page = pool.get('papyrus.page')

        transaction = Transaction()
        connection = transaction.connection
        database = transaction.database
        # Ensure no two processes execute this method concurrently as we would
        # be moving files twice and there could be race conditions
        database.lock(connection, cls._table)

        datamanager = FileDataManager()
        datamanager = transaction.join(datamanager)

        pages_to_create = []
        documents_to_create = []
        queue_files = {}
        for queue in queues:
            files = []
            for file_name in sorted(glob.glob(os.path.join(
                            queue.source_directory, '*'))):
                # TODO: If file_name already exists as a record and file does
                # not exist in destination it means that we can move the file
                # directly (or just after the transaction finished and the
                # FileDataManager is being executed)
                fname = os.path.basename(file_name)
                # check that file_name doesn't exist in processed directory
                processed_fname = os.path.join(queue.storage_directory, fname)
                count = 0
                check_file = True
                while check_file:
                    if os.path.isfile(processed_fname):
                        count += 1
                        new_file = '%s-%s' % (count, fname)
                        processed_fname = os.path.join(queue.storage_directory,
                            new_file)
                    elif count > 0:
                        shutil.move(os.path.join(queue.source_directory, fname),
                            os.path.join(queue.source_directory, new_file))
                        fname = new_file
                        check_file = False
                    else:
                        check_file = False
                queue.store_file(datamanager, fname)
                if queue.type == 'page':
                    page = cls.get_page(queue, fname)
                    pages_to_create.append(page._save_values)
                elif queue.type == 'document':
                    document = cls.get_document(queue, fname)
                    documents_to_create.append(document._save_values)
                files.append(fname)
            queue_files[queue] = files

        if pages_to_create:
            Page.create(pages_to_create)
        if documents_to_create:
            Document.create(documents_to_create)

    @classmethod
    def cron_process(cls):
        'Process method to be used by cron. It is a separate method so '
        '"process()" is easier to override.'
        queues = cls.search([('scheduler', '=', True)])
        cls.process(queues)

    @classmethod
    @ModelView.button
    def clean(cls, queues):
        '''Removes files in storage_directory that do not have a record in
        papyrus.document or papyrus.page. That is, the document/page has been
        deleted but the file was not removed as we cannot do it in the delete()
        operation because the transaction may be rolled back.
        '''
        transaction = Transaction()
        connection = transaction.connection
        database = transaction.database
        # Ensure there's no Queue.process() being executed at the same time.
        # Otherwise, it could happen that a file exists in the
        # storage_directory but the transaction that created it, has not been
        # committed yet.
        database.lock(connection, cls._table)

        pool = Pool()
        Page = pool.get('papyrus.page')
        Document = pool.get('papyrus.document')

        for queue in queues:
            to_delete = []
            if queue.type == 'document':
                Model = Document
            else:
                Model = Page

            existing = set([x.filename for x in Model.search([
                        ('queue', '=', queue.id)])])
            for path in sorted(glob.glob(os.path.join(
                            queue.storage_directory, '*'))):
                fname = os.path.basename(path)
                if not fname in existing:
                    to_delete.append(path)

            for path in to_delete:
                os.unlink(path)

    @classmethod
    def cron_clean(cls):
        'Clean method to be used by cron. It is a separate method so '
        '"clean()" is easier to override.'
        queues = cls.search([])
        cls.clean(queues)


class Document(Workflow, ModelSQL, ModelView):
    'Papyrus Document'
    __name__ = 'papyrus.document'
    _rec_name = 'number'
    number = fields.Char('Number', required=True,
        states={
            'readonly': (Bool(Eval('pages'))),
        }, depends=['pages'])
    queue = fields.Many2One('papyrus.queue', 'Queue', required=True,
        states={
            'readonly': (Bool(Eval('pages'))),
            },
        depends=['pages'])
    state = fields.Selection([
            ('pending', 'Pending'),
            ('inspected', 'Inspected'),
            ('processed', 'Processed'),
            ], 'State', required=True, readonly=True)
    reference = fields.Char('Reference')
    data = fields.Function(fields.Binary('Data'), 'get_data')
    text = fields.Text('Text', readonly=True)
    boxes = fields.One2Many('papyrus.document.box', 'document', 'Boxes')
    filename = fields.Char('File Name', readonly=True)
    image = fields.Function(fields.Binary('Image'), 'on_change_with_image')
    current_page = fields.Integer('Current Page', domain=[
            If(Bool(Eval('page_count')), [
                    ('current_page', '>=', 1),
                    ('current_page', '<=', Eval('page_count')),
                    ], []),
            ], depends=['page_count'])
    page_count = fields.Function(fields.Integer('Page Count'), 'get_page_count')
    pages = fields.One2Many('papyrus.page', 'document', 'Pages', add_remove=[
            ('document', '=', None),
            ('queue', '=', Eval('queue')),
            ], order=[('sequence', 'ASC')],
        states={
            'readonly': (Eval('state') != 'processed'),
            'invisible': Bool(Eval('filename')),
            }, depends=['state', 'queue', 'filename'])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= set((
                ('pending', 'inspected'),
                ('inspected', 'pending'),
                ('inspected', 'processed'),
                ))
        cls._order.insert(0, ('number', 'DESC'))
        cls._buttons.update({
                'pending': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-back',
                    'depends': ['state'],
                    },
                'inspect': {
                    'invisible': Eval('state') != 'pending',
                    'icon': 'tryton-search',
                    'depends': ['state'],
                    },
                'process': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-ok',
                    'depends': ['state'],
                    },
                'previous_page': {
                    'readonly': Eval('current_page', 1) <= 1,
                    'icon': 'tryton-back',
                    'depends': ['current_page'],
                    },
                'next_page': {
                    'readonly': (Eval('current_page', 1) >=
                        Eval('page_count', 1)),
                    'icon': 'tryton-forward',
                    'depends': ['current_page', 'page_count'],
                    },
                })

    @staticmethod
    def default_state():
        return 'pending'

    @staticmethod
    def default_current_page():
        return 1

    @classmethod
    def create(cls, vlist):
        pool = Pool()
        Queue = pool.get('papyrus.queue')
        Sequence = pool.get('ir.sequence')

        # cache of already instantiated queues
        queues = {}
        vlist = vlist[:]
        for values in vlist:
            if values.get('number'):
                continue
            if not values.get('queue'):
                continue
            queue = queues.get(values['queue'], Queue(values['queue']))
            values['number'] = Sequence.get_id(queue.document_sequence.id)
        return super().create(vlist)

    @classmethod
    def copy(cls, documents):
        raise UserError(gettext('papyrus.document_copy_forbidden'))

    def get_full_path(self):
        if self.filename:
            return os.path.join(self.queue.storage_directory, self.filename)

    def get_data(self, name):
        if self.filename:
            fname = self.get_full_path()
            if os.path.isfile(fname):
                with open(fname, 'rb') as fp:
                    return fp.read()
            return

        to_merge = []
        for page in self.pages:
            if not page.data:
                continue
            to_merge.append(page.get_full_path())

        if to_merge:
            odir = self.queue.storage_directory
            output = '%s%s.pdf' % (odir, self.number)
            to_merge.insert(0, 'convert')
            to_merge.append(output)
            subprocess.check_call(to_merge)
            with open(output, "rb") as f:
                return f.read()

    @fields.depends('current_page', 'page_count')
    def on_change_current_page(self):
        if not self.page_count:
            return
        if not self.current_page or self.current_page < 1:
            self.current_page = 1
        elif self.current_page > self.page_count:
            self.current_page = self.page_count

    @fields.depends('current_page', 'queue', 'filename', 'pages')
    def on_change_with_image(self, name=None):
        if not self.filename:
            pages = self.pages or []
            page = (self.current_page or 1) - 1
            if page >= 0 and page < len(pages):
                return self.pages[page].image
            return
        res = tools.page_image(self.get_full_path(), self.current_page or 1)
        return res

    def get_page_count(self, name):
        if not self.filename:
            return len(self.pages)
        return tools.page_count(self.get_full_path())

    def get_record(self):
        return

    def get_attachment(self, record):
        Attachment = Pool().get('ir.attachment')

        attachment = Attachment()
        attachment.name = '%s.pdf' % self.number
        attachment.resource = record
        attachment.type = 'data'
        attachment.data = self.data
        return attachment

    def scan_text(self):
        text = tools.pdftotext(self.get_full_path())
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_textboxes(self):
        if not self.filename:
            return
        Box = Pool().get('papyrus.document.box')
        boxes = tools.pdftoboxes(self.get_full_path(), Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_datamatrix(self):
        Box = Pool().get('papyrus.document.box')
        filename = self.get_full_path()
        boxes = tools.datamatrix(filename, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_tesseract(self):
        filename = self.get_full_path()
        text = tools.tesseract(filename)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_engines(self):
        return ['text', 'textboxes', 'datamatrix', 'tesseract']

    def scan(self):
        if not self.filename:
            return
        for engine in self.scan_engines():
            getattr(self, 'scan_%s' % engine)()

    @classmethod
    @ModelView.button
    @Workflow.transition('pending')
    def pending(cls, documents):
        DocumentBox = Pool().get('papyrus.document.box')
        boxes = []
        for document in documents:
            document.text = None
            boxes += list(document.boxes)
        if boxes:
            DocumentBox.delete(boxes)
        cls.save(documents)

    @classmethod
    @ModelView.button
    @Workflow.transition('inspected')
    def inspect(cls, documents):
        for document in documents:
            document.scan()
        cls.save(documents)

    @classmethod
    def cron_inspect(cls):
        'Inspect method to be used by cron. It is a separate method so '
        '"inspect()" is easier to override.'
        documents = cls.search([('state', '=', 'pending')])
        cls.inspect(documents)

    @classmethod
    @Workflow.transition('processed')
    def proceed(cls, documents):
        pass

    @classmethod
    @ModelView.button
    def process(cls, documents):
        to_proceed = []
        for document in documents:
            if document.state != 'inspected':
                continue

            record = document.get_record()
            if not record:
                continue

            to_proceed.append(document)

            attachment = document.get_attachment(record)
            # We save record by record because if we saved in batch at the
            # end we would be using a lot of memory
            attachment.save()

        if to_proceed:
            # Mark as processed only the documents that where attached
            # to a record
            cls.proceed(to_proceed)

    @classmethod
    def cron_process(cls):
        'Process method to be used by cron. It is a separate method so '
        '"process()" is easier to override.'
        documents = cls.search([('state', '=', 'inspected')])
        cls.process(documents)

    @ModelView.button_change('current_page', 'filename', 'pages')
    def previous_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page > 1:
            self.current_page -= 1
        self.image = self.on_change_with_image()

    @ModelView.button_change('current_page', 'page_count', 'filename', 'pages')
    def next_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page < self.page_count:
            self.current_page += 1
        self.image = self.on_change_with_image()


class DocumentBox(ModelSQL, ModelView):
    'Papyrus Document Box'
    __name__ = 'papyrus.document.box'
    document = fields.Many2One('papyrus.document', 'Document', required=True,
        ondelete='CASCADE')
    x0 = fields.Float('X0')
    y0 = fields.Float('Y0')
    x1 = fields.Float('X1')
    y1 = fields.Float('Y1')
    width = fields.Function(fields.Float('Width'), 'on_change_with_width')
    height = fields.Function(fields.Float('Height'), 'on_change_with_height')
    type = fields.Selection([
            ('text', 'Text'),
            ('barcode', 'Barcode'),
            ], 'Type', required=True)
    text = fields.Text('Text')

    @fields.depends('x0', 'y0', 'x1', 'y1')
    def on_change_with_width(self, name=None):
        return (self.x1 or 0.0) - (self.x0 or 0.0)

    @fields.depends('x0', 'y0', 'x1', 'y1')
    def on_change_with_height(self, name=None):
        return (self.y1 or 0.0) - (self.y0 or 0.0)


class Page(sequence_ordered(), Workflow, ModelSQL, ModelView):
    'Papyrus Page'
    __name__ = 'papyrus.page'
    _rec_name = 'filename'
    data = fields.Function(fields.Binary('Data',
        filename='filename'), 'get_data', setter='set_data')
    text = fields.Text('Text', readonly=True)
    boxes = fields.One2Many('papyrus.page.box', 'page', 'Boxes', readonly=True)
    document = fields.Many2One('papyrus.document', 'Document',
        states={
            'readonly': (Eval('state') == 'processed'),
            # 'required': (Eval('state') == 'processed'),
            },
        depends=['state'])
    queue = fields.Many2One('papyrus.queue', 'Queue', required=True,
        states={
            'readonly': (Bool(Eval('filename'))),
            },
        depends=['filename'])
    filename = fields.Char("File Name",
        states={
            'readonly': (Bool(Eval('filename'))),
            },
        depends=['filename'])
    image = fields.Function(fields.Binary('Image'), 'on_change_with_image')
    state = fields.Selection([
            ('pending', 'Pending'),
            ('inspected', 'Inspected'),
            ('processed', 'Processed'),
            ], 'State', required=True, readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= set((
                ('pending', 'inspected'),
                ('inspected', 'pending'),
                ('inspected', 'processed'),
                ))
        cls._buttons.update({
                'pending': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-back',
                    'depends': ['state'],
                    },
                'inspect': {
                    'invisible': Eval('state') != 'pending',
                    'icon': 'tryton-search',
                    'depends': ['state'],
                    },
                'process': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-ok',
                    'depends': ['state'],
                    },
                })

    @staticmethod
    def default_state():
        return 'pending'

    @classmethod
    def copy(cls, pages):
        raise UserError(gettext('papyrus.page_copy_forbidden'))

    def get_full_path(self):
        if self.filename:
            return os.path.join(self.queue.storage_directory, self.filename)

    def get_data(self, name):
        if not self.filename:
            return
        with open(self.get_full_path(), 'rb') as f:
            return f.read()

    @classmethod
    def set_data(cls, pages, name, value):
        if not value:
            return

        for page in pages:
            fname = page.get_full_path()
            if os.path.isfile(fname):
                raise UserError(gettext('papyrus.cannot_save_file',
                    filename=page.filename))
            with open(fname, 'wb') as fp:
                fp.write(value)

    @fields.depends('current_page', 'queue', 'filename')
    def on_change_with_image(self, name=None):
        if not self.filename:
            return
        return tools.page_image(self.get_full_path(), 1)

    def scan_text(self):
        if not self.filename:
            return
        text = tools.pdftotext(self.get_full_path())
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_textboxes(self):
        if not self.filename:
            return
        Box = Pool().get('papyrus.page.box')
        boxes = tools.pdftoboxes(self.get_full_path(), Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_datamatrix(self):
        Box = Pool().get('papyrus.page.box')
        filename = self.get_full_path()
        boxes = tools.datamatrix(filename, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_tesseract(self):
        filename = self.get_full_path()
        text = tools.tesseract(filename)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_engines(self):
        return ['text', 'textboxes', 'datamatrix', 'tesseract']

    def scan(self):
        for engine in self.scan_engines():
            getattr(self, 'scan_%s' % engine)()

    @staticmethod
    def get_prefixes():
        return []

    def get_document(self, previous):
        Document = Pool().get('papyrus.document')
        for box in self.boxes:
            if not box.text:
                continue
            for prefix in self.get_prefixes():
                if box.text.startswith(prefix):
                    document = Document()
                    document.queue = self.queue
                    document.state = 'inspected'
                    document.reference = box.text
                    document.pages = (self,)
                    return document
        if previous:
            previous.pages += (self,)
        return previous

    @classmethod
    @ModelView.button
    @Workflow.transition('pending')
    def pending(cls, pages):
        PageBox = Pool().get('papyrus.page.box')
        boxes = []
        for page in pages:
            page.text = None
            boxes += list(page.boxes)
        cls.save(pages)
        if boxes:
            PageBox.delete(boxes)

    @classmethod
    @ModelView.button
    @Workflow.transition('inspected')
    def inspect(cls, pages):
        for page in pages:
            page.scan()
        cls.save(pages)

    @classmethod
    def cron_inspect(cls):
        'Inspect method to be used by cron. It is a separate method so '
        '"inspect()" is easier to override.'
        pages = cls.search([('state', '=', 'pending')])
        cls.inspect(pages)

    @classmethod
    @Workflow.transition('processed')
    def proceed(cls, pages):
        pass

    @classmethod
    @ModelView.button
    def process(cls, pages):
        to_save = []
        to_proceed = []
        # Loop over pages grouping by queue
        queues = set([x.queue.id for x in pages])
        for queue_id in queues:
            previous = None
            for page in pages:
                if page.queue.id != queue_id:
                    continue
                if page.state != 'inspected':
                    continue

                document = page.get_document(previous)
                if not document:
                    continue

                to_proceed.append(page)

                if document != previous:
                    to_save.append(document)
                previous = document

        if to_proceed:
            # Mark as processed only the pages that were added to a document
            cls.proceed(to_proceed)
        if to_save:
            Document.save(to_save)

    @classmethod
    def cron_process(cls):
        'Process method to be used by cron. It is a separate method so '
        '"process()" is easier to override.'
        pages = cls.search([('state', '=', 'inspected')])
        cls.process(pages)


class PageBox(ModelSQL, ModelView):
    'Papyrus Page Box'
    __name__ = 'papyrus.page.box'
    page = fields.Many2One('papyrus.page', 'Page', required=True,
        ondelete='CASCADE')
    x0 = fields.Float('X0')
    y0 = fields.Float('Y0')
    x1 = fields.Float('X1')
    y1 = fields.Float('Y1')
    width = fields.Function(fields.Float('Width'), 'on_change_with_width')
    height = fields.Function(fields.Float('Height'), 'on_change_with_height')
    type = fields.Selection([
            ('text', 'Text'),
            ('barcode', 'Barcode'),
            ], 'Type', required=True)
    text = fields.Text('Text')
    extra = fields.Text('Extra')

    @fields.depends('x0', 'y0', 'x1', 'y1')
    def on_change_with_width(self, name=None):
        return (self.x1 or 0.0) - (self.x0 or 0.0)

    @fields.depends('x0', 'y0', 'x1', 'y1')
    def on_change_with_height(self, name=None):
        return (self.y1 or 0.0) - (self.y0 or 0.0)
