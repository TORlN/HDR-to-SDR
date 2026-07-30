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

    def test_readme_must_mention_hardware_fingerprint(self):
        """Network section must disclose the hardware fingerprint sent on activation.

        A privacy section that omits inconvenient facts (like what machine
        identifiers are transmitted) is worse than silence, because it converts
        a defensible position into a caught overstatement.
        """
        text = _read('README.md')
        self.assertIn('hardware fingerprint', text.lower(),
                      msg='Network section must mention hardware fingerprint sent with activation')

    def test_readme_must_not_claim_no_machine_data_sent(self):
        """The network section must not make an absolute no-machine-data claim.

        On Pro activation, get_hardware_fingerprint() (MAC, hostname, CPU arch,
        OS family) is sent to Lemon Squeezy. The README must not claim nothing
        about the machine is transmitted, or it will be caught as a lie.
        """
        text = _read('README.md')
        # The absolute claim that no machine data is sent must not appear.
        # It may be split across lines, so normalize whitespace.
        normalized = ' '.join(text.split())
        self.assertNotRegex(
            normalized,
            r'[Nn]othing about your.*?machine.*?is transmitted anywhere',
            msg='Must not claim zero machine data is sent (hardware fingerprint is sent on Pro activation)'
        )

    def test_readme_network_table_does_not_cite_empty_public_source(self):
        """Network table must not cite src/licensing.py as the Lemon Squeezy source.

        src/licensing.py in the public repo is a thin façade with no network code.
        The actual implementation is in src/pro/licensing.py (private). Pointing
        readers at a public file that contains nothing to verify invites the
        conclusion that the section is misleading.
        """
        text = _read('README.md')
        # Find the table and check if the Lemonsqueezy row cites src/licensing.py
        # as the source (which would be misleading since that file is just a façade).
        # We'll look for the pattern in the network section.
        lines = text.split('\n')
        in_network_section = False
        found_lemonsqueezy_row = False
        for i, line in enumerate(lines):
            if 'Network activity' in line:
                in_network_section = True
            if in_network_section and 'api.lemonsqueezy.com' in line:
                found_lemonsqueezy_row = True
                # This row should NOT cite 'src/licensing.py' as the source,
                # because that file is just a façade. It should either cite
                # pro/licensing.py (and note it's private) or acknowledge
                # the implementation is closed-source.
                # We'll accept any of: pro/licensing, closed, private, not public
                self.assertTrue(
                    'src/licensing.py' not in line or 'pro' in line or 'closed' in line.lower() or 'private' in line.lower(),
                    msg=f'Lemonsqueezy row at line {i+1} should not cite src/licensing.py alone (it\'s just a façade)'
                )
        self.assertTrue(
            found_lemonsqueezy_row,
            msg='Network activity table has no api.lemonsqueezy.com row -- the row this '
                'test is supposed to be checking is missing entirely'
        )
