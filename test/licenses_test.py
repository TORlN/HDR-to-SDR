"""GPL compliance: the bundled ffmpeg's license terms must ship with the app."""
import os
import re
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestThirdPartyNotices(unittest.TestCase):
    def setUp(self):
        self.notices_path = os.path.join(_ROOT, 'THIRD_PARTY_NOTICES.md')

    def test_notices_file_exists(self):
        self.assertTrue(os.path.isfile(self.notices_path),
                        msg='THIRD_PARTY_NOTICES.md is required for GPL compliance')

    def test_notices_name_ffmpeg_and_revision(self):
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertIn('FFmpeg', text)
        self.assertRegex(text, r'c6bb22dea0',
                         msg='notices must cite the exact bundled revision')

    def test_notices_state_gpl_v2(self):
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertRegex(text, r'GPL\s*v?2|General Public License.*version 2')

    def test_notices_do_not_claim_gplv3(self):
        """ffmpeg -L reports v2-or-later; this build has no --enable-version3."""
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertNotRegex(text, r'GPLv3|GPL version 3')

    def test_notices_contain_written_source_offer(self):
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertRegex(text, r'(?i)source', msg='GPLv2 §3 requires a source offer')

    def test_notices_do_not_claim_false_attached_source(self):
        """The notice must not claim FFmpeg source is attached to releases if it isn't.
        GPLv2 §3 compliance depends on an accurate written offer, not false claims."""
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertNotRegex(text, r'Attached to the corresponding release',
                           msg='notices must not falsely claim source is attached to releases')

    def test_notices_written_offer_includes_email_contact(self):
        """GPLv2 §3(b) offer must be reachable by any third party without requiring
        an account or special access. Email is accessible to anyone."""
        text = open(self.notices_path, encoding='utf-8').read()
        self.assertRegex(text, r'hdrtosdr\.dev@outlook\.com',
                         msg='written offer must include email contact for GPLv2 §3 compliance')


class TestLicenseFiles(unittest.TestCase):
    def test_license_texts_present(self):
        for name in ('ffmpeg-LICENSE.md', 'COPYING.GPLv2',
                     'x264-COPYING', 'x265-COPYING'):
            path = os.path.join(_ROOT, 'licenses', name)
            self.assertTrue(os.path.isfile(path), msg=f'missing licenses/{name}')

    def test_gpl_text_is_the_real_thing(self):
        path = os.path.join(_ROOT, 'licenses', 'COPYING.GPLv2')
        text = open(path, encoding='utf-8', errors='replace').read()
        self.assertIn('GNU GENERAL PUBLIC LICENSE', text.upper())
        self.assertGreater(len(text), 10_000,
                           msg='COPYING.GPLv2 looks truncated')


class TestInstallerBundlesLicenses(unittest.TestCase):
    def test_installer_ships_licenses_dir(self):
        iss = open(os.path.join(_ROOT, 'installer.iss'), encoding='utf-8').read()
        self.assertIn('licenses', iss,
                      msg='installer.iss must bundle the licenses/ directory')

    def test_installer_ships_notices(self):
        iss = open(os.path.join(_ROOT, 'installer.iss'), encoding='utf-8').read()
        self.assertIn('THIRD_PARTY_NOTICES.md', iss)
