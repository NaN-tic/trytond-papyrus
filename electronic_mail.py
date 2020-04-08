from trytond.model import fields
from trytond.pool import PoolMeta


class ElectronicMail(metaclass=PoolMeta):
    __name__ = 'electronic.mail'

    documents = fields.One2Many('papyrus.document', 'origin', 'Documents')
