"""Project configuration must stay pinned, single-sourced and enforced.

Audit item 8. Three concrete problems, each with a test here:

- **Dev tools floated on `>=`.** `pytest>=8.0.0` had already resolved to an
  installed 9.1.1 -- a major version jump with no code change and nothing to
  notice it. `pyinstaller>=6.21.0` is worse than a test-tool drift: it decides
  what goes into the signed installer, so an unpinned bump changes the shipped
  artifact without a commit.
- **The Python version was a bare literal duplicated across two CI jobs**, with
  nothing tying it to what the README promised users.
- **Config was spread across four files.** pyright and coverage now live in
  pyproject.toml; these tests pin the two settings that actually gate the build
  so a consolidation (or a future move back out) cannot quietly drop them.

Runtime pins are deliberately not lockfile-managed: pillow and tkinterdnd2
both declare zero dependencies, so requirements.txt already resolves to
exactly two packages and a lockfile would add maintenance for no
reproducibility gain. test_runtime_requirements_have_no_transitive_surface
pins that assumption -- if a runtime dep ever gains dependencies, that test is
where the decision gets revisited.
"""
import os
import re
import tomllib
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_WORKFLOW = os.path.join(_REPO_ROOT, '.github', 'workflows', 'python-tests.yml')
_PYPROJECT = os.path.join(_REPO_ROOT, 'pyproject.toml')
_README = os.path.join(_REPO_ROOT, 'README.md')

# The two runtime packages, each verified with `pip show` to have an empty
# Requires: field as of 2026-08-02.
_DEPENDENCY_FREE_RUNTIME_DEPS = frozenset(('pillow', 'tkinterdnd2'))


def _read(path: str) -> str:
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _requirement_lines(filename: str) -> list:
    """Every actual requirement in *filename*, comments and blanks stripped."""
    lines = []
    for raw in _read(os.path.join(_REPO_ROOT, filename)).splitlines():
        line = raw.split('#', 1)[0].strip()
        if line:
            lines.append(line)
    return lines


class TestRequirementsArePinned(unittest.TestCase):
    """Every dependency resolves to one version, today and in a year."""

    def _assert_all_pinned(self, filename: str) -> None:
        for requirement in _requirement_lines(filename):
            with self.subTest(requirement=requirement):
                self.assertRegex(
                    requirement, r'^[A-Za-z0-9._-]+==[^,\s]+$',
                    msg=f'{filename}: {requirement!r} is not an exact pin. A '
                        f'floating spec silently changes what CI runs and what '
                        f'the installer ships.')

    def test_runtime_requirements_are_exactly_pinned(self):
        self._assert_all_pinned('requirements.txt')

    def test_dev_requirements_are_exactly_pinned(self):
        self._assert_all_pinned('requirements-dev.txt')

    def test_runtime_requirements_have_no_transitive_surface(self):
        """The reason requirements.txt needs no lockfile.

        Guards the assumption, not the packages: if a runtime dep is added or
        one of these grows dependencies, this fails and the lockfile question
        gets asked again instead of being silently answered 'no'.
        """
        names = {re.split(r'[=<>!~\[]', r, maxsplit=1)[0].strip().lower()
                 for r in _requirement_lines('requirements.txt')}
        unexpected = names - _DEPENDENCY_FREE_RUNTIME_DEPS
        self.assertEqual(
            unexpected, set(),
            msg=f'new runtime dependency {sorted(unexpected)} -- confirm it is '
                f'dependency-free (pip show <name>, empty Requires:) and add it '
                f'to _DEPENDENCY_FREE_RUNTIME_DEPS, or introduce a lockfile')


