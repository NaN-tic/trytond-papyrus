# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.

from dominate.tags import div, img, section

from trytond.modules.html_report.dominate_report import DominateReport


class PapyrusReportMixin(DominateReport):
    prefix = ''
    side_margin = 0

    @classmethod
    def css(cls, action, data, records):
        return (
            '@page {\n'
            '  size: 30mm 32mm;\n'
            '  margin: 1mm;\n'
            '}\n'
            'body {\n'
            '  margin: 0;\n'
            '}\n'
            '.papyrus-page {\n'
            '  align-items: center;\n'
            '  box-sizing: border-box;\n'
            '  display: flex;\n'
            '  height: 30mm;\n'
            '  justify-content: center;\n'
            '  page-break-after: always;\n'
            '  width: 28mm;\n'
            '}\n'
            '.papyrus-page:last-child {\n'
            '  page-break-after: auto;\n'
            '}\n'
            '.papyrus-code {\n'
            '  display: block;\n'
            '  height: 25mm;\n'
            '  margin: 0 auto;\n'
            '  width: 25mm;\n'
            '}\n'
        )

    @classmethod
    def title(cls, action, data, records):
        return ''

    @classmethod
    def _qr_value(cls, record):
        return '%s%s' % (cls.prefix, record.raw.id)

    @classmethod
    def body(cls, action, data, records):
        container = div()
        for record in records:
            page = section(cls='papyrus-page')
            page.add(img(src=cls.qrcode(cls._qr_value(record)),
                    cls='papyrus-code'))
            container.add(page)
        return container

    @classmethod
    def header(cls, action, data, records):
        pass

    @classmethod
    def footer(cls, action, data, records):
        pass
