import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from review.storage import write_json


class StorageTests(unittest.TestCase):
    def test_recovers_from_windows_sharing_lock(self):
        original = Path.replace
        attempts = []
        def locked_once(source, target):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError('simulated sharing lock')
            return original(source, target)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'status.json'
            target.write_text('{}', encoding='utf-8')
            with patch.object(Path, 'replace', locked_once), patch('review.storage.time.sleep'):
                write_json(target, {'state': 'running'})
            self.assertEqual(json.loads(target.read_text()), {'state': 'running'})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])
            self.assertEqual(len(attempts), 2)
