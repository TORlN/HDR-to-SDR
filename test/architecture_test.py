"""Module boundaries are a test, not a convention.

src/conversion.py used to import tkinter, which made a headless/CLI mode
impossible and forced 41 messagebox patches into the conversion suite. That
edge is gone (audit item 2); this file is what stops it, or an equivalent,
coming back.

Only the machine-checkable part lives here: which module may import which,
and which may touch tkinter. Every edge in the table is already visible in
`grep '^from' src/*.py`, so the table discloses nothing the public source
does not. The reasoning, the layer map and the known traps live in
src/pro/ARCHITECTURE.md.
"""
import ast
import os
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_ROOT, 'src')

# Layers, leaves first. A module may import only from layers nearer the leaves
# than itself -- never sideways within its own layer, never upward.
#
# The tkinter flag is a ceiling, not a requirement: False is enforced, True
# merely permits. (main.pyw imports tkinterdnd2, not tkinter, and would fail
# an assertion that True modules must import it.)
_ALLOWED: "dict[str, tuple[frozenset[str], bool]]" = {
    'settings':           (frozenset(), False),
    'updater':            (frozenset(), False),
    'license_errors':     (frozenset(), False),
    'utils':              (frozenset(), False),
    'conversion_view':    (frozenset(), False),
    'ffmpeg_command':     (frozenset({'conversion_view', 'utils'}), False),
    'licensing':          (frozenset({'license_errors'}), False),
    'conversion':         (frozenset({'utils', 'conversion_view', 'ffmpeg_command'}), False),
    'dark_theme':         (frozenset(), True),
    'dialog_theme':       (frozenset(), True),
    'tk_conversion_view': (frozenset({'conversion_view'}), True),
    'preview':            (frozenset({'utils'}), True),
    'dialogs':            (frozenset({'dialog_theme', 'licensing', 'updater'}), True),
    'gui':                (frozenset({'dark_theme', 'conversion', 'tk_conversion_view',
                                      'utils', 'settings', 'dialogs', 'preview',
                                      'updater'}), True),
    'main':               (frozenset({'gui', 'licensing', 'utils'}), True),
}

# gui.py is the composition root, not a shared library. That edge is the
# direction audit item 2 exists to protect.
_MAY_IMPORT_GUI = frozenset({'main'})

# src/pro/ is a separate private repo with its own copy of this guard, and it
# is absent from CI checkouts entirely. src/_secrets.py is gitignored for the
# same reason.
_NOT_OURS = frozenset({'pro', '_secrets'})


def _modules() -> dict:
    """Every public module in src/, as {name: path}.

    main.pyw carries no .py extension, so a plain *.py sweep would silently
    skip the one module that imports gui.
    """
    found = {}
    for entry in sorted(os.listdir(_SRC)):
        name, ext = os.path.splitext(entry)
        if ext in ('.py', '.pyw') and name not in _NOT_OURS:
            found[name] = os.path.join(_SRC, entry)
    return found


def _module_reached(dotted: str) -> str:
    """The src-module name a dotted import target actually reaches.

    A leading 'src.' collapses to its second component. src/ is a namespace
    package with no __init__.py, so `import src.gui` names exactly the module
    `import gui` does; the spelling resolves only because the tests put the
    repo root on sys.path, and would not resolve at all in the frozen build.
    Left un-collapsed it yields 'src', which is not a key in _modules(), so
    every caller's `& set(modules)` filter silently drops it -- which is how
    `from src.gui import HDRConverterGUI` in a src/ module would evade
    test_only_the_entry_point_imports_gui, and `import src.pro.licensing`
    TestProIsReachedOnlyThroughImportlib. Closed here symmetrically with the
    pro guard's own _imports (commit 85beb68).
    """
    parts = dotted.split('.')
    if parts[0] == 'src' and len(parts) > 1:
        return parts[1]
    return parts[0]


