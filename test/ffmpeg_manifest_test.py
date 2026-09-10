"""Release builds accept only the pinned FFmpeg input files."""

import copy
import io
import os
import unittest

from tools.verify_ffmpeg_manifest import main, verify_manifest
from tools.release_provenance import collect_manifest


_CONTENTS = {
    'src/ffmpeg.exe': b'ffmpeg binary',
    'src/ffprobe.exe': b'ffprobe binary',
    'tools/ffmpeg-patches/ffmpeg_extra.sh': b'patch hook',
}

_VALID_MANIFEST = {
    'schema_version': 1,
    'build': {
        'ffmpeg_source': 'https://git.ffmpeg.org/ffmpeg.git',
        'ffmpeg_revision': 'b32f8d1c2377079302d23f82d555d13deda68c57',
        'mabs_source': 'https://github.com/m-ab-s/media-autobuild_suite',
        'mabs_revision': 'snapshot-sha256:0123456789abcdef',
        'configuration': '--disable-autodetect --enable-gpl',
        'patch_revision': '5b3bf1938ce419c3853774c1ff81baa0b1dc8417',
    },
    'files': [
        {
            'path': 'src/ffmpeg.exe',
            'size': 13,
            'sha256': 'd85cf8deeb08a8301e7326462d14b2ae4aadeca6dc03d9f5bfd00b8ff5108a40',
        },
        {
            'path': 'src/ffprobe.exe',
            'size': 14,
            'sha256': 'b180cc1238e3ba602e074d21c4410de1209b632aa091297986dca070a240ee29',
        },
        {
            'path': 'tools/ffmpeg-patches/ffmpeg_extra.sh',
            'size': 10,
            'sha256': 'c4186920553a7e926a672e287923a382eeb11e3117bc753bc1d297c879d09aee',
        },
    ],
}

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _open_fixture(path, mode='rb'):
    if mode != 'rb':
        raise AssertionError('the verifier must open pinned files as binary')
    normalized = os.path.relpath(os.fspath(path), 'C:/repo').replace('\\', '/')
    if normalized not in _CONTENTS:
        raise FileNotFoundError(normalized)
    return io.BytesIO(_CONTENTS[normalized])


class TestFfmpegManifest(unittest.TestCase):

    def test_valid_manifest_accepts_the_exact_pinned_bytes(self):
        verify_manifest('C:/repo', _VALID_MANIFEST, open_file=_open_fixture)

    def test_rejects_a_digest_mismatch(self):
        manifest = copy.deepcopy(_VALID_MANIFEST)
        manifest['files'][0]['sha256'] = '0' * 64

        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch.*src/ffmpeg.exe'):
            verify_manifest('C:/repo', manifest, open_file=_open_fixture)

    def test_rejects_crlf_in_the_shell_patch_before_accepting_its_manifest_entry(self):
        """A Windows line-ending conversion must stop the MABS patch hook.

        Removing the line-ending validation would let a CRLF shell script
        reach the Linux-like MABS environment, where it can fail unclearly.
        """
        manifest = copy.deepcopy(_VALID_MANIFEST)
        manifest['files'][2]['size'] = 12

        def crlf_patch(path, mode='rb'):
            normalized = os.path.relpath(os.fspath(path), 'C:/repo').replace('\\', '/')
            if normalized == 'tools/ffmpeg-patches/ffmpeg_extra.sh':
                return io.BytesIO(b'patch hook\r\n')
            return _open_fixture(path, mode)

        with self.assertRaisesRegex(ValueError, 'LF line endings'):
            verify_manifest('C:/repo', manifest, open_file=crlf_patch)

    def test_rejects_a_size_mismatch(self):
        manifest = copy.deepcopy(_VALID_MANIFEST)
        manifest['files'][1]['size'] = 999

        with self.assertRaisesRegex(ValueError, 'size mismatch.*src/ffprobe.exe'):
            verify_manifest('C:/repo', manifest, open_file=_open_fixture)

    def test_rejects_missing_or_unexpected_pinned_paths(self):
        missing = copy.deepcopy(_VALID_MANIFEST)
        missing['files'].pop()
        unexpected = copy.deepcopy(_VALID_MANIFEST)
        unexpected['files'].append({
            'path': 'src/other.exe',
            'size': 1,
            'sha256': '0' * 64,
        })

        with self.assertRaisesRegex(ValueError, 'pinned paths'):
            verify_manifest('C:/repo', missing, open_file=_open_fixture)
        with self.assertRaisesRegex(ValueError, 'pinned paths'):
            verify_manifest('C:/repo', unexpected, open_file=_open_fixture)

    def test_rejects_incomplete_build_provenance(self):
        manifest = copy.deepcopy(_VALID_MANIFEST)
        del manifest['build']['patch_revision']

        with self.assertRaisesRegex(ValueError, 'build provenance'):
            verify_manifest('C:/repo', manifest, open_file=_open_fixture)

    def test_rejects_a_missing_pinned_file(self):
        def missing_ffprobe(path, mode='rb'):
            if os.fspath(path).replace('\\', '/').endswith('src/ffprobe.exe'):
                raise FileNotFoundError('ffprobe is absent')
            return _open_fixture(path, mode)

        with self.assertRaisesRegex(ValueError, 'missing.*src/ffprobe.exe'):
            verify_manifest('C:/repo', _VALID_MANIFEST, open_file=missing_ffprobe)

    def test_main_reports_success_without_launching_external_tools(self):
        output = io.StringIO()

        result = main(
            'C:/repo',
            manifest=_VALID_MANIFEST,
            open_file=_open_fixture,
            output=output,
        )

        self.assertEqual(result, 0)
        self.assertIn('verified', output.getvalue().lower())

    def test_main_returns_failure_for_unapproved_bytes(self):
        manifest = copy.deepcopy(_VALID_MANIFEST)
        manifest['files'][0]['sha256'] = '0' * 64
        output = io.StringIO()

        result = main(
            'C:/repo',
            manifest=manifest,
            open_file=_open_fixture,
            output=output,
        )

        self.assertEqual(result, 1)
        self.assertIn('SHA-256 mismatch', output.getvalue())


