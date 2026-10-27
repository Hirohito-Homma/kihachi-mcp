import unittest, tempfile, json
from pathlib import Path
from unittest.mock import patch
import server
class Guards(unittest.TestCase):
    def test_invalid_prompt(self):
        for value in ['',None,'x'*2001]:
            with self.assertRaises(ValueError): server.action('/api/compose',{'prompt':value})
    def test_path_traversal(self):
        with self.assertRaises(ValueError): server.project('../escape')
    def test_changed_live_blocks_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'baseline.json').write_text('{}')
            with patch.object(server,'project',return_value=p),patch.object(server,'live_state',return_value={'is_playing':False,'tracks':[],'tempo':120,'signature':[4,4]}),patch.object(server,'run') as run:
                with self.assertRaisesRegex(ValueError,'状態が変わり'): server.action('/api/apply',{'id':'a'*32})
                run.assert_not_called()
    def test_duplicate_blocks_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); state={'is_playing':False,'tracks':[],'tempo':120,'signature':[4,4]}
            (p/'baseline.json').write_text(json.dumps(server.baseline(state)))
            (p/'attempt.json').write_text('{}')
            with patch.object(server,'project',return_value=p),patch.object(server,'live_state',return_value=state),patch.object(server,'run') as run:
                with self.assertRaises(FileExistsError):server.action('/api/apply',{'id':'a'*32})
                run.assert_not_called()
if __name__=='__main__': unittest.main()
