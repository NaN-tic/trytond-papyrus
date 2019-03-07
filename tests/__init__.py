# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
try:
    from trytond.modules.papyrus.tests.test_papyrus import suite
except ImportError:
    from .test_papyrus import suite

__all__ = ['suite']
