import unittest

from trytond.pool import Pool
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules
from trytond.transaction import Transaction


class TestOfficeMenu(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):
        config = activate_modules('papyrus')

        with Transaction().start(
                config.database_name, config.user,
                context=config.context):
            pool = Pool(config.database_name)
            ModelData = pool.get('ir.model.data')
            Action = pool.get('ir.action.act_window')
            Menu = pool.get('ir.ui.menu')
            Group = pool.get('res.group')

            office = Menu(ModelData.get_id('office', 'menu_office'))
            configuration = Menu(ModelData.get_id(
                    'office', 'menu_configuration'))
            categories = Menu(ModelData.get_id(
                    'office', 'menu_category_tree'))
            documents = Menu(ModelData.get_id(
                    'papyrus', 'menu_papyrus_document'))
            pages = Menu(ModelData.get_id('papyrus', 'menu_papyrus_page'))
            queue = Menu(ModelData.get_id('papyrus', 'menu_papyrus_queue'))
            queue_action = Action(ModelData.get_id(
                    'papyrus', 'act_papyrus_queue'))
            office_group = Group(ModelData.get_id('office', 'group_office'))
            papyrus_group = Group(ModelData.get_id(
                    'papyrus', 'group_papyrus'))
            papyrus_admin = Group(ModelData.get_id(
                    'papyrus', 'group_papyrus_admin'))

            self.assertEqual(documents.parent, office)
            self.assertEqual(categories.parent, office)
            self.assertGreater(documents.sequence, categories.sequence)
            self.assertEqual(documents.name, 'Papyrus Documents')
            self.assertEqual(documents.icon, 'papyrus')
            self.assertEqual(
                {group.id for group in documents.groups},
                {papyrus_group.id, papyrus_admin.id})
            self.assertEqual(pages.parent, documents)
            self.assertEqual(queue.parent, configuration)
            self.assertEqual(queue.name, 'Queues')
            self.assertEqual(queue_action.name, 'Document Queues')
            self.assertFalse(ModelData.search([
                        ('module', '=', 'papyrus'),
                        ('fs_id', '=', 'menu_papyrus'),
                        ]))
            self.assertFalse(ModelData.search([
                        ('module', '=', 'papyrus'),
                        ('fs_id', '=', 'menu_papyrus_configuration'),
                        ]))
            self.assertEqual(papyrus_group.parent, office_group)
            self.assertEqual(papyrus_admin.parent, papyrus_group)