class TestPythonVersionIsSingleSourced(unittest.TestCase):
    """One declaration, every consumer derived from it."""

    def _workflow(self) -> str:
        return _read(_WORKFLOW)

    def _declared_version(self) -> str:
        match = re.search(r'^\s*PYTHON_VERSION:\s*[\'"]?([\d.]+)[\'"]?\s*$',
                          self._workflow(), re.MULTILINE)
        self.assertIsNotNone(
            match, msg='no PYTHON_VERSION declaration found in the workflow')
        assert match is not None
        return match.group(1)

    def test_declared_exactly_once(self):
        found = re.findall(r'^\s*PYTHON_VERSION:\s*[\'"]?[\d.]+[\'"]?\s*$',
                           self._workflow(), re.MULTILINE)
        self.assertEqual(
            len(found), 1,
            msg=f'expected one PYTHON_VERSION declaration, found {len(found)} '
                f'-- two declarations is the duplication this replaced')

    def test_no_job_hardcodes_a_version(self):
        hardcoded = re.findall(r'^\s*python-version:\s*[\'"]?([\d.]+)[\'"]?\s*$',
                               self._workflow(), re.MULTILINE)
        self.assertEqual(
            hardcoded, [],
            msg=f'these jobs pin a literal version instead of referencing the '
                f'single declaration: {hardcoded}')

    def test_every_job_uses_the_declaration(self):
        uses = re.findall(r'^\s*python-version:\s*(.+?)\s*$',
                          self._workflow(), re.MULTILINE)
        self.assertTrue(uses, msg='no python-version keys found at all')
        for value in uses:
            with self.subTest(value=value):
                self.assertIn('env.PYTHON_VERSION', value,
                              msg=f'{value!r} does not derive from PYTHON_VERSION')

    def test_readme_promises_the_version_ci_actually_runs(self):
        version = self._declared_version()
        readme = _read(_README)
        self.assertIn(
            f'Python {version}', readme,
            msg=f'README does not state "Python {version}", the version CI '
                f'runs -- users would be told to install something untested')

    def test_readme_does_not_still_advertise_a_range(self):
        # The claim this replaced was "tested on 3.10-3.13" while CI only ever
        # ran one version. An en-dash or hyphen range next to "Python" is the
        # shape of that regression.
        readme = _read(_README)
        ranges = re.findall(r'Python\s+\d+\.\d+\s*[-–]\s*\d+\.\d+', readme)
        self.assertEqual(ranges, [], msg=f'README advertises untested range(s): '
                                         f'{ranges}')


class TestGatesSurviveConsolidation(unittest.TestCase):
    """pyproject.toml now holds the settings that gate the build."""

    def _pyproject(self) -> dict:
        with open(_PYPROJECT, 'rb') as handle:
            return tomllib.load(handle)

    def test_pyproject_exists_and_parses(self):
        self.assertIsInstance(self._pyproject(), dict)

    def test_coverage_floor_is_not_below_90(self):
        floor = self._pyproject()['tool']['coverage']['report']['fail_under']
        self.assertGreaterEqual(
            floor, 90,
            msg=f'coverage floor dropped to {floor}; 90 is the documented gate')

    def test_coverage_still_measures_src_only(self):
        run = self._pyproject()['tool']['coverage']['run']
        self.assertEqual(run['source'], ['src'])

    def test_coverage_still_omits_the_private_pro_repo(self):
        # src/pro is a separate repo with its own suite; measuring it here
        # would make local and CI coverage disagree by ~5 points.
        omit = self._pyproject()['tool']['coverage']['report']['omit']
        self.assertIn('src/pro/*', omit)

    def test_pyright_checks_both_src_and_test(self):
        include = self._pyproject()['tool']['pyright']['include']
        self.assertEqual(sorted(include), ['src', 'test'],
                         msg='CI runs bare pyright, so include decides the '
                             'scope -- dropping test/ silently ungates it')

    def test_pyright_still_runs_in_standard_mode(self):
        mode = self._pyproject()['tool']['pyright']['typeCheckingMode']
        self.assertEqual(mode, 'standard')

    def test_no_stale_config_files_left_behind(self):
        # Both tools prefer their dedicated file over pyproject.toml, so a
        # leftover would silently win and the settings above would stop
        # describing reality.
        for stale in ('.coveragerc', 'pyrightconfig.json'):
            with self.subTest(file=stale):
                self.assertFalse(
                    os.path.exists(os.path.join(_REPO_ROOT, stale)),
                    msg=f'{stale} still exists and takes precedence over '
                        f'pyproject.toml, so this file is no longer the source '
                        f'of truth')


if __name__ == '__main__':
    unittest.main()
