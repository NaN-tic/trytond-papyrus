import os
from trytond.model import ModelView, fields
from trytond.pool import PoolMeta
from trytond.pyson import Eval, If, Bool
from trytond.config import config
from trytond.filestore import filestore
from trytond.ir.attachment import store_prefix
from . import tools

__all__ = ['Attachment']


# We need a cache when files are stored in a cloud filestore
cache_directory = config.get('papyrus', 'cache_directory')
if config.get('database', 'class'):
    assert cache_directory, ('Cache directory is required when FileStore is '
        'not the default one')


class Attachment(metaclass=PoolMeta):
    "Attachment"
    __name__ = 'ir.attachment'
    image = fields.Function(fields.Binary('Image'), 'on_change_with_image')
    current_page = fields.Integer('Current Page', domain=[
            If(Bool(Eval('page_count')), [
                    ('current_page', '>=', 1),
                    ('current_page', '<=', Eval('page_count')),
                    ], []),
            ], depends=['page_count'])
    page_count = fields.Function(fields.Integer('Page Count'), 'get_page_count')

    @classmethod
    def __setup__(cls):
        super(Attachment, cls).__setup__()
        cls._buttons.update({
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

    def get_full_path(self):
        if config.get('database', 'class'):
            # TODO: Remove cache periodically
            # Cache objects when they're stored in a cloud FileStore
            dirname = os.path.join(cache_directory, 'ir.attachment')
            if not os.path.exists(dirname):
                os.makedirs(dirname, 0o770)
            path = os.path.join(dirname, str(self.id))
            with open(path, 'w') as f:
                f.write(self.data)
        else:
            path = filestore._filename(self.file_id, store_prefix)
        return path

    def get_page_count(self, name):
        return tools.page_count(self.get_full_path())

    @fields.depends('current_page')
    def on_change_with_image(self, name=None):
        return tools.page_image(self.get_full_path(), self.current_page or 1)

    @ModelView.button_change('current_page')
    def previous_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page and self.current_page > 1:
            self.current_page -= 1
        self.image = self.on_change_with_image()

    @ModelView.button_change('current_page', 'page_count')
    def next_page(self):
        # TODO: Check why it doesn't work. Seems to be a GTK client issue
        if self.current_page and self.current_page < self.page_count:
            self.current_page += 1
        self.image = self.on_change_with_image()

    @fields.depends('current_page', 'page_count')
    def on_change_current_page(self):
        if not self.page_count:
            return
        if not self.current_page or self.current_page < 1:
            self.current_page = 1
        elif self.current_page > self.page_count:
            self.current_page = self.page_count
