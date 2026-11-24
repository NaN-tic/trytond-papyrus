# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import logging
import os
import re
from datetime import datetime
from email import message_from_bytes
from fnmatch import fnmatch
from io import BytesIO
from pathlib import Path

import gdown
import requests
from gdown.exceptions import DownloadError
from sql import Null
from sql.operators import Exists
from trytond.model import (ModelSQL, ModelView, Workflow, fields,
    sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Bool, Eval, If, PYSONEncoder, Id
from trytond.i18n import gettext
from trytond.exceptions import UserError, UserWarning
from trytond.transaction import Transaction, without_check_access
from trytond.wizard import Wizard, StateView, Button, StateAction
from . import tools
from .datamanager import FileDataManager
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

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


def _binary_data(record):
    data = getattr(record, 'data', None)
    if isinstance(data, int) and record.id is not None and record.id >= 0:
        # The clients can request the size of a Binary together with a preview.
        with Transaction().set_context({f'{record.__name__}.data': None}):
            data = record.read([record.id], ['data'])[0]['data']
    return data


def _migrate_binary_data(Model, Queue):
    cursor = Transaction().connection.cursor()
    table = Model.__table__()
    queue = Queue.__table__()
    last_id = 0
    while True:
        cursor.execute(*table.join(queue, condition=table.queue == queue.id).select(
                table.id, table.filename, queue.storage_directory,
                where=(table.id > last_id) & (table.filename != Null)
                & (table.filename != '') & (table.data == Null),
                order_by=table.id, limit=100))
        rows = cursor.fetchall()
        if not rows:
            break
        for record_id, filename, directory in rows:
            directory = Path(directory).resolve()
            path = (directory / filename).resolve()
            if not path.is_relative_to(directory):
                raise RuntimeError(
                    f'{Model.__name__} {record_id}: {path} is outside {directory}')
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise RuntimeError(
                    f'{Model.__name__} {record_id}: cannot migrate {path}') from exc
            cursor.execute(*table.update(
                    [table.data], [Model.data.sql_format(data)],
                    where=table.id == record_id))
        last_id = rows[-1][0]


class Queue(ModelSQL, ModelView):
    'Document Queue'
    __name__ = 'papyrus.queue'
    name = fields.Char('Name', required=True)
    page_sequence = fields.Many2One('ir.sequence', 'Page Sequence',
        domain=[
            ('sequence_type', '=', Id('papyrus', 'sequence_type_papyrus_page')),
            ], states={
            'required': Eval('type') == 'page',
            'invisible': Eval('type') != 'page',
            })
    document_sequence = fields.Many2One('ir.sequence', 'Document Sequence',
        domain=[
            ('sequence_type', '=', Id('papyrus',
                    'sequence_type_papyrus_document')),
            ], required=True)
    source_directory = fields.Char('Source Directory',
        help='Absolute path directory',
        states={
            'invisible': Eval('source_type') != 'directory',
            'required': Eval('source_type') == 'directory',
        })
    source_url = fields.Char('Source URL', states={
            'invisible': Eval('source_type') != 'google_drive',
            'required': Eval('source_type') == 'google_drive',
            }, help='Google Drive shared URL of the folder to be used as '
        'source.')
    scheduler = fields.Boolean('Scheduler')
    type = fields.Selection([
            ('document', 'Document'),
            ('page', 'Page'),
            ], 'Type', required=True)
    company = fields.Many2One('company.company', "Company")
    image_dpi = fields.Char('DPI', states={
            'invisible': Eval('type') != 'page',
            }, help='DPI: This value indicates the number '
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
            ('google_drive', 'Google Drive'),
            ], 'Source', required=True)
    source_inbox = fields.Many2One('electronic.mail.mailbox', 'Source Inbox',
        states=ELECTRONIC_MAIL_STATES)
    storage_inbox = fields.Many2One('electronic.mail.mailbox', 'Storage Inbox',
        states=ELECTRONIC_MAIL_STATES)
    discarded_inbox = fields.Many2One('electronic.mail.mailbox',
        'Discarded Inbox', states=ELECTRONIC_MAIL_STATES)
    electronic_mail_urls_default_policy = fields.Selection([
            (None, ''),
            ('download', 'Download'),
            ('discard', 'Discard'),
            ], 'Default URL Policy', states=ELECTRONIC_MAIL_STATES,
        help='The default policy to apply if URL does not match any of the '
        'whitlisted nor blacklisted prefixes.')
    electronic_mail_urls_whitelist = fields.Text('URL Whitelist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            },
        help='List of URL prefixes (one per line) that should be downloaded. '
        'For example, use https:// to download all URLs starting with '
        '"https://".')
    electronic_mail_urls_blacklist = fields.Text('URL Blacklist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            },
        help='List of URL prefixes (one per line) that should not be '
        'downloaded. For example, use http:// to NO download URLs starting '
        'with "http://"')
    filename_whitelist = fields.Text('Filename Whitelist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, help='List of filename-matching '
        'patterns (one per line). File names matching any of the patterns will '
        'be created as documents as long as they do not match any blacklist '
        'pattern. Valid matching expressions include: *.pdf, *.doc, '
        'invoice*.pdf')
    filename_blacklist = fields.Text('Filename Blacklist', states={
            'invisible': Eval('source_type') != 'electronic_mail',
            }, help='List of filename-matching '
        'patterns (one per line). Filenames matching any of the patterns will '
        'be discarded. Valid matching expressions include: *.pdf, *.doc, '
        'invoice*.pdf')
    document_process_auto = fields.Boolean('Document Process Auto',
        states={
            'invisible': ~Bool(Eval('scheduler'))
        })
    delay = fields.TimeDelta('Delay', states={
            'invisible': Eval('source_type') != 'directory',
            })

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._buttons.update({
                'process': {
                    'icon': 'tryton-ok',
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
            ('//group[@id="google_drive"]', 'states', {
                'invisible': Eval('source_type') != 'google_drive',
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

    def get_page(self, filename, data):
        pool = Pool()
        Page = pool.get('papyrus.page')

        page = Page()
        page.queue = self
        page.filename = filename
        page.data = data
        page.sequence = self.page_sequence.get()
        return page

    def get_document(self, filename, data):
        pool = Pool()
        Document = pool.get('papyrus.document')

        document = Document()
        document.queue = self
        document.filename = filename
        document.data = data
        document.company = self.company
        return document

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
        # Prevent concurrent imports from consuming the same source files.
        self.lock()

        datamanager = FileDataManager()
        datamanager = transaction.join(datamanager)

        pages = []
        documents = []
        for path in sorted(Path(self.source_directory).glob('*')):
            if path.name.startswith('.') or not path.is_file():
                continue
            if self.delay:
                file_time = datetime.fromtimestamp(path.stat().st_mtime)
                if file_time > datetime.now() - self.delay:
                    continue
            data = path.read_bytes()
            if self.type == 'page':
                pages.append(self.get_page(path.name, data))
            else:
                documents.append(self.get_document(path.name, data))
            datamanager.put(path)
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
            url = text[start:end-1]
            url = url.replace('&amp;', '&')
            start = end

            # Apply whitelist, blacklist and default policy
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
        return list(set(urls))

    def process_electronic_mail(self):
        pool = Pool()
        ElectronicMail = pool.get('electronic.mail')

        pages = []
        documents = []
        mails = ElectronicMail.search([
                ('mailbox', '=', self.source_inbox),
                ])
        for mail in mails:
            mail_file = mail.mail_file
            if not mail_file:
                continue
            email = message_from_bytes(mail_file)
            attachments = mail.get_attachments(email)

            mail.mailbox = self.discarded_inbox

            # Extract all URLs from the body of the e-mail, download them,
            # and if they return a file, add them as attachments to be
            # processed
            try:
                body = mail.body
            except UnicodeDecodeError:
                logger.error('UnicodeDecodeError mail ID: %s)' % (mail.id))
                continue

            for url in self.find_urls(body):
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

                fname = '%015d.%s' % (
                    mail.id * 100 + count, 'pdf' if self.type == 'document' else ext)

                if self.type == 'document':
                    if ext == 'pdf':
                        data = attachment['data']
                    else:
                        data = tools.soffice_convert(attachment['data'], ext,
                            'pdf')
                    if not data:
                        continue
                    document = self.get_document(fname, data)
                    document.origin = mail
                    documents.append(document)
                    mail.mailbox = self.storage_inbox
                elif ext in ('jpg', 'jpeg', 'png', 'tif') and attachment['data']:
                    page = self.get_page(fname, attachment['data'])
                    page.origin = mail
                    pages.append(page)
                    mail.mailbox = self.storage_inbox

        ElectronicMail.save(mails)
        return documents, pages

    def process_google_drive(self):
        pool = Pool()
        Page = pool.get('papyrus.page')
        Document = pool.get('papyrus.document')

        # Ensure no two processes execute this method concurrently as we would
        # be creating the same document or page twice
        self.lock()

        pages = []
        documents = []
        try:
            drive_files = gdown.download_folder(url=self.source_url,
                skip_download=True)
        except DownloadError:
            logger.exception('Google Drive folder listing failed for queue %s '
                'and URL %s', self.rec_name, self.source_url)
            return documents, pages
        Model = Page if self.type == 'page' else Document
        existing = {record.filename for record in Model.search([
                    ('queue', '=', self.id),
                    ('filename', 'in', [file.path for file in drive_files]),
                    ])}
        for drive_file in drive_files:
            filename = drive_file.path
            if filename in existing:
                continue
            with BytesIO() as output:
                try:
                    gdown.download(id=drive_file.id, output=output)
                except DownloadError:
                    logger.exception('Google Drive download failed for %s',
                        drive_file.id)
                    continue
                data = output.getvalue()
            if not data:
                continue
            if self.type == 'page':
                pages.append(self.get_page(filename, data))
            else:
                documents.append(self.get_document(filename, data))
            existing.add(filename)
        return documents, pages

    @classmethod
    def cron_process(cls):
        'Process method to be used by cron. It is a separate method so '
        '"process()" is easier to override.'
        queues = cls.search([('scheduler', '=', True)])
        for queue in queues:
            with Transaction().set_context(queue_name='papyrus'):
                cls.__queue__.process([queue])


class Document(Workflow, ModelSQL, ModelView):
    'Papyrus Document'
    __name__ = 'papyrus.document'
    _rec_name = 'number'
    number = fields.Char('Number', required=True,
        states={
            'readonly': (Bool(Eval('pages'))),
        })
    queue = fields.Many2One('papyrus.queue', 'Queue', required=True,
        states={
            'readonly': (Bool(Eval('pages'))),
            })
    state = fields.Selection([
            ('pending', 'Pending'),
            ('inspected', 'Inspected'),
            ('processed', 'Processed'),
            ('cancelled', 'Cancelled'),
            ], 'State', required=True, readonly=True)
    reference = fields.Char('Reference')
    data = fields.Binary('Data', filename='filename')
    text = fields.Text('Text', readonly=True)
    boxes = fields.One2Many('papyrus.document.box', 'document', 'Boxes')
    filename = fields.Char('File Name')
    image = fields.Function(fields.Binary('Image', filename='image_filename'),
        'on_change_with_image')
    image_filename = fields.Function(fields.Char('Image Filename'),
            'on_change_with_image_filename')
    current_page = fields.Integer('Current Page', domain=[
            If(Bool(Eval('page_count')), [
                    ('current_page', '>=', 1),
                    ('current_page', '<=', Eval('page_count')),
                    ], []),
            ])
    page_count = fields.Function(fields.Integer('Page Count'), 'get_page_count')
    pages = fields.One2Many('papyrus.page', 'document', 'Pages', add_remove=[
            ('document', '=', None),
            ('queue', '=', Eval('queue')),
            ], order=[('sequence', 'ASC')],
        states={
            'readonly': (Eval('state') != 'processed'),
            'invisible': Bool(Eval('filename')),
            })
    company = fields.Many2One('company.company', "Company")
    document_company = fields.Function(fields.Many2One('company.company',
        'Document Company'),
        'on_change_with_document_company')
    origin = fields.Reference('Origin', selection='get_origin', readonly=True)
    employee = fields.Many2One('company.employee', 'Employee',
        domain=[
            ('company', '=', Eval('company', -1)),
            ])

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
                'reinspect': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-refresh',
                    'depends': ['state'],
                    },
                'process': {
                    'invisible': Eval('state') != 'inspected',
                    'icon': 'tryton-ok',
                    'depends': ['state'],
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

        # cache of already instantiated queues
        queues = {}
        vlist = vlist[:]
        for values in vlist:
            if values.get('number'):
                continue
            if not values.get('queue'):
                continue
            queue = queues.get(values['queue'], Queue(values['queue']))
            values['number'] = queue.document_sequence.get()
        return super().create(vlist)

    @classmethod
    def copy(cls, documents, default=None):
        # not call super
        if not documents:
            return []
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
                ('name', 'in', models),
                ])
        return [(None, '')] + [(m.name, m.string) for m in models]

    @fields.depends('current_page', 'page_count')
    def on_change_current_page(self):
        if not self.page_count:
            return
        if not self.current_page or self.current_page < 1:
            self.current_page = 1
        elif self.current_page > self.page_count:
            self.current_page = self.page_count

    @fields.depends('id', 'current_page', 'filename', 'pages')
    def on_change_with_image(self, name=None):
        if not self.filename and self.pages:
            page = (self.current_page or 1) - 1
            if 0 <= page < len(self.pages):
                return self.pages[page].image
        elif data := _binary_data(self):
            with tools.temporary_file(data) as path:
                return tools.page_image(path, self.current_page or 1)

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

    @fields.depends('company', 'queue')
    def on_change_with_document_company(self, name=None):
        return (
            (self.company and self.company.id) or
            (self.queue and self.queue.company and self.queue.company.id)
            )

    def get_page_count(self, name):
        if not self.filename and self.pages:
            return len(self.pages)
        if data := _binary_data(self):
            with tools.temporary_file(data) as path:
                return tools.page_count(path)

    def get_record(self):
        return

    def get_attachment(self, record):
        Attachment = Pool().get('ir.attachment')

        attachment = Attachment()
        attachment.name = '%s.pdf' % self.number
        attachment.resource = record
        attachment.type = 'data'
        attachment.data = self.data
        if self.text:
            # Take advantage of the fact that we already extracted the content
            attachment.content = self.text
            attachment.data_updated = False
            attachment.mimetype = 'application/pdf'
        return attachment

    def scan_text(self, path):
        text = tools.pdftotext(path)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_textboxes(self, path):
        Box = Pool().get('papyrus.document.box')
        boxes = tools.pdftoboxes(path, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_datamatrix(self, path):
        Box = Pool().get('papyrus.document.box')
        boxes = tools.datamatrix(path, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_tesseract(self, path):
        Box = Pool().get('papyrus.document.box')
        text, boxes = tools.tesseract(path, Box)
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
        engines = self.scan_engines()
        if engines and self.data:
            with tools.temporary_file(self.data) as path:
                for engine in engines:
                    getattr(self, 'scan_%s' % engine)(path)

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
            company_id = document.document_company and document.document_company.id or -1
            with Transaction().set_context(company=company_id):
                document.scan()
        cls.save(documents)

    @classmethod
    @ModelView.button
    def reinspect(cls, documents):
        DocumentBox = Pool().get('papyrus.document.box')

        boxes = []
        for document in documents:
            document.text = None
            boxes += list(document.boxes)
        if boxes:
            DocumentBox.delete(boxes)

        for document in documents:
            company_id = document.document_company and document.document_company.id or -1
            with Transaction().set_context(
                    company=company_id, papyrus_reinspect=True):
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
        if self.document and self.document.data:
            with tools.temporary_file(self.document.data) as path:
                for page in range(1, tools.page_count(path) + 1):
                    pages.append({
                        'page': tools.page_image(path, page, width=500),
                        'split': False,
                        })
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

    def do_split_document(self, action):
        Warning = Pool().get('res.user.warning')

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
                    pages='\n'.join(','.join(str(p + 1) for p in pages)
                        for pages in documents)))

        original = self.start.document
        reader = PdfReader(BytesIO(original.data))
        stem = Path(original.filename or original.number).stem
        new_document_ids = []
        for index, pages in enumerate(documents, 1):
            writer = PdfWriter()
            for page in pages:
                writer.add_page(reader.pages[page])
            with BytesIO() as output:
                writer.write(output)
                document = original.queue.get_document(
                    f'{stem}-{index}.pdf', output.getvalue())
            document.number = f'{original.number}-{index}'
            document.company = original.company
            document.save()
            new_document_ids.append(document.id)

        domain = [('id', 'in', new_document_ids)]
        action['pyson_search_value'] = PYSONEncoder().encode(domain)

        type(original).cancelled([original])

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
    data = fields.Binary('Data', filename='filename')
    text = fields.Text('Text', readonly=True)
    boxes = fields.One2Many('papyrus.page.box', 'page', 'Boxes', readonly=True)
    document = fields.Many2One('papyrus.document', 'Document',
        states={
            'readonly': (Eval('state') == 'processed'),
            # 'required': (Eval('state') == 'processed'),
            })
    queue = fields.Many2One('papyrus.queue', 'Queue', required=True,
        states={
            'readonly': (Bool(Eval('filename'))),
            })
    filename = fields.Char("File Name",
        states={
            'readonly': (Bool(Eval('filename'))),
            })
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

    @classmethod
    def __register__(cls, module_name):
        super().__register__(module_name)
        pool = Pool()
        Queue = pool.get('papyrus.queue')
        handler = Queue.__table_handler__(module_name)
        if handler.column_exist('storage_directory'):
            # The document and page tables now both have a Binary column.
            Document = pool.get('papyrus.document')
            _migrate_binary_data(Document, Queue)
            _migrate_binary_data(cls, Queue)
            cursor = Transaction().connection.cursor()
            document = Document.__table__()
            page = cls.__table__()
            queue = Queue.__table__()
            last_id = 0
            while True:
                cursor.execute(*document.join(queue, condition=document.queue == queue.id)
                    .select(document.id, queue.image_dpi,
                        where=(document.id > last_id) & (document.data == Null)
                        & ((document.filename == Null) | (document.filename == ''))
                        & Exists(page.select(page.id, where=page.document == document.id)),
                        order_by=document.id, limit=100))
                documents = cursor.fetchall()
                if not documents:
                    break
                for document_id, dpi in documents:
                    cursor.execute(*page.select(page.data,
                            where=page.document == document_id,
                            order_by=[page.sequence, page.id]))
                    data = tools.merge_pages((row[0] for row in cursor), dpi)
                    cursor.execute(*document.update(
                            [document.data], [Document.data.sql_format(data)],
                            where=document.id == document_id))
                last_id = documents[-1][0]
            handler.drop_column('storage_directory')

    @classmethod
    def _sync_document_data(cls, documents):
        documents.discard(None)
        if not documents:
            return
        Document = Pool().get('papyrus.document')
        with without_check_access():
            for document in documents:
                if document.filename:
                    continue
                pages = cls.search([('document', '=', document.id)],
                    order=[('sequence', 'ASC'), ('id', 'ASC')])
                data = None
                if pages:
                    data = tools.merge_pages(
                        (page.data for page in pages), document.queue.image_dpi)
                Document.write([document], {'data': data})

    @classmethod
    def create(cls, vlist):
        pages = super().create(vlist)
        cls._sync_document_data({page.document for page in pages})
        return pages

    @classmethod
    def write(cls, *args):
        actions = iter(args)
        changed = {page for pages, values in zip(actions, actions)
            if {'document', 'data', 'sequence'} & values.keys()
            for page in pages}
        documents = {page.document for page in changed}
        super().write(*args)
        documents.update(page.document
            for page in cls.browse([page.id for page in changed]))
        cls._sync_document_data(documents)

    @classmethod
    def delete(cls, pages):
        documents = {page.document for page in pages}
        super().delete(pages)
        cls._sync_document_data(documents)

    @staticmethod
    def default_state():
        return 'pending'

    @classmethod
    def copy(cls, pages, default=None):
        # not call super
        return

    @staticmethod
    def _get_origin():
        'Return list of Model names for origin Reference'
        return ['electronic.mail']

    @classmethod
    def get_origin(cls):
        IrModel = Pool().get('ir.model')
        models = cls._get_origin()
        models = IrModel.search([
                ('name', 'in', models),
                ])
        return [(None, '')] + [(m.name, m.string) for m in models]

    @fields.depends('id', 'filename')
    def on_change_with_image(self, name=None):
        if data := _binary_data(self):
            with tools.temporary_file(data) as path:
                return tools.page_image(path, 1)

    def scan_text(self, path):
        text = tools.pdftotext(path)
        if text:
            if self.text is None:
                self.text = ''
            elif self.text:
                self.text += '\n\n'
            self.text += text

    def scan_textboxes(self, path):
        Box = Pool().get('papyrus.page.box')
        boxes = tools.pdftoboxes(path, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_datamatrix(self, path):
        Box = Pool().get('papyrus.page.box')
        boxes = tools.datamatrix(path, Box)
        if self.boxes:
            self.boxes += tuple(boxes)
        else:
            self.boxes = boxes

    def scan_tesseract(self, path):
        Box = Pool().get('papyrus.page.box')
        text, boxes = tools.tesseract(path, Box)
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
        if self.data:
            with tools.temporary_file(self.data) as path:
                for engine in self.scan_engines():
                    getattr(self, 'scan_%s' % engine)(path)

    @staticmethod
    def get_prefixes():
        return []

    def get_document(self, previous):
        Document = Pool().get('papyrus.document')
        for box in self.boxes:
            if not box.text:
                continue
            if self.is_new_document(box):
                document = Document()
                document.queue = self.queue
                document.state = 'inspected'
                document.reference = box.text
                document.pages = (self,)
                return document
        if previous:
            previous.pages += (self,)
        return previous

    def is_new_document(self, box):
        for prefix in self.get_prefixes():
            if box.text.startswith(prefix):
                return True

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
        with Transaction().set_context(queue_name='papyrus'):
            cls.__queue__.inspect(pages)

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
            ('state', '=', 'inspected')
            ])
        with Transaction().set_context(queue_name='papyrus'):
            cls.__queue__.process(pages)


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
