"""Tests for settings persistence (load/save to JSON on disk)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.settings import load_settings, save_settings, DEFAULTS


class TestLoadSettings(unittest.TestCase):

    def test_returns_defaults_when_file_missing(self):
        with patch('src.settings.SETTINGS_FILE', '/nonexistent/path/settings.json'):
            result = load_settings()
        self.assertEqual(result, DEFAULTS)

    def test_returns_defaults_for_corrupt_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('not valid json {{{')
            tmp = f.name
        try:
            with patch('src.settings.SETTINGS_FILE', tmp):
                result = load_settings()
        finally:
            os.unlink(tmp)
        self.assertEqual(result, DEFAULTS)

    def test_returns_saved_values(self):
        data = {
            'gamma': 2.2, 'filter': 'Static', 'tonemapper': 'Hable',
            'gpu_accel': True, 'open_after_conversion': True, 'display_preview': False,
            'quality': 19,
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            tmp = f.name
        try:
            with patch('src.settings.SETTINGS_FILE', tmp):
                result = load_settings()
        finally:
            os.unlink(tmp)
        self.assertEqual(result, data)

    def test_partial_file_fills_missing_keys_with_defaults(self):
        data = {'gamma': 2.0}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            tmp = f.name
        try:
            with patch('src.settings.SETTINGS_FILE', tmp):
                result = load_settings()
        finally:
            os.unlink(tmp)
        self.assertEqual(result['gamma'], 2.0)
        self.assertEqual(result['filter'], DEFAULTS['filter'])
        self.assertEqual(result['tonemapper'], DEFAULTS['tonemapper'])
        self.assertEqual(result['gpu_accel'], DEFAULTS['gpu_accel'])
        self.assertEqual(result['open_after_conversion'], DEFAULTS['open_after_conversion'])

    def test_unknown_keys_are_ignored(self):
        data = {
            'gamma': 1.5, 'filter': 'Static', 'tonemapper': 'Reinhard',
            'gpu_accel': False, 'open_after_conversion': True, 'display_preview': True,
            'unknown_key': 'should be dropped',
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            tmp = f.name
        try:
            with patch('src.settings.SETTINGS_FILE', tmp):
                result = load_settings()
        finally:
            os.unlink(tmp)
        self.assertNotIn('unknown_key', result)
        self.assertEqual(result['gamma'], 1.5)


class TestSaveSettings(unittest.TestCase):

    def test_creates_directory_and_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, 'subdir', 'settings.json')
            with patch('src.settings.SETTINGS_FILE', test_file):
                save_settings({
                    'gamma': 2.2, 'filter': 'Static', 'tonemapper': 'Hable',
                    'gpu_accel': True, 'open_after_conversion': False, 'display_preview': True,
                })
            self.assertTrue(os.path.exists(test_file))
            with open(test_file, encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data['gamma'], 2.2)
            self.assertEqual(data['filter'], 'Static')

    def test_oserror_on_mkdir_does_not_raise(self):
        with patch('src.settings.os.makedirs', side_effect=OSError('no permission')):
            save_settings({'gamma': 1.0})  # must not propagate the OSError

    def test_non_serializable_value_does_not_raise(self):
        """Mock objects (from test teardowns) must not propagate TypeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, 'settings.json')
            with patch('src.settings.SETTINGS_FILE', test_file):
                save_settings({'gamma': object()})  # object() is not JSON serializable

    def test_round_trip_preserves_all_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, 'settings.json')
            with patch('src.settings.SETTINGS_FILE', test_file):
                save_settings(dict(DEFAULTS))
                result = load_settings()
        self.assertEqual(result, DEFAULTS)

    def test_only_known_keys_are_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, 'settings.json')
            settings_with_extra = {**DEFAULTS, 'rogue_key': 'drop me'}
            with patch('src.settings.SETTINGS_FILE', test_file):
                save_settings(settings_with_extra)
            with open(test_file, encoding='utf-8') as f:
                written = json.load(f)
        self.assertNotIn('rogue_key', written)
        self.assertEqual(set(written.keys()), set(DEFAULTS.keys()))


if __name__ == '__main__':
    unittest.main()
