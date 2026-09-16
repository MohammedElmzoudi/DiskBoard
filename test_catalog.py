import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import diskpick_catalog as catalog
import diskpick_cli as cli


class CatalogTests(unittest.TestCase):
    def area(self,**kw):
        return dict(id='test',title='Test area',description='Example area',kind='inspect',roots=[],**kw)

    def validate(self,areas):
        return catalog.validate(dict(version=1,areas=areas))

    def test_mixed_input_preserves_order_deduplicates(self):
        self.assertEqual(catalog.parse_selection('1 4, 2,3 4',9),[1,4,2,3])

    def test_invalid_selection_rejects_entire_input(self):
        for value in ['1 99','0','-1','1; rm','',',','1.5','all']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                catalog.parse_selection(value,5)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):self.validate([self.area(),self.area()])

    def test_arbitrary_commands_rejected(self):
        area=self.area();area['command']='rm -rf /'
        with self.assertRaises(ValueError):self.validate([area])

    def test_unknown_handler_rejected(self):
        area=self.area();area['kind']='shell'
        with self.assertRaises(ValueError):self.validate([area])

    def test_rust_non_target_root_rejected(self):
        area=self.area();area.update(kind='rust',roots=['~/Documents'])
        with self.assertRaises(ValueError):self.validate([area])

    def test_browser_custom_root_rejected(self):
        area=self.area();area.update(kind='browser',roots=['~/Documents'],cache='Cache')
        with self.assertRaises(ValueError):self.validate([area])

    def test_traversal_rejected(self):
        area=self.area();area['roots']=['~/Documents/../Desktop']
        with self.assertRaises(ValueError):self.validate([area])

    def test_terminal_control_sequence_rejected(self):
        area=self.area();area['title']='hello\033[2J'
        with self.assertRaises(ValueError):self.validate([area])

    def test_read_only_handler_never_calls_cleaner(self):
        cleaner=mock.Mock()
        catalog.execute_handler(self.area(),cleaner)
        self.assertEqual(cleaner.mock_calls,[])

    def test_noninteractive_cleanup_needs_yes(self):
        with mock.patch.object(catalog,'load',return_value=[self.area()]):
            with mock.patch.object(cli,'clean') as clean,self.assertRaises(SystemExit):
                cli.main(['clean','--areas','test'])
            clean.assert_not_called()

    def test_unknown_id_rejects_whole_cleanup(self):
        with mock.patch.object(catalog,'load',return_value=[self.area()]):
            with mock.patch.object(cli,'clean') as clean,self.assertRaises(SystemExit):
                cli.main(['clean','--areas','test,unknown','--yes'])
            clean.assert_not_called()

    def test_noninteractive_stable_ids_preserve_order(self):
        a=self.area();b=self.area();b['id']='second'
        with mock.patch.object(catalog,'load',return_value=[a,b]):
            with mock.patch.object(cli,'clean',return_value={}) as clean:
                cli.main(['clean','--areas','second,test,second','--yes','--json'])
            self.assertEqual(clean.call_args.args[0],[b,a])

    def test_demo_rejects_real_cleanup_options(self):
        with mock.patch.object(cli,'clean') as clean,self.assertRaises(SystemExit):
            cli.main(['--demo','--yes'])
        clean.assert_not_called()

    def test_clean_selected_browser_area_with_audit_preserves_other_cache(self):
        import diskpick_engine as engine
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            profiles=root/'profiles'
            profile=profiles/'mcp-chrome-fixture'
            for name in ['Cache','Code Cache']:
                p=profile/'Default'/name
                p.mkdir(parents=True)
                (p/'data').write_text('disposable')
                os.utime(p/'data',(time.time()-172800,)*2)
                os.utime(p,(time.time()-172800,)*2)
            (profile/'Default/Cookies').write_text('KEEP')
            area=dict(id='http',title='HTTP',description='HTTP cache',kind='browser',cache='Cache')
            # Use actual descriptor traversal, deletion, journal and singleton lock.
            with mock.patch.object(cli,'STATE',root/'audit'),mock.patch.object(engine,'PROFILES',profiles):
                with mock.patch.object(engine,'idle'),mock.patch.object(catalog,'execute_handler') as execute:
                    execute.side_effect=lambda a,c:c.browsers(profiles,cache_names=(a['cache'],))
                    result=cli.clean([area])
            self.assertEqual(result['results'][0]['completed'],1)
            self.assertEqual(list((profile/'Default/Cache').iterdir()),[])
            self.assertTrue((profile/'Default/Code Cache/data').exists())
            self.assertEqual((profile/'Default/Cookies').read_text(),'KEEP')
            events=[json.loads(line)['event'] for line in Path(result['audit']).read_text().splitlines()]
            self.assertLess(events.index('intent'),events.index('completed'))


if __name__=='__main__':unittest.main()
