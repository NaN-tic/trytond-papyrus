# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import glob
import os
import os.path
import re
import shutil
import subprocess
import requests
from email import message_from_bytes
from PyPDF2 import PdfFileReader, PdfFileWriter
from fnmatch import fnmatch
from trytond.model import (ModelSQL, ModelView, Workflow, fields,
    sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If, PYSONEncoder
from trytond.i18n import gettext
from trytond.exceptions import UserError
from trytond.transaction import Transaction
from trytond.exceptions import UserWarning
from trytond.wizard import Wizard, StateView, Button, StateAction
from . import tools
from .datamanager import FileDataManager

__all__ = ['Queue', 'Document', 'Page', 'DocumentSplitPage',
    'DocumentSplitStart', 'DocumentSplit', 'DocumentBox',
    'PageBox']

ELECTRONIC_MAIL_STATES = {
    'invisible': Eval('source_type') != 'electronic_mail',
    'required': Eval('source_type') == 'electronic_mail',
    }

def get_filename_from_response(response):
    "Get filename from requests' Response object"
    cd = response.headers.get('content-disposition')
    if not cd:
        return None
    fname = re.findall('filename=(.+)', cd)
    if len(fname) == 0:
        return None
    fname = fname[0]
    fname = fname.replace('"', '')
    return fname


class Queue(ModelSQL, ModelView):
    'Document Queue'
    __name__ = 'papyrus.queue'
    name = fields.Char('Name', required=True)
    page_sequence = fields.Many2One('ir.sequence', 'Page Sequence',
        domain=[
            ('code', '=', 'papyrus.page'),
            ], states={
            'required': Eval('type') == 'page',
            'invisible': Eval('type') != 'page',
            }, depends=['type'])
    document_sequence = fields.Many2One('ir.sequence', 'Document Sequence',
        domain=[
            ('code', '=', 'papyrus.document'),
            ], required=True)
    source_directory = fields.Char('Source Directory',
        help='Absolute path directory',
        states={
            'invisible': Eval('source_type') != 'directory',
            'required': Eval('source_type') == 'directory',
        }, depends=['source_type'])
    storage_directory = fields.Char('Storage Directory',
        help='Absolute path directory', required=True)
    scheduler = fields.Boolean('Scheduler')
    type = fields.Selection([
            ('document', 'Document'),
            ('page', 'Page'),
            ], 'Type', required=True)
    company = fields.Many2One('company.company', "Company")
    image_dpi = fields.Char('DPI', states={
            'invisible': Eval('type') != 'page',
            }, depends=['type'], help='DPI: This value indicates the number '
        'of pixels per inch that PDFs of documents will generate when they '
        'come from multiple pages, if the system is unable to get the value '
        'from the image on the page (usually in files in PDF format). '
        'Instead, the system respects the number of pixels on the page when '
        'they are capable of get them (typically JPEG files). Therefore, if '
        'the system must use this parameter, the resulting PDF size may be '
        'affected. That is why we recommend using JPEG files for pages.')
    source_type = fields.Selection([
            ('directory', 'Directory'),
            ('electronic_mail', 'Electronic mail'),
            ], 'Source', required=True)
    source_inbox = fields.Many2One('electronic.mail.mailbox', 'Source Inbox',
        states=ELECTRONIC_MAIL_STATES, depends=['source_type'])
    storage_inbox = fields.Many2One('electronic.mail.mailbox', 'Storage Inbox',
        states=ELECTRONIC_MAIL_STATES, depends=['source_type'])
    discarded_inbox = fields.Many2One('electronic.mail.mailbox',
        'Discarded Inbox', states=ELECTRONIC_MAIL_STATES,
        depends=['source_type'])
    electronic_mail_urls_default_policy = fields.Selection([
            (None, ''),
            ('download', 'Download'),
            ('discard', 'Discard'),
            ], 'Default URL Policy', states=ELECTRONIC_MAIL_STATES,
        depends=['source_type'],
        help='The default policy to apply if URL does not match any of the '
        'whitlisted nor blacklisted prefixes.')
    electronic_mail_urls_whitelist = fields.Text('URL Whitelist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, depends=['source_type'],
        help='List of URL prefixes (one per line) that should be downloaded. '
        'For example, use https:// to download all URLs starting with '
        '"https://".')
    electronic_mail_urls_blacklist = fields.Text('URL Blacklist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, depends=['source_type'],
        help='List of URL prefixes (one per line) that should not be '
        'downloaded. For example, use http:// to NO download URLs starting '
        'with "http://"')
    filename_whitelist = fields.Text('Filename Whitelist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, depends=['source_type'], help='List of filename-matching '
        'patterns (one per line). File names matching any of the patterns will '
        'be created as documents as long as they do not match any blacklist '
        'pattern. Valid matching expressions include: *.pdf, *.doc, '
        'invoice*.pdf')
    filename_blacklist = fields.Text('Filename Blacklist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, depends=['source_type'], help='List of filename-matching '
        'patterns (one per line). Filenames matching any of the patterns will '
        'be discarded. Valid matching expressions include: *.pdf, *.doc, '
        'invoice*.pdf')
    document_process_auto = fields.Boolean('Document Process Auto',
        states={
            'invisible': ~Bool(Eval('scheduler'))
        }, depends=['scheduler'])

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

    @classmethod
    def view_attributes(cls):
        return super().view_attributes() + [
            ('//group[@id="directory"]', 'states', {
                'invisible': Eval('source_type') != 'directory',
                }),
            ('//group[@id="electronic_mail"]', 'states', {
                'invisible': Eval('source_type') != 'electronic_mail',
                }),
            ]

    @staticmethod
    def default_scheduler():
        return True

    @staticmethod
    def default_source_type():
        return 'directory'

    @fields.depends('type')
    def on_change_with_filename_whitelist(self):
        if self.type == 'document':
            return '\n'.join(['*.pdf', '*.doc', '*.docx', '*.odp', '*.ods',
                    '*.ppt', '*.pptx', '*.xls', '*.xlsx'])
        elif self.type == 'page':
            return '\n'.join(['*.jpg', '*.jpeg', '*.png', '*.tif'])

    def get_page(self, filename):
        pool = Pool()
        Page = pool.get('papyrus.page')
        Sequence = pool.get('ir.sequence')

        page = Page()
        page.queue = self
        page.filename = filename
        page.sequence = Sequence.get_id(self.page_sequence.id)
        return page

    def get_document(self, filename):
        pool = Pool()
        Document = pool.get('papyrus.document')

        document = Document()
        document.queue = self
        document.filename = filename
        document.company = self.company
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

        pages = []
        documents = []
        for queue in queues:
            method = getattr(queue, 'process_%s' % queue.source_type)
            d, p = method()
            documents += d
            pages += p

        if pages:
            Page.save(pages)
        if documents:
            Document.save(documents)

    def process_directory(self):
        transaction = Transaction()
        connection = transaction.connection
        database = transaction.database
        # Ensure no two processes execute this method concurrently as we would
        # be moving files twice and there could be race conditions
        database.lock(connection, self._table)

        datamanager = FileDataManager()
        datamanager = transaction.join(datamanager)

        files = []
        pages = []
        documents = []
        for file_name in sorted(glob.glob(os.path.join(
                        self.source_directory, '*'))):
            # TODO: If file_name already exists as a record and file does
            # not exist in destination it means that we can move the file
            # directly (or just after the transaction finished and the
            # FileDataManager is being executed)
            fname = os.path.basename(file_name)
            # check that file_name doesn't exist in processed directory
            processed_fname = os.path.join(self.storage_directory, fname)
            count = 0
            check_file = True
            while check_file:
                if os.path.isfile(processed_fname):
                    count += 1
                    new_file = '%s-%s' % (count, fname)
                    processed_fname = os.path.join(self.storage_directory,
                        new_file)
                elif count > 0:
                    shutil.move(os.path.join(self.source_directory, fname),
                        os.path.join(self.source_directory, new_file))
                    fname = new_file
                    check_file = False
                else:
                    check_file = False
            self.store_file(datamanager, fname)
            if self.type == 'page':
                page = self.get_page(fname)
                pages.append(page)
            elif self.type == 'document':
                document = self.get_document(fname)
                documents.append(document)
            files.append(fname)
        return documents, pages

    def matching_filename(self, filename):
        '''
        Checks if the given filename should be imported as a document
        according to the queue filename_whitelist and filename_blacklist
        fields.
        A filename will be valid only if it matches at least one whitelist
        pattern and does not match any blacklist pattern.
        '''
        whitelist = (self.filename_whitelist or '').split('\n')
        for pattern in whitelist:
            if fnmatch(filename, pattern):
                break
        else:
            return False
        blacklist = (self.filename_blacklist or '').split('\n')
        for pattern in blacklist:
            if fnmatch(filename, pattern):
                return False
        return True

    def find_urls(self, text):
        'Very naive algorithm for finding all URLs in a string'
        whitelist = (self.electronic_mail_urls_whitelist or '').split('\n')
        blacklist = (self.electronic_mail_urls_blacklist or '').split('\n')
        default_policy = self.electronic_mail_urls_default_policy

        urls = []
        # By replacing ' and " with whitespace we try to ensure the end of the
        # URL is very easy to find because the URL might be in an href="xx" in
        # a HTML string or in a plain text file (where it will always
        text = text.replace('"', ' ').replace("'", ' ')
        start = 0
        while start >= 0:
            http_start = text.find('http://', start)
            https_start = text.find('https://', start)
            if http_start >= 0 and https_start >= 0:
                start = min(http_start, https_start)
            else:
                start = max(http_start, https_start)
            if start < 0:
                break
            space_end = text.find(' ', start)
            enter_end = text.find('\n', start)
            end = min(space_end, enter_end)
            url = text[start:end]
            url = url.replace('&amp;', '&')
            start = end

            # Apply whitelist, blacklist and default policy
            whitelist = (self.electronic_mail_urls_whitelist or '').split('\n')
            for item in whitelist:
                if not item.strip():
                    continue
                if url.startswith(item):
                    urls.append(url)
                    break
            else:
                for item in blacklist:
                    if not item.strip():
                        continue
                    if url.startswith(item):
                        break
                else:
                    if default_policy == 'download':
                        urls.append(url)
        return urls

    def process_electronic_mail(self):
        pool = Pool()
        ElectronicMail = pool.get('electronic.mail')

        pages = []
        documents = []
        mails = ElectronicMail.search([
                ('mailbox', '=', self.source_inbox),
                ])
        for mail in mails:
            mail_file = mail._get_mail(mail)
            if not mail_file:
                continue
            email = message_from_bytes(mail_file)
            attachments = mail.get_attachments(email)

            mail.mailbox = self.discarded_inbox

            # Extract all URLs from the body of the e-mail, download them,
            # and if they return a file, add them as attachments to be
            # processed
            for url in self.find_urls(mail.body):
                try:
                    response = requests.get(url, timeout=15, verify=False,
                        allow_redirects=True)
                except requests.exceptions.RequestException:
                    continue
                filename = get_filename_from_response(response)
                if not filename:
                    continue
                attachments.append({
                        'filename': filename,
                        'data': response.content,
                        })

            if not attachments:
                continue

            count = 0
            for attachment in attachments:
                count += 1
                file_name = attachment['filename'].lower()

                if not self.matching_filename(file_name):
                    continue

                first_part, ext = os.path.splitext(file_name)
                ext = ext.replace('.', '')
                if not ext:
                    # In some cases attachments are received without extension
                    # because it's missing the separating dot.
                    ext = first_part[-3:]
                    if not ext:
                        continue

                fname = '%015d.%s' % (mail.id * 100 + count, ext)
                processed_fname = os.path.join(self.storage_directory, fname)

                if self.type == 'document':
                    if ext == 'pdf':
                        data = attachment['data']
                    else:
                        data = tools.soffice_convert(attachment['data'], ext,
                            'pdf')
                    if not data:
                        continue
                    with open(processed_fname, 'wb') as f:
                        f.write(data)
                    document = self.get_document(fname)
                    document.origin = mail
                    documents.append(document)
                    mail.mailbox = self.storage_inbox
                elif self.type == 'page':
                    if ext in ('jpg', 'jpeg', 'png', 'tif'):
                        with open(processed_fname, 'wb') as f:
                            f.write(attachment['data'])
                        page = self.get_page(fname)
                        page.origin = mail
                        pages.append(page)
                        mail.mailbox = self.storage_inbox

        ElectronicMail.save(mails)
        return documents, pages

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
            ('cancelled', 'Cancelled'),
            ], 'State', required=True, readonly=True)
    reference = fields.Char('Reference')
    data = fields.Function(fields.Binary('Data', filename='filename'),
        'get_data')
    text = fields.Text('Text', readonly=True)
    boxes = fields.One2Many('papyrus.document.box', 'document', 'Boxes')
    filename = fields.Char('File Name', readonly=True)
    image = fields.Function(fields.Binary('Image', filename='image_filename'),
        'on_change_with_image')
    image_filename = fields.Function(fields.Char('Image Filename'),
            'on_change_with_image_filename')
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
    company = fields.Many2One('company.company', "Company")
    origin = fields.Reference('Origin', selection='get_origin', readonly=True)
    employee = fields.Many2One('company.employee', 'Employee',
        domain=[
            ('company', '=', Eval('company', -1)),
            ],
        depends=['company'])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._transitions |= set((
                ('pending', 'inspected'),
                ('pending', 'cancelled'),
                ('inspected', 'pending'),
                ('inspected','cancelled'),
                ('inspected', 'processed'),
                ('cancelled', 'pending'),
                ))
        cls._order.insert(0, ('queue', 'ASC'))
        cls._order.insert(1, ('number', 'ASC'))
        cls._buttons.update({
                'pending': {
                    'invisible': ~Eval('state').in_(['inspected','cancelled']),
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
                'split': {
                    'invisible': ~Eval('state').in_(['pending']),
                    'icon': 'tryton-document-split',
                    'depends': ['state']
                    },
                'cancelled': {
                    'invisible': ~Eval('state').in_(['pending','inspected']),
                    'icon': 'tryton-cancel',
                    'depends': ['state'],
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
    def copy(cls, documents, default=None):
        raise UserError(gettext('papyrus.document_copy_forbidden'))

    @staticmethod
    def _get_origin():
        'Return list of Model names for origin Reference'
        return ['electronic.mail']

    @classmethod
    def get_origin(cls):
        IrModel = Pool().get('ir.model')
        models = cls._get_origin()
        models = IrModel.search([
                ('model', 'in', models),
                ])
        return [(None, '')] + [(m.model, m.name) for m in models]

    @fields.depends('filename', 'queue')
    def get_full_path(self):
        if self.filename and self.queue:
            return os.path.join(self.queue.storage_directory, self.filename)

    def get_data(self, name):
        if self.filename:
            fname = self.get_full_path()
            if os.path.isfile(fname):
                with open(fname, 'rb') as fp:
                    return fp.read()
            return

        files = []
        for page in self.pages:
            if not page.data:
                continue
            files.append(page.get_full_path())

        if files:
            odir = self.queue.storage_directory
            output = '%s%s.pdf' % (odir, self.number)
            to_merge = ['convert']
            if self.queue.image_dpi:
                to_merge += ['-density', self.queue.image_dpi]
            to_merge += files
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

    @fields.depends('current_page', 'filename', 'pages',
        methods=['get_full_path'])
    def on_change_with_image(self, name=None):
        if not self.filename:
            pages = self.pages or []
            page = (self.current_page or 1) - 1
            if page >= 0 and page < len(pages):
                return self.pages[page].image
            return
        path = self.get_full_path()
        if path:
            res = tools.page_image(path, self.current_page or 1)
            return res

    @fields.depends('current_page', 'filename')
    def on_change_with_image_filename(self, name=None):
        if self.filename:
            filename, _ = os.path.splitext(self.filename or '')
        else:
            filename = 'x'
        page = self.current_page
        if not page or page < 0:
            page = 0
        return '%s-%02d.jpg' % (filename, page)

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
        attachment.content = self.text
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
        Box = Pool().get('papyrus.document.box')
        filename = self.get_full_path()
        text, boxes = tools.tesseract(filename, Box)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

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
        for document in documents:
            with Transaction().set_context(queue_name='papyrus'):
                cls.__queue__.inspect([document])

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
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancelled(cls, documents):
        pass

    @classmethod
    def cron_process(cls):
        'Process method to be used by cron. It is a separate method so '
        '"process()" is easier to override.'
        documents = cls.search([
            ('state', '=', 'inspected'),
            ('queue.document_process_auto', '=', True)
            ])
        for document in documents:
            with Transaction().set_context(queue_name='papyrus'):
                cls.__queue__.process([document])

    # TODO: When this issue is implemented https://bugs.tryton.org/issue8331
    # the "hardcoded" fields will no longer be needed.
    @ModelView.button_change('current_page', 'page_count', 'pages', 'filename',
        'queue')
    def previous_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page > 1:
            self.current_page -= 1
        self.image = self.on_change_with_image()

    # TODO: When this issue is implemented https://bugs.tryton.org/issue8331
    # the hardcoded fields will no longer be needed.
    @ModelView.button_change('current_page', 'page_count', 'filename', 'pages',
        'queue')
    def next_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page < self.page_count:
            self.current_page += 1
        self.image = self.on_change_with_image()

    @classmethod
    @ModelView.button_action('papyrus.wizard_split')
    def split(cls, documents):
        pass


class DocumentSplitPage(ModelView):
    'Document Split Page'
    __name__ = 'document.split.page'
    page = fields.Binary('Image')
    split = fields.Boolean('Split')


class DocumentSplitStart(ModelView):
    'Document Split Start'
    __name__ = 'document.split.start'
    document = fields.Many2One('papyrus.document', 'Document', readonly=True)
    pages = fields.One2Many('document.split.page', None, 'Pages',
        readonly=True)

    @fields.depends('document')
    def on_change_with_pages(self, name=None):
        pages = []
        if self.document:
            for p in range(self.document.page_count):
                current_page = p + 1
                page_image = None

                path = self.document.get_full_path()
                if path:
                    page_image = tools.page_image(path, current_page or 1, width=500)

                values = {
                    'page': page_image,
                    'split': False,
                }
                pages.append(values)
        return pages


class DocumentSplit(Wizard):
    'Papyrus Split'
    __name__ = 'document.split'

    start = StateView('document.split.start',
        'papyrus.papyrus_split_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('OK', 'split_document', 'tryton-ok', True),
        ])
    split_document = StateAction('papyrus.act_papyrus_document')

    def default_start(self, fields):
        Document = Pool().get('papyrus.document')
        document = Document(Transaction().context['active_id'])
        return {
            'document':document.id,
            'pages': [],
        }

    def do_split_document(self,action):
        Warning = Pool().get('res.user.warning')
        Document = Pool().get('papyrus.document')

        document = [0]
        documents = [document]
        num_page = 1
        for page in self.start.pages[1:]:
            if page.split:
                document = []
                documents.append(document)
            document.append(num_page)
            num_page += 1

        #Warning confirmation
        key = 'split_document_%s' % (self.start.document.id)
        if Warning.check(key):
            raise UserWarning(key,
                gettext('papyrus.split_document_confirmation',
                    num_documents=len(documents),
                    pages='\n'.join([','.join(str(x) for x in documents)])))

        #Create documents
        with open(self.start.document.get_full_path(), 'rb') as original:
            reader = PdfFileReader(original)
            num_document = 0
            new_document_id = []
            for d in documents:
                num_document += 1
                writer = PdfFileWriter()
                for p in d:
                   writer.addPage(reader.getPage(p))
                #Document name is like: path/name.extension
                name = (self.start.document.get_full_path().split('.')[0]
                    + '-' + str(num_document) + '.' +
                    self.start.document.get_full_path().split('.')[1])
                with open(name,'wb') as outfile:
                    writer.write(outfile)

                #Create new documents
                document = Document()
                document.queue = self.start.document.queue
                document.number = (self.start.document.number +
                    '-' + str(num_document))
                document.filename = name
                document.save()
                new_document_id.append(document.id)

        domain = [('id', 'in', new_document_id)]
        action['pyson_search_value'] = PYSONEncoder().encode(domain)

        self.start.document.state = 'cancelled'
        self.start.document.save()

        return action, {}


class DocumentBox(ModelSQL, ModelView):
    'Papyrus Document Box'
    __name__ = 'papyrus.document.box'
    document = fields.Many2One('papyrus.document', 'Document', required=True,
        ondelete='CASCADE')
    page = fields.Integer('Page')
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
    origin = fields.Reference('Origin', selection='get_origin', readonly=True)

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
    def copy(cls, pages, default=None):
        raise UserError(gettext('papyrus.page_copy_forbidden'))

    @staticmethod
    def _get_origin():
        'Return list of Model names for origin Reference'
        return ['electronic.mail']

    @classmethod
    def get_origin(cls):
        IrModel = Pool().get('ir.model')
        models = cls._get_origin()
        models = IrModel.search([
                ('model', 'in', models),
                ])
        return [(None, '')] + [(m.model, m.name) for m in models]

    @fields.depends('filename', 'queue')
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

    @fields.depends('filename', methods=['get_full_path'])
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
        Box = Pool().get('papyrus.page.box')
        filename = self.get_full_path()
        text, boxes = tools.tesseract(filename, Box)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

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
        Document = Pool().get('papyrus.document')

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
        pages = cls.search([
            ('state', '=', 'inspected'),
            ('queue.document_process_auto', '=', True)])
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