def _imports(path: str) -> set:
    """Top-level package name of every import in *path*, nested ones included.

    dialogs.py and gui.py both import legitimately from inside function
    bodies, so nested nodes must be collected rather than skipped -- and
    collecting them is what closes the deferred-import escape hatch that would
    otherwise let `def f(): from tkinter import messagebox` back into
    conversion.py. Relative imports are skipped: src/ is a namespace package
    with none, and node.module is None for them.

    `from src import gui` names its target only in the alias list, so that
    form is read from the aliases rather than from node.module.
    """
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(_module_reached(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ''
            if module == 'src':
                names.update(_module_reached(f'src.{alias.name}')
                             for alias in node.names)
            else:
                names.add(_module_reached(module))
    return names


class TestNoAgentInstructionsInThePublicRepo(unittest.TestCase):

    def test_gitignore_still_excludes_claude_md(self):
        """CLAUDE.md belongs in src/pro/, which covers both repos as one
        system. Asserting the rule (rather than trusting it) is the same shape
        as project_config_test.test_no_stale_config_files_left_behind."""
        with open(os.path.join(_ROOT, '.gitignore'), encoding='utf-8') as handle:
            rules = {line.strip() for line in handle}
        self.assertIn(
            'CLAUDE.md', rules,
            msg="'CLAUDE.md' is no longer a .gitignore rule -- agent "
                "instruction files must stay out of the public repo")


class TestEveryModuleIsPlaced(unittest.TestCase):
    """A new module fails the suite until someone puts it in a layer on
    purpose. That is what makes this guard outlive the refactor that
    prompted it."""

    def test_every_src_module_appears_in_the_table(self):
        missing = sorted(set(_modules()) - set(_ALLOWED))
        self.assertEqual(
            missing, [],
            msg=f'module(s) {missing} are not in the layer table -- place each '
                f'one deliberately (see src/pro/ARCHITECTURE.md) rather than '
                f'pasting in whatever it happens to import')

    def test_the_table_names_no_module_that_is_gone(self):
        stale = sorted(set(_ALLOWED) - set(_modules()))
        self.assertEqual(
            stale, [],
            msg=f'the table still names deleted module(s): {stale}')


class TestImportsStayInsideTheAllowlist(unittest.TestCase):

    def test_no_module_imports_outside_its_allowlist(self):
        modules = _modules()
        for name, path in sorted(modules.items()):
            allowed = _ALLOWED[name][0]
            extra = sorted((_imports(path) & set(modules)) - allowed - {name})
            with self.subTest(module=name):
                self.assertEqual(
                    extra, [],
                    msg=f'{name} imports {extra}, which its layer does not '
                        f'allow -- imports may only point toward the leaves')

    def test_no_headless_module_imports_tkinter(self):
        for name, path in sorted(_modules().items()):
            if _ALLOWED[name][1]:
                continue
            with self.subTest(module=name):
                self.assertNotIn(
                    'tkinter', _imports(path),
                    msg=f'{name} imports tkinter, but it sits on the headless '
                        f'side of the seam a CLI mode would enter through -- '
                        f'put the GUI work behind conversion_view instead')

    def test_only_the_entry_point_imports_gui(self):
        for name, path in sorted(_modules().items()):
            if name == 'gui' or name in _MAY_IMPORT_GUI:
                continue
            with self.subTest(module=name):
                self.assertNotIn(
                    'gui', _imports(path),
                    msg=f'{name} imports gui, which is the composition root, '
                        f'not a shared library')


class TestImportsHelperSeesEverySrcPrefixedForm(unittest.TestCase):
    """The same class of hole that was already closed on the pro side.

    src/ is a namespace package with no __init__.py, so `from src.gui import
    HDRConverterGUI` inside a src/ module names exactly the module `import
    gui` does -- it resolves only because the tests put the repo root on
    sys.path, and would not resolve at all in the frozen build. Left
    un-collapsed it yields 'src', which is not a key in _modules(), so every
    caller's `& set(modules)` filter drops it: that spelling evaded
    test_only_the_entry_point_imports_gui outright, and `import
    src.pro.licensing` evaded TestProIsReachedOnlyThroughImportlib the same
    way.
    """

    def test_every_way_of_importing_through_src_is_visible(self):
        source = (
            "import src.gui\n"
            "from src.gui import HDRConverterGUI\n"
            "from src import gui\n"
            "import src.pro.licensing\n"
            "from src.pro.licensing import activate_license\n"
            "import utils\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'synthetic.py')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(source)
            found = _imports(path)
        self.assertEqual(
            found, {'gui', 'pro', 'utils'},
            msg=f'_imports() dropped a src.-prefixed edge -- got {found!r} '
                f'for a file containing "import src.gui", "from src.gui '
                f'import X", "from src import gui", "import '
                f'src.pro.licensing" and "from src.pro.licensing import x"; '
                f'none of them may collapse to the bare, unfiltered \'src\'')


class TestProIsReachedOnlyThroughImportlib(unittest.TestCase):

    def test_no_public_module_statically_imports_pro(self):
        """licensing.py and dialogs.py reach pro through
        importlib.import_module, which until now was protected by nothing but
        a source comment. The typecheck job depends on it holding: pyright
        never resolves pro (it is absent from CI checkouts), and a static
        import would break the free-edition build outright."""
        for name, path in sorted(_modules().items()):
            with self.subTest(module=name):
                self.assertNotIn(
                    'pro', _imports(path),
                    msg=f'{name} imports pro statically -- reach it through '
                        f'importlib.import_module so the free edition still '
                        f'builds when src/pro/ is absent')


if __name__ == '__main__':
    unittest.main()
