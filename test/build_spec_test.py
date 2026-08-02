"""HDR_to_SDR_Converter.spec must stay relocatable.

The spec is the PyInstaller input for the PyArmor/obfuscated release branch of
build_installer.bat. It was untracked for most of the project's history and
accumulated absolute paths baked to one developer's checkout
('C:\\Users\\<name>\\Desktop\\HDR to SDR\\...'), which build from that machine
and nowhere else.

Rather than assert the referenced files exist -- ffmpeg.exe, the venv's
site-packages and the icon are all gitignored, so they are absent on CI -- these
tests execute the spec with stub PyInstaller globals and a sentinel SPECPATH,
then assert every path it produces lands under that sentinel. That catches an
absolute path and a mis-joined relative one, and stays hermetic.
"""
import os
import re
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SPEC_PATH = os.path.join(_ROOT, 'HDR_to_SDR_Converter.spec')

_SENTINEL = os.path.join(os.sep + 'sentinel_root', 'checkout')


class _StubAnalysis:
    """Stands in for PyInstaller's Analysis, capturing what the spec passed."""

    def __init__(self, scripts, **kwargs):
        self.scripts = scripts
        self.kwargs = kwargs
        self.binaries = kwargs.get('binaries') or []
        self.datas = kwargs.get('datas') or []
        self.pure = []


class _StubBuildStep:
    """Stands in for PYZ/EXE/COLLECT, which the spec only chains together."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _exec_spec(specpath):
    """Run the spec with stubbed PyInstaller globals; return the captured objects."""
    captured = {}

    def _make(name, cls):
        def _factory(*args, **kwargs):
            obj = cls(*args, **kwargs)
            captured[name] = obj
            return obj
        return _factory

    namespace = {
        'Analysis': _make('analysis', _StubAnalysis),
        'PYZ': _make('pyz', _StubBuildStep),
        'EXE': _make('exe', _StubBuildStep),
        'COLLECT': _make('collect', _StubBuildStep),
        'SPECPATH': specpath,
        '__file__': os.path.join(specpath, 'HDR_to_SDR_Converter.spec'),
    }
    with open(_SPEC_PATH, encoding='utf-8') as f:
        source = f.read()
    exec(compile(source, _SPEC_PATH, 'exec'), namespace)
    return captured


def _source_paths(captured):
    """Every filesystem path the spec feeds to PyInstaller, as (label, path)."""
    analysis = captured['analysis']
    pairs = [('scripts[%d]' % i, p) for i, p in enumerate(analysis.scripts)]
    for label, entries in (('binaries', analysis.binaries), ('datas', analysis.datas)):
        for i, entry in enumerate(entries):
            pairs.append(('%s[%d]' % (label, i), entry[0]))
    icon = captured['exe'].kwargs.get('icon') or []
    if isinstance(icon, str):
        icon = [icon]
    pairs += [('icon[%d]' % i, p) for i, p in enumerate(icon)]
    return pairs


class TestSpecIsRelocatable(unittest.TestCase):

    def test_spec_source_has_no_absolute_paths(self):
        """No drive-letter or POSIX-root literal may be hardcoded in the spec."""
        with open(_SPEC_PATH, encoding='utf-8') as f:
            source = f.read()
        offenders = re.findall(r"""['"]([A-Za-z]:[\\/]|/(?:home|Users)/)[^'"]*['"]""",
                               source)
        self.assertEqual(
            offenders, [],
            msg=('HDR_to_SDR_Converter.spec hardcodes absolute path(s): %r -- '
                 'build them from SPECPATH so the spec works from any checkout'
                 % (offenders,)))

    def test_every_path_resolves_under_specpath(self):
        """Executed with SPECPATH=<sentinel>, every path must land under it."""
        captured = _exec_spec(_SENTINEL)
        for label, path in _source_paths(captured):
            self.assertTrue(
                os.path.normpath(path).startswith(os.path.normpath(_SENTINEL)),
                msg=('%s does not resolve under SPECPATH: %r -- the spec must '
                     'not reference anything outside its own checkout'
                     % (label, path)))

    def test_spec_relocates_when_specpath_changes(self):
        """Two different SPECPATHs must yield two different path sets.

        Guards the degenerate pass where paths are constant and merely happen
        to sit under the sentinel.
        """
        first = dict(_source_paths(_exec_spec(_SENTINEL)))
        second = dict(_source_paths(_exec_spec(os.path.join(os.sep + 'other', 'tree'))))
        self.assertEqual(sorted(first), sorted(second))
        for label in first:
            self.assertNotEqual(
                first[label], second[label],
                msg='%s did not move with SPECPATH: %r' % (label, first[label]))


class TestSpecContent(unittest.TestCase):

    def test_pro_hidden_imports_match_build_script(self):
        """The spec's hiddenimports must match build_installer.bat's flags.

        build_installer.bat uses the spec for the PyArmor branch and its own
        --hidden-import flags otherwise, so a module added to one and not the
        other ships fine unobfuscated but silently vanishes from obfuscated
        builds. The build script says to keep both lists in sync; this enforces it.
        """
        captured = _exec_spec(_SENTINEL)
        spec_imports = set(captured['analysis'].kwargs.get('hiddenimports') or [])
        with open(os.path.join(_ROOT, 'build_installer.bat'), encoding='utf-8') as f:
            bat = f.read()
        # Anchored to line start: both the ':: ' comment block documenting this
        # requirement and a later 'echo Check the --hidden-import flags...'
        # error message mention the flag mid-line, and would otherwise parse as
        # a module named 'flags'. Real flags are always their own continuation line.
        bat_imports = set(re.findall(r'^\s*--hidden-import\s+(\S+)', bat, re.MULTILINE))
        self.assertEqual(
            spec_imports, bat_imports,
            msg=('hiddenimports in HDR_to_SDR_Converter.spec and the '
                 '--hidden-import flags in build_installer.bat have drifted; '
                 'obfuscated and unobfuscated builds would ship different code'))


if __name__ == '__main__':
    unittest.main()
