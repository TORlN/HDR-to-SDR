"""Behavioral tests for the pure output-resolution model."""
import unittest

from src.resolution import (
    PRESETS,
    ResolutionTarget,
    output_dimensions,
    output_suffix,
    preset_for_short_edge,
    validate_target,
)


class TestResolutionDimensions(unittest.TestCase):
    def test_landscape_720p_preserves_aspect_ratio(self) -> None:
        self.assertEqual(
            output_dimensions(1920, 1080, ResolutionTarget(720)),
            (1280, 720),
        )

    def test_portrait_720p_targets_the_short_edge(self) -> None:
        self.assertEqual(
            output_dimensions(1080, 1920, ResolutionTarget(720)),
            (720, 1280),
        )

    def test_square_target_keeps_both_dimensions_even(self) -> None:
        self.assertEqual(
            output_dimensions(1001, 1001, ResolutionTarget(720)),
            (720, 720),
        )

    def test_nonstandard_ratio_floors_both_dimensions_to_even(self) -> None:
        self.assertEqual(
            output_dimensions(1919, 800, ResolutionTarget(719, custom=True)),
            (1722, 718),
        )

    def test_one_pixel_custom_value_normalizes_to_two(self) -> None:
        dimensions = output_dimensions(
            1920, 1080, ResolutionTarget(1, custom=True)
        )
        self.assertIsNotNone(dimensions)
        assert dimensions is not None
        width, height = dimensions
        self.assertEqual(min(width, height), 2)

    def test_source_target_has_no_dimensions(self) -> None:
        self.assertIsNone(output_dimensions(1920, 1080, None))

    def test_target_equal_to_source_normalizes_to_no_resize(self) -> None:
        self.assertIsNone(
            output_dimensions(1920, 1080, ResolutionTarget(1080, custom=True))
        )

    def test_odd_target_that_floors_to_source_has_no_resize(self) -> None:
        self.assertIsNone(
            output_dimensions(1920, 1080, ResolutionTarget(1081, custom=True))
        )

    def test_invalid_source_dimensions_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, 'Source dimensions must be positive integers'
        ):
            output_dimensions(0, 1080, ResolutionTarget(720))

    def test_nonpositive_custom_short_edge_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError, 'Custom short edge must be a positive integer'
        ):
            output_dimensions(1920, 1080, ResolutionTarget(0, custom=True))


class TestResolutionCatalogAndTiers(unittest.TestCase):
    def test_presets_are_descending_standard_short_edges(self) -> None:
        self.assertEqual(
            [(preset.label, preset.short_edge) for preset in PRESETS],
            [
                ('8K', 4320),
                ('4K', 2160),
                ('1440p', 1440),
                ('1080p', 1080),
                ('720p', 720),
                ('480p', 480),
            ],
        )

    def test_preset_lookup_returns_matching_preset(self) -> None:
        preset = preset_for_short_edge(2160)
        self.assertIsNotNone(preset)
        self.assertEqual((preset.label, preset.filename_label), ('4K', '4k'))

    def test_preset_lookup_returns_none_for_non_catalog_value(self) -> None:
        self.assertIsNone(preset_for_short_edge(900))

    def test_community_accepts_only_a_lower_preset(self) -> None:
        validate_target(1920, 1080, ResolutionTarget(720), licensed=False)
        for target in (
            ResolutionTarget(1080),
            ResolutionTarget(1440),
            ResolutionTarget(720, custom=True),
        ):
            with self.assertRaisesRegex(
                ValueError, 'Custom resolution and upscaling require Pro'
            ):
                validate_target(1920, 1080, target, licensed=False)

    def test_community_source_below_480p_has_no_preset_target(self) -> None:
        validate_target(640, 360, None, licensed=False)
        with self.assertRaisesRegex(
            ValueError, 'Custom resolution and upscaling require Pro'
        ):
            validate_target(640, 360, ResolutionTarget(480), licensed=False)

    def test_pro_accepts_preset_upscale_and_custom_target(self) -> None:
        validate_target(1920, 1080, ResolutionTarget(4320), licensed=True)
        validate_target(
            1920, 1080, ResolutionTarget(901, custom=True), licensed=True
        )

    def test_non_catalog_preset_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'Unknown resolution preset'):
            validate_target(1920, 1080, ResolutionTarget(900), licensed=True)


class TestResolutionSuffix(unittest.TestCase):
    def test_source_has_no_suffix(self) -> None:
        self.assertIsNone(output_suffix(1920, 1080, None))

    def test_presets_use_lowercase_filename_labels(self) -> None:
        for target, suffix in (
            (ResolutionTarget(4320), '8k'),
            (ResolutionTarget(2160), '4k'),
        ):
            with self.subTest(target=target):
                self.assertEqual(output_suffix(1920, 1080, target), suffix)

    def test_custom_uses_normalized_final_dimensions(self) -> None:
        self.assertEqual(
            output_suffix(1920, 1080, ResolutionTarget(900, custom=True)),
            '1600x900',
        )

    def test_target_that_resolves_to_source_has_no_suffix(self) -> None:
        self.assertIsNone(
            output_suffix(1920, 1080, ResolutionTarget(1080, custom=True))
        )

    def test_odd_target_that_floors_to_source_has_no_suffix(self) -> None:
        self.assertIsNone(
            output_suffix(1920, 1080, ResolutionTarget(1081, custom=True))
        )
