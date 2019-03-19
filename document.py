# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import glob
import os
import os.path
import shutil
import subprocess
import json
import tempfile
from trytond.model import (ModelSQL, ModelView, Workflow, fields,
    sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Bool, Eval
from trytond.config import config as config_
from trytond.i18n import gettext
from trytond.exceptions import UserError
from .datamatrix import DataMatrix

__all__ = ['Queue', 'QueueModel', 'Document', 'Page']
_IDENTIFY_FORMATS = ['PNG', 'JPG', 'JPEG', 'GIF', 'PDF']

def get_directory(queue, type):
    if getattr(queue, type + '_directory'):
        return getattr(queue, type + '_directory')
    return os.path.join(queue.directory, type)

def move_file(queue, filename, directory=None):
    to_directory = (directory or get_directory(queue, 'processed'))
    if not os.path.isdir(to_directory):
        os.mkdir(to_directory)
    destination = os.path.join(to_directory, filename)
    if os.path.exists(destination):
        os.remove(destination)
    shutil.move(os.path.join(queue.directory, filename), to_directory)


class Queue(ModelSQL, ModelView):
    'Document Queue'
    __name__ = 'papyrus.queue'
    name = fields.Char('Name', required=True)
    page_sequence = fields.Many2One('ir.sequence', 'Page Sequence',
        domain=[
            ('code', '=', 'papyrus.page'),
        ], required=True)
    directory = fields.Char('Directory', required=True,
        help='Absolute path directory')
    processed_directory = fields.Char('Processed Directory',
        help='Absolute path directory')
    scheduler = fields.Boolean('Scheduler')
    models = fields.Many2Many('papyrus.queue.model', 'queue', 'model', 'Models',
        help='Models that search to attach documents')
    type = fields.Selection([
            ('document', 'Document'),
            ('page', 'Page'),
            ], 'Type', required=True)

    @classmethod
    def __setup__(cls):
        super(Queue, cls).__setup__()
        cls._buttons.update({
                'process': {},
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
        # TODO: Use sequence
        document.number = 'XXX'
        document.queue = queue
        document.filename = filename
        return document

    @classmethod
    def import_pages(cls):
        queues = cls.search([('scheduler', '=', True)])
        cls.process(queues)

    @classmethod
    @ModelView.button
    def process(cls, queues):
        # TODO: Check if there can be a race condition if two users click on
        # process button at the same time

        pages_to_create = []
        documents_to_create = []
        queue_files = {}
        for queue in queues:
            processed_dir = get_directory(queue, 'processed')
            files = []
            for file_name in sorted(glob.glob(queue.directory + '/*.*')):
                fname = os.path.basename(file_name)
                # check that file_name doesn't exist in processed directory
                processed_fname = os.path.join(processed_dir, fname)
                count = 0
                check_file = True
                while check_file:
                    if os.path.isfile(processed_fname):
                        count += 1
                        new_file = '%s-%s' % (count, fname)
                        processed_fname = os.path.join(processed_dir, new_file)
                    elif count > 0:
                        shutil.move(os.path.join(queue.directory, fname),
                            os.path.join(queue.directory, new_file))
                        fname = new_file
                        check_file = False
                    else:
                        check_file = False
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

        # TODO move files two phase commit
        for queue, files in queue_files.items():
            for file in files:
                move_file(queue, file)


class QueueModel(ModelSQL):
    'Document Queue - Model'
    __name__ = 'papyrus.queue.model'
    _table = 'papyrus_queue_model_rel'
    queue = fields.Many2One('papyrus.queue', 'Queue', ondelete='CASCADE',
        required=True, select=True)
    model = fields.Many2One('ir.model', 'Model', ondelete='CASCADE',
        required=True, select=True)


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
            ('processed', 'Processed'),
        ], 'State', required=True, readonly=True)
    reference = fields.Char('Reference')
    content = fields.Function(fields.Binary('Content'), 'get_content')
    text = fields.Function(fields.Text('Text'), 'get_text')
    filename = fields.Char('File Name', readonly=True)
    image = fields.Function(fields.Binary('Image'), 'get_image')
    current_page = fields.Integer('Current Page')
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
        super(Document, cls).__setup__()
        cls._transitions |= set((
                ('pending', 'processed'),
                ('processed', 'pending'),
                ))
        cls._order.insert(0, ('number', 'DESC'))
        cls._buttons.update({
                'process': {
                    'invisible': Eval('state') == 'processed',
                    },
                'previous_page': {
                    #'readonly': Eval('current_page') >= 1,
                    'icon': 'tryton-back',
                    },
                'next_page': {
                    # TODO: Set appropriate maximum
                    #'readonly': Eval('current_page') <= 100,
                    'icon': 'tryton-forward',
                    },
                })

    @staticmethod
    def default_state():
        return 'pending'

    @classmethod
    def copy(cls, documents):
        # TODO
        pass

    def get_full_path(self):
        return os.path.join(get_directory(self.queue, 'processed'),
            self.filename)

    def get_content(self, name):
        if self.filename:
            fname = self.get_full_path()
            if os.path.isfile(fname):
                with open(fname, 'rb') as fp:
                    return fp.read()
            return

        to_merge = []
        for page in self.pages:
            if not page.attachment:
                continue
            fname = os.path.join(
                get_directory(page.queue, 'processed'), page.filename)
            to_merge.append(fname)

        if to_merge:
            odir = get_directory(self.queue, 'processed')
            output = '%s%s.pdf' % (odir, self.number)
            to_merge.insert(0, 'convert')
            to_merge.append(output)
            subprocess.check_call(to_merge)
            with open(output, "rb") as f:
                return f.read()

    def get_text(self, name):
        if not self.filename:
            return
        out, err = subprocess.Popen( ['/usr/bin/pdftotext', '-layout', '-enc',
                'UTF-8', self.get_full_path(), '-'],
            stdout=subprocess.PIPE).communicate()
        out = out.decode('utf8')
        return out

    @staticmethod
    def to_jpg(pdf_binary):
        path = '/'.join(pdf_binary.file_path.split('/')[:-1])

        jpg_name = pdf_binary.name[:-4] + '.jpg'
        jpg_path = path + '/' + jpg_name

        subprocess.call(["convert", '-quality', '90', '-density', '200x200',
                '-background', 'white', '-alpha', 'remove',
                pdf_binary.file_path + '[0]', jpg_path])
        return (jpg_path, jpg_name)

    def get_image(self, name):
        if not self.filename:
            return

        processed_dir = get_directory(self.queue, 'processed')
        _, jpg_path = tempfile.mkstemp(suffix='.jpg')

        filename = os.path.join(processed_dir, self.filename)
        filename += '[%d]' % ((self.current_page or 1) - 1)

        # Adding '[0]' to source filename in convert, extracts only the first
        # page of the PDF file
        subprocess.call(["convert", '-quality', '90', '-density', '200x200',
                '-background', 'white', '-alpha', 'remove', filename,
                jpg_path])
        with open(jpg_path, 'rb') as f:
            res = f.read()
        os.unlink(jpg_path)
        return res

    def get_page_count(self, name):
        if not self.filename:
            return len(self.pages)
        out, err = subprocess.Popen( ['/usr/bin/pdfinfo',
                self.get_full_path()], stdout=subprocess.PIPE).communicate()
        out = out.decode('utf-8')
        out = [x for x in out.splitlines() if x.startswith('Pages:')]
        if not out:
            return 0
        out = out[0]
        return int(out.split(':')[-1].strip())

    def get_record(self):
        record = None
        for model in self.queue.models:
            Model = Pool().get(model.model)
            default = ''.join([i[:1].upper()
                for i in Model.__name__.split('.')])
            prefix = config_.get('papyrus',
                Model.__name__.replace('.', '_'), default=default)
            # TODO search by field name or rec_name (number, reference...)

            records = Model.search([
                ('rec_name', '=', self.number.replace(prefix, '', 1)),
                ], limit=1)
            if records:
                record, = records
                break
        return record

    def get_attachment(self, record):
        Attachment = Pool().get('ir.attachment')

        attachment = Attachment()
        attachment.name = '%s.pdf' % self.number
        attachment.resource = '%s,%s' % (record.__name__, record.id)
        attachment.type = 'data'
        attachment.data = self.content
        return attachment

    @classmethod
    @ModelView.button
    @Workflow.transition('processed')
    def process(cls, documents):
        for document in documents:
            if document.state != 'pending':
                continue

            record = document.get_record()
            if not record:
                continue

            attachment = document.get_attachment(record)
            # We save record by record because if we saved in batch at the
            # end we would be using a lot of memory
            attachment.save()

    @classmethod
    @ModelView.button
    def previous_page(cls, documents):
        for document in documents:
            if document.current_page > 1:
                document.current_page -= 1
        cls.save(documents)

    @classmethod
    @ModelView.button
    def next_page(cls, documents):
        for document in documents:
            if document.current_page < document.page_count:
                document.current_page += 1
        cls.save(documents)

    @classmethod
    def attach_documents(cls):
        documents = cls.search([('state', '=', 'pending')])
        if documents:
            cls.process(documents)


class Page(sequence_ordered(), Workflow, ModelSQL, ModelView):
    'Papyrus Page'
    __name__ = 'papyrus.page'
    _rec_name = 'filename'
    content = fields.Function(fields.Binary('Content',
        filename='filename'), 'get_content', setter='set_content')
    document = fields.Many2One('papyrus.document', 'Document',
        states={
            'readonly': (Eval('state') == 'processed'),
            # 'required': (Eval('state') == 'processed'),
            },
        depends=['state'])
    queue = fields.Many2One('papyrus.queue', 'Queue', required=True,
        states={
            'readonly': (Bool(Eval('attachment'))),
            },
        depends=['attachment'])
    filename = fields.Char("File Name", required=True,
        states={
            'readonly': (Bool(Eval('attachment'))),
            },
        depends=['attachment'])
    state = fields.Selection([
            ('pending', 'Pending'),
            ('processed', 'Processed'),
            ], 'State', required=True, readonly=True)
    data = fields.Text('Data', readonly=True)

    @classmethod
    def __setup__(cls):
        super(Page, cls).__setup__()
        cls._transitions |= set((
                ('pending', 'processed'),
                ('processed', 'pending'),
                ))
        cls._buttons.update({
                'process': {
                    'invisible': Eval('state') == 'processed',
                    },
                })

    @staticmethod
    def default_state():
        return 'pending'

    @classmethod
    def copy(cls, pages):
        # TODO
        pass

    @classmethod
    def get_content(cls, pages, name):
        contents = {}
        converter = fields.Binary.cast

        for page in pages:
            fname = os.path.join(
                get_directory(page.queue, 'processed'), page.filename)
            if os.path.isfile(fname):
                try:
                    with open(fname, 'rb') as fp:
                        data = fp.read()
                except Exception:
                    data = None
            contents[page.id] = converter(data) if data else None
        return contents

    @classmethod
    def set_content(cls, pages, name, value):
        if not value:
            return

        for page in pages:
            fname = os.path.join(
                get_directory(page.queue, 'processed'), page.filename)
            if os.path.isfile(fname):
                raise UserError(gettext('papyrus.cannot_save_file',
                    filename=page.filename))
            with open(fname, 'wb') as fp:
                fp.write(value)

    def scan(self):
        filename = os.path.join(
            get_directory(self.queue, 'processed'), self.filename)
        return DataMatrix.scan(filename)

    @staticmethod
    def get_prefixes():
        return []

    def get_document(self, previous):
        Document = Pool().get('papyrus.document')

        boxes = self.scan()
        self.data = json.encode(boxes)
        for box in boxes:
            if box.text and box.text.startswith(self.get_prefixes()):
                document = Document()
                document.reference = box.text
                document.pages = (self,)
                return document
        previous.pages += (self,)
        return previous

    @classmethod
    @ModelView.button
    @Workflow.transition('processed')
    def process(cls, pages):
        to_save = []
        # Loop over pages grouping by queue
        queues = set([x.queue.id for x in pages])
        for queue_id in queues:
            previous = None
            for page in pages:
                if page.queue.id != queue_id:
                    continue
                if page.state != 'pending':
                    continue

                document = page.get_document(previous)
                if not document:
                    continue

                if document != previous:
                    to_save.append(document)
                previous = document

        to_save = []
        if to_save:
            Document.save(to_save)

    @classmethod
    def create_documents(cls):
        pages = cls.search([('state', '=', 'pending')])
        if pages:
            cls.process(pages)