class TestReleaseBinaryTracking(unittest.TestCase):

    def test_release_executables_are_tracked_through_git_lfs(self):
        attributes_path = os.path.join(_ROOT, '.gitattributes')
        self.assertTrue(os.path.isfile(attributes_path), '.gitattributes is missing')
        with open(attributes_path, encoding='utf-8') as handle:
            attributes = handle.read()
        for path in ('src/ffmpeg.exe', 'src/ffprobe.exe'):
            self.assertIn(
                f'{path} filter=lfs diff=lfs merge=lfs -text',
                attributes,
                f'{path} is not pinned through Git LFS',
            )

    def test_release_executables_override_the_general_exe_ignore(self):
        with open(os.path.join(_ROOT, '.gitignore'), encoding='utf-8') as handle:
            ignore = handle.read()
        general_rule = ignore.find('*.exe')
        for path in ('src/ffmpeg.exe', 'src/ffprobe.exe'):
            exception = ignore.find(f'!{path}')
            self.assertGreater(
                exception,
                general_rule,
                f'{path} remains hidden by the general executable ignore rule',
            )


class TestReleaseProvenance(unittest.TestCase):

    def test_normal_release_records_paired_commits_and_artifact_hashes(self):
        """A normal installer must retain its exact paired source identities.

        Removing either commit capture, changing the installer hash, or
        omitting pinned build inputs must make this release record incomplete.
        """
        contents = {
            'requirements.txt': b'Pillow==11.0.0\n',
            'requirements-dev.txt': b'pyinstaller==6.21.0\n',
            'src/ffmpeg.exe': b'ffmpeg',
            'src/ffprobe.exe': b'ffprobe',
            'installer_output/HDR_to_SDR_Setup.exe': b'installer',
        }

        def open_file(path, mode='rb'):
            relative = os.path.relpath(os.fspath(path), 'C:/repo').replace('\\', '/')
            if mode != 'rb' or relative not in contents:
                raise FileNotFoundError(relative)
            return io.BytesIO(contents[relative])

        def run(command, **_kwargs):
            root = command[2]
            if command[3:] == ['status', '--porcelain']:
                return type('Result', (), {'returncode': 0, 'stdout': ''})()
            if command[3:] == ['rev-parse', 'HEAD']:
                commit = 'public-commit' if root == 'C:/repo' else 'private-commit'
                return type('Result', (), {'returncode': 0, 'stdout': commit + '\n'})()
            self.fail('unexpected git command: %r' % (command,))

        manifest = collect_manifest(
            'C:/repo',
            'C:/repo/installer_output/HDR_to_SDR_Setup.exe',
            run=run,
            open_file=open_file,
            python_version='3.13.14',
            tool_versions={'pyinstaller': '6.21.0', 'pyarmor': None},
        )

        self.assertEqual(manifest['repositories'], {
            'public': 'public-commit', 'pro': 'private-commit',
        })
        self.assertFalse(manifest['free_only'])
        self.assertEqual(manifest['python_version'], '3.13.14')
        self.assertEqual(manifest['tool_versions']['pyinstaller'], '6.21.0')
        self.assertEqual(
            manifest['artifacts']['installer']['sha256'],
            '9c0d294c05fc1d88d698034609bb81c0c69196327594e4c69d2915c80fd9850c',
        )

    def test_dirty_public_source_blocks_provenance(self):
        """A release record must reject source changes not tied to a commit."""
        def run(command, **_kwargs):
            self.assertEqual(command[3:], ['status', '--porcelain'])
            return type('Result', (), {'returncode': 0, 'stdout': ' M src/gui.py\n'})()

        with self.assertRaisesRegex(ValueError, 'public repository is dirty'):
            collect_manifest('C:/repo', 'C:/repo/installer.exe', run=run)

    def test_free_only_fallback_records_no_private_commit(self):
        """The emergency fallback must be visibly distinct from a normal release."""
        contents = {
            'requirements.txt': b'',
            'requirements-dev.txt': b'',
            'src/ffmpeg.exe': b'',
            'src/ffprobe.exe': b'',
            'installer_output/HDR_to_SDR_Setup_FREE.exe': b'',
        }

        def open_file(path, mode='rb'):
            relative = os.path.relpath(os.fspath(path), 'C:/repo').replace('\\', '/')
            return io.BytesIO(contents[relative])

        def run(command, **_kwargs):
            if command[3:] == ['status', '--porcelain']:
                return type('Result', (), {'returncode': 0, 'stdout': ''})()
            if command[3:] == ['rev-parse', 'HEAD']:
                return type('Result', (), {'returncode': 0, 'stdout': 'public-commit\n'})()
            self.fail('unexpected git command: %r' % (command,))

        manifest = collect_manifest(
            'C:/repo',
            'C:/repo/installer_output/HDR_to_SDR_Setup_FREE.exe',
            free_only=True,
            run=run,
            open_file=open_file,
            python_version='3.13.14',
            tool_versions={},
        )

        self.assertTrue(manifest['free_only'])
        self.assertEqual(manifest['repositories'], {'public': 'public-commit'})


if __name__ == '__main__':
    unittest.main()
