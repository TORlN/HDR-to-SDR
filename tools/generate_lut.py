"""Generate src/luts/rec2020_to_rec709.cube: a 33x33x33 3D LUT that performs
BT.2020->BT.709 gamut correction on gamma-encoded (Rec.709 OETF) RGB values.

Run once, by hand, whenever this script changes:
    python tools/generate_lut.py

The output is committed to git like any other asset -- never regenerated at
build or app-start time. This generator is specific to this one closed-form
gamut transform; it is not a template for v4.0's creative preset LUTs, which
will be authored in grading software or sourced externally, never scripted.
"""
import os

LUT_SIZE = 33

# BT.2020 RGB -> BT.709 RGB combined matrix (via CIE XYZ, D65 white point for
# both standards -- no chromatic adaptation needed). Derived from each
# standard's primaries chromaticities:
#   BT.2020: R(0.708,0.292) G(0.170,0.797) B(0.131,0.046)
#   BT.709:  R(0.640,0.330) G(0.300,0.600) B(0.150,0.060)
#   White (both): D65 (0.3127, 0.3290)
BT2020_TO_BT709 = [
    [1.6604910021, -0.5876411388, -0.0728498633],
    [-0.1245504745, 1.1328998971, -0.0083494226],
    [-0.0181507634, -0.1005788980, 1.1187296614],
]


def _rec709_eotf(v: float) -> float:
    """Rec.709 inverse transfer function: gamma-encoded value -> linear light."""
    if v < 0.081:
        return v / 4.5
    return ((v + 0.099) / 1.099) ** (1 / 0.45)


def _rec709_oetf(linear: float) -> float:
    """Rec.709 transfer function: linear light -> gamma-encoded value."""
    if linear < 0.018:
        return 4.5 * linear
    return 1.099 * (linear ** 0.45) - 0.099


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _convert(r: float, g: float, b: float) -> 'tuple[float, float, float]':
    """One gamma-encoded, BT.2020-tagged RGB triple -> gamma-encoded BT.709 RGB.

    Decode with the Rec.709 EOTF (the frame is already transfer=bt709 at this
    point in the real filter chain -- only the gamut/primaries are wrong),
    apply the gamut matrix in linear light, clamp out-of-gamut results to
    [0, 1] (matching zscale's own clipping behavior for p=bt709), then
    re-encode with the Rec.709 OETF.
    """
    lr, lg, lb = _rec709_eotf(r), _rec709_eotf(g), _rec709_eotf(b)
    m = BT2020_TO_BT709
    lr709 = m[0][0] * lr + m[0][1] * lg + m[0][2] * lb
    lg709 = m[1][0] * lr + m[1][1] * lg + m[1][2] * lb
    lb709 = m[2][0] * lr + m[2][1] * lg + m[2][2] * lb
    lr709, lg709, lb709 = _clamp01(lr709), _clamp01(lg709), _clamp01(lb709)
    return _rec709_oetf(lr709), _rec709_oetf(lg709), _rec709_oetf(lb709)


def generate_cube_lines(size: int) -> 'list[str]':
    """Return the full .cube file content as a list of lines (no trailing
    newline character embedded -- the caller joins with '\\n')."""
    lines = [f'LUT_3D_SIZE {size}']
    # .cube ordering: red index fastest, then green, then blue -- confirmed
    # against real ffmpeg lut3d/libplacebo output in this session (a
    # deliberately non-symmetric test LUT built with this exact ordering
    # produced the expected, distinguishable result on both filters).
    for bi in range(size):
        b = bi / (size - 1)
        for gi in range(size):
            g = gi / (size - 1)
            for ri in range(size):
                r = ri / (size - 1)
                out_r, out_g, out_b = _convert(r, g, b)
                lines.append(f'{out_r:.6f} {out_g:.6f} {out_b:.6f}')
    return lines


def main() -> None:
    lines = generate_cube_lines(LUT_SIZE)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'luts')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.normpath(os.path.join(out_dir, 'rec2020_to_rec709.cube'))
    with open(out_path, 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'Wrote {out_path} ({len(lines) - 1} grid points)')


if __name__ == '__main__':
    main()
