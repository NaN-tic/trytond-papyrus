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
        page.filename = filename
        page.queue = queue
        page.sequence = Sequence.get_id(queue.page_sequence.id)
        return page

    @classmethod
    def import_pages(cls):
        queues = cls.search([('scheduler', '=', True)])
        cls.process(queues)

    @classmethod
    @ModelView.button
    def process(cls, queues):
        Page = Pool().get('papyrus.page')
        # TODO: Check if there can be a race condition if two users click on
        # process button at the same time

        to_create = []
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
                page = cls.get_page(queue, fname)
                to_create.append(page._save_values)
                files.append(fname)
            queue_files[queue] = files

        if to_create:
            Page.create(to_create)
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
    _rec_name = 'code'
    code = fields.Char('Code', required=True,
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
    content = fields.Function(fields.Binary('Content'), 'get_content')
    pages = fields.One2Many('papyrus.page', 'document', 'Pages', add_remove=[
            ('document', '=', None),
            ('queue', '=', Eval('queue')),
        ], order=[('sequence', 'ASC')],
        states={
            'readonly': (Eval('state') != 'processed'),
        }, depends=['state', 'queue'])

    @classmethod
    def __setup__(cls):
        super(Document, cls).__setup__()
        cls._transitions |= set((
                ('pending', 'processed'),
                ('processed', 'pending'),
                ))
        cls._order.insert(0, ('code', 'DESC'))
        cls._buttons.update({
                'process': {
                    'invisible': Eval('state') == 'processed',
                    },
                })

    @staticmethod
    def default_state():
        return 'pending'

    @classmethod
    def copy(cls, documents):
        # TODO
        pass

    def get_content(self):
        to_merge = []
        for page in self.pages:
            if not page.attachment:
                continue
            fname = os.path.join(
                get_directory(page.queue, 'processed'), page.filename)
            to_merge.append(fname)

        if to_merge:
            odir = get_directory(self.queue, 'processed')
            output = '%s%s.pdf' % (odir, self.code)
            to_merge.insert(0, 'convert')
            to_merge.append(output)
            subprocess.check_call(to_merge)
            with open(output, "rb") as f:
                return f.read()

    def get_record(self):
        record = None
        for model in self.queue.models:
            Model = Pool().get(model.model)
            default = ''.join([i[:1].upper()
                for i in Model.__name__.split('.')])
            prefix = config_.get('papyrus',
                Model.__name__.replace('.', '_'), default=default)
            # TODO search by field name or rec_name (code, reference...)

            records = Model.search([
                ('rec_name', '=', self.code.replace(prefix, '', 1)),
                ], limit=1)
            if records:
                record, = records
                break
        return record

    def get_attachment(self, record):
        Attachment = Pool().get('ir.attachment')

        attachment = Attachment()
        attachment.name = '%s.pdf' % self.code
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

    def get_document(self, previous):
        Document = Pool().get('papyrus.document')

        boxes = self.scan()
        if not boxes or not boxes[0].text:
            previous.pages += (self,)
            return previous
        code = boxes[0].text

        document = Document()
        document.code = code
        document.pages = (self,)
        return document

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
