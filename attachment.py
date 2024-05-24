import os
from trytond.model import fields
from trytond.wizard import Wizard, StateAction
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction
from trytond.config import config
from trytond.filestore import filestore
from trytond.pyson import PYSONEncoder
from trytond.ir.attachment import store_prefix
from sql.functions import Substring, Position


# We need a cache when files are stored in a cloud filestore
cache_directory = config.get('papyrus', 'cache_directory')


class Attachment(metaclass=PoolMeta):
    "Attachment"
    __name__ = 'ir.attachment'
    content = fields.Text('Content', readonly=True)

    @fields.depends('file_id', 'data')
    def get_full_path(self):
        pool = Pool()
        Attachment = pool.get('ir.attachment')
        if config.get('database', 'class'):
            if not self.data:
                return
            # TODO: Remove cache periodically
            # Cache objects when they're stored in a cloud FileStore
            assert cache_directory, ('Cache directory is required when '
                'FileStore is not the default one')
            dirname = os.path.join(cache_directory, 'ir.attachment')
            if not os.path.exists(dirname):
                os.makedirs(dirname, 0o770)
            with Transaction().set_context({'ir.attachment.data': None}):
                attachment = Attachment(self.id)
            path = os.path.join(dirname, str(self.id))
            with open(path, 'wb') as f:
                f.write(attachment.data)
        else:
            if not self.file_id:
                return
            prefix = store_prefix
            if prefix is None:
                prefix = Transaction().database.name
            path = filestore._filename(self.file_id, prefix)
        return path


class PapyrusAttachment(Wizard):
    "Papyrus Attachment"
    __name__ = 'papyrus.attachment'
    start_state = 'open_'
    open_ = StateAction('papyrus.act_attachment_form')

    def do_open_(self, action):

        def convert_resource(domain, model):
            if not domain:
                return []
            if domain[0] not in ('AND', 'OR') and not isinstance(domain[0], (list, tuple)):
                domain[0] = 'resource.%s' % domain[0]
                domain.append(model)
                return domain

            new_domain = []
            for item in domain:
                if isinstance(item, tuple):
                    item = list(item)
                if isinstance(item, list):
                    item = convert_resource(item, model)
                new_domain.append(item)
            return new_domain

        pool = Pool()
        ModelAccess = pool.get('ir.model.access')
        Model = pool.get('ir.model')
        Rule = pool.get('ir.rule')
        Attachment = pool.get('ir.attachment')
        attachment = Attachment.__table__()

        query = attachment.select(
            Substring(
                attachment.resource, 0, Position(',', attachment.resource)),
            distinct=True)
        models = Model.search([('model', 'in', query)])
        access = ModelAccess.get_access([m.model for m in models])

        domain = ['OR']
        for model in models:
            if access[model.model]['read']:
                with Transaction().set_context(_check_access=True):
                    domain_get = Rule.domain_get(model.model)
                if domain_get:
                    domain.append(convert_resource(domain_get, model.model))
                else:
                    domain.append(('resource', 'like',  model.model+',%'))

        action['pyson_domain'] = PYSONEncoder().encode(domain)
        return action, {}

    def transition_open_(self):
        return 'end'
