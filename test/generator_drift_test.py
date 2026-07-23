"""Guards against src/luts/rec2020_to_rec709.cube silently diverging from
tools/generate_lut.py (e.g. a hand-edit, a merge artifact, or regenerating
with an uncommitted script change). The committed .cube file must always be
exactly what the generator produces."""
import importlib.util
import os
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_GENERATOR_PATH = os.path.join(_REPO_ROOT, 'tools', 'generate_lut.py')
_CUBE_PATH = os.path.join(_REPO_ROOT, 'src', 'luts', 'rec2020_to_rec709.cube')

_spec = importlib.util.spec_from_file_location('generate_lut', _GENERATOR_PATH)
generate_lut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_lut)


class TestLutGeneratorDrift(unittest.TestCase):

    def test_committed_cube_matches_generator_output(self):
        expected_lines = generate_lut.generate_cube_lines(generate_lut.LUT_SIZE)
        expected_content = '\n'.join(expected_lines) + '\n'
        with open(_CUBE_PATH, 'r', newline='\n') as f:
            actual_content = f.read()
        self.assertEqual(
            actual_content, expected_content,
            "src/luts/rec2020_to_rec709.cube does not match tools/generate_lut.py's "
            "output -- regenerate it with: python tools/generate_lut.py"
        )

    def test_cube_header_declares_correct_size(self):
        with open(_CUBE_PATH, 'r') as f:
            first_line = f.readline().strip()
        self.assertEqual(first_line, f'LUT_3D_SIZE {generate_lut.LUT_SIZE}')
