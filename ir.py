# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from trytond.pool import PoolMeta


class Cron(metaclass=PoolMeta):
    __name__ = 'ir.cron'

    @classmethod
    def __setup__(cls):
         super().__setup__()
         cls.method.selection += [
             ('papyrus.queue|cron_process', 'Papyrus Process Queue'),
             ('papyrus.queue|cron_clean', 'Papyrus Clean Files'),
             ('papyrus.document|cron_inspect',
                 'Papyrus Inspect Documents'),
             ('papyrus.document|cron_process',
                 'Papyrus Process Documents'),
             ('papyrus.page|cron_inspect', 'Papyrus Inspect Pages'),
             ('papyrus.page|cron_process', 'Papyrus Process Pages'),
             ]
