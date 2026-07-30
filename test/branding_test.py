"""Branding reservation and the network-activity disclosure must stay present."""
import os
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _read(*parts):
    return open(os.path.join(_ROOT, *parts), encoding='utf-8').read()


class TestBrandingReservation(unittest.TestCase):
    def test_trademark_file_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, 'TRADEMARK.md')))

    def test_logo_notice_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(_ROOT, 'logo', 'NOTICE.md')))

    def test_trademark_excludes_itself_from_mit(self):
        text = _read('TRADEMARK.md')
        self.assertIn('MIT', text)
        self.assertRegex(text, r'(?i)not.*(covered|granted|included)')

    def test_readme_asks_forks_to_rename(self):
        text = _read('README.md')
        self.assertRegex(text, r'(?i)fork')
        self.assertRegex(text, r'(?i)rename|different name|own name')


class TestNetworkDisclosure(unittest.TestCase):
    def test_readme_documents_both_endpoints(self):
        text = _read('README.md')
        self.assertIn('api.github.com', text)
        self.assertIn('api.lemonsqueezy.com', text)

    def test_readme_states_no_telemetry(self):
        text = _read('README.md')
        self.assertRegex(text, r'(?i)no (analytics|telemetry|tracking)')

    def test_claim_is_true_no_other_network_callers(self):
        """The README claim must stay honest: only two modules may open sockets.

        If a new module starts making requests, this fails and the README has
        to be updated rather than quietly becoming false.
        """
        allowed = {'updater.py', 'licensing.py'}
        offenders = []
        src = os.path.join(_ROOT, 'src')
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames
                           if d not in ('__pycache__', 'pro', 'luts')]
            for name in filenames:
                if not name.endswith(('.py', '.pyw')) or name in allowed:
                    continue
                body = open(os.path.join(dirpath, name),
                            encoding='utf-8', errors='replace').read()
                if 'urllib.request' in body or 'http.client' in body \
                        or 'requests.' in body:
                    offenders.append(name)
        self.assertEqual([], offenders,
                         msg=f'new network callers not disclosed in README: {offenders}')
