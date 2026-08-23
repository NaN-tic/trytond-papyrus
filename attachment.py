import os

import trytond.config as config_
from trytond.filestore import filestore
from trytond.ir.attachment import store_prefix
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction


cache_directory = config_.get('papyrus', 'cache_directory')


class Attachment(metaclass=PoolMeta):
    'Attachment'
    __name__ = 'ir.attachment'

    @fields.depends('file_id', 'data')
    def get_full_path(self):
        Attachment = Pool().get('ir.attachment')
        if config_.get('database', 'class'):
            if not self.data:
                return
            assert cache_directory, (
                'Cache directory is required when FileStore is not the '
                'default one')
            dirname = os.path.join(cache_directory, 'ir.attachment')
            if not os.path.exists(dirname):
                os.makedirs(dirname, 0o770)
            with Transaction().set_context({'ir.attachment.data': None}):
                attachment = Attachment(self.id)
            path = os.path.join(dirname, str(self.id))
            with open(path, 'wb') as file_:
                file_.write(attachment.data)
        else:
            if not self.file_id:
                return
            prefix = store_prefix or Transaction().database.name
            path = filestore._filename(self.file_id, prefix)
        return path
