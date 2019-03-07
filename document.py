# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import glob
import os
import os.path
import shutil
import subprocess
from functools import wraps
from trytond.model import ModelSQL, ModelView, Workflow, fields, sequence_ordered
from trytond.transaction import Transaction
from trytond.pool import Pool
from trytond.pyson import Bool, Eval
from trytond.config import config as config_
from trytond.i18n import gettext
from trytond.exceptions import UserError
from . import datamatrix

__all__ = ['Queue', 'QueueModel', 'Document', 'Page']
_IDENTIFY_FORMATS = ['PNG', 'JPG', 'JPEG', 'GIF', 'PDF']

def get_directory(queue, type):
    if getattr(queue, type+'_directory'):
        return getattr(queue, type+'_directory')
    return os.path.join(queue.directory, type)

def move_file(queue, filename, directory=None):
    to_directory = (directory or get_directory(queue, 'processed'))
    if not os.path.isdir(to_directory):
        os.mkdir(to_directory)
    destination = os.path.join(to_directory, filename)
    if os.path.exists(destination):
        os.remove(destination)
    shutil.move(os.path.join(queue.directory, filename), to_directory)

def is_merge_file(filename):
    oformat = subprocess.check_output(['identify', '-format', '"%m"', filename])
    if oformat.decode("utf-8").replace('"', '') in _IDENTIFY_FORMATS:
        return True
    return False

def with_root(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        with Transaction().set_user(0):
            return func(self, *args, **kwargs)
    return wrapper


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

        to_create = []
        queue_files = {}
        for queue in queues:
            processed_dir = get_directory(queue, 'processed')
            files = []
            for file_name in sorted(glob.glob(queue.directory + '/*.*')):
                fname = os.path.basename(file_name)
                # check taht file_name don't exist in processed directory
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
    'Document'
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
        pass

    @classmethod
    @Workflow.transition('processed')
    def proceed(cls, documents):
        pass

    @classmethod
    @ModelView.button
    @with_root
    def process(cls, documents):
        Attachment = Pool().get('ir.attachment')

        to_create = []
        for document in documents:
            if document.state != 'pending':
                continue

            record = None
            for model in document.queue.models:
                Model = Pool().get(model.model)
                default = ''.join([i[:1].upper()
                    for i in Model.__name__.split('.')])
                prefix = config_.get('papyrus',
                    Model.__name__.replace('.', '_'), default=default)
                # TODO search by field name or rec_name (code, reference...)

                records = Model.search([
                    ('rec_name', '=', document.code.replace(prefix, '', 1)),
                    ], limit=1)
                if records:
                    record, = records
                    break

            if not record:
                continue

            to_merge = []
            for page in document.pages:
                if not page.attachment:
                    continue
                fname = os.path.join(
                    get_directory(page.queue, 'processed'), page.filename)
                if is_merge_file(fname):
                    to_merge.append(fname)
                else:
                    attachment = Attachment()
                    attachment.name = page.filename
                    attachment.resource = '%s,%s' % (record.__name__, record.id)
                    attachment.type = 'data'
                    attachment.data = page.attachment
                    to_create.append(attachment._save_values)

            if to_merge:
                odir = get_directory(document.queue, 'processed')
                output = '%s%s.pdf' % (odir, document.code)
                to_merge.insert(0, 'convert')
                to_merge.append(output)

                subprocess.check_call(to_merge)

                attachment = Attachment()
                attachment.name = '%s.pdf' % document.code
                attachment.resource = '%s,%s' % (record.__name__, record.id)
                attachment.type = 'data'
                attachment.data = page.attachment
                with open(output, "rb") as f:
                    attachment.data = f.read()
                to_create.append(attachment._save_values)

        if to_create:
            Attachment.create(to_create)
        cls.proceed(documents)

    @classmethod
    def attach_documents(cls):
        documents = cls.search([('state', '=', 'pending')])
        if documents:
            cls.process(documents)


class Page(sequence_ordered(), Workflow, ModelSQL, ModelView,
        datamatrix.DataMatrixMixin):
    'Document Page'
    __name__ = 'papyrus.page'
    _rec_name = 'filename'
    attachment = fields.Function(fields.Binary('Attachment',
        filename='filename'), 'get_attachment', setter='set_attachment')
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
        pass

    @classmethod
    def get_attachment(cls, pages, name):
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
    def set_attachment(cls, pages, name, value):
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

    def get_document(code):
        Document = Pool().get('papyrus.document')

        document = Document()
        document.code = code
        return document

    @classmethod
    @Workflow.transition('processed')
    def proceed(cls, pages):
        pass

    @classmethod
    @ModelView.button
    def process(cls, pages):
        Document = Pool().get('papyrus.document')

        documents = dict(((d.code, d.queue), d) for d in Document.search(
            [('state', '=', 'pending')]))

        to_save = []
        for page in pages:
            if page.state != 'pending':
                continue

            fname = os.path.join(
                get_directory(page.queue, 'processed'), page.filename)
            content = cls.spawn('dmtxread', '--newline', '--verbose',
                '--milliseconds=10000', fname)
            boxes = cls.parse_output(content)
            if not boxes or not boxes[0].text:
                continue

            code = boxes[0].text
            qcode = (code, page.queue)
            if documents.get(qcode):
                document = documents[qcode]
                document.pages += (page,)
            else:
                document = cls.get_document(code)
                document.queue = page.queue
                document.pages = (page,)
            documents[qcode] = document

        to_save = []
        for _, document in documents.items():
            if not document.pages:
                continue
            to_save.append(document)

        if to_save:
            Document.save(to_save)
            cls.proceed(pages)

    @classmethod
    def create_documents(cls):
        pages = cls.search([('state', '=', 'pending')])
        if pages:
            cls.process(pages)
