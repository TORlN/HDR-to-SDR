"""Verify the exact FFmpeg inputs admitted to a release build."""

import hashlib
import json
import os
import sys


_PINNED_PATHS = {
    'src/ffmpeg.exe',
    'src/ffprobe.exe',
    'tools/ffmpeg-patches/ffmpeg_extra.sh',
}
_PROVENANCE_FIELDS = {
    'ffmpeg_source',
    'ffmpeg_revision',
    'mabs_source',
    'mabs_revision',
    'configuration',
    'patch_revision',
}


def verify_manifest(repo_root, manifest, *, open_file=open):
    """Raise when a release input differs from its tracked manifest."""
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise ValueError('unsupported FFmpeg manifest schema')

    build = manifest.get('build')
    if not isinstance(build, dict) or any(
        not isinstance(build.get(field), str) or not build[field].strip()
        for field in _PROVENANCE_FIELDS
    ):
        raise ValueError('incomplete FFmpeg build provenance')

    files = manifest.get('files')
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise ValueError('invalid FFmpeg pinned paths')
    paths = [item.get('path') for item in files]
    if len(paths) != len(_PINNED_PATHS) or set(paths) != _PINNED_PATHS:
        raise ValueError('invalid FFmpeg pinned paths')

    root = os.path.abspath(os.fspath(repo_root))
    for item in files:
        relative_path = item['path']
        size = item.get('size')
        expected_digest = item.get('sha256')
        if (
            not isinstance(size, int) or isinstance(size, bool) or size <= 0
            or not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or any(char not in '0123456789abcdef' for char in expected_digest)
        ):
            raise ValueError(f'invalid file metadata for {relative_path}')

        path = os.path.join(root, *relative_path.split('/'))
        digest = hashlib.sha256()
        measured_size = 0
        try:
            with open_file(path, 'rb') as handle:
                if relative_path == 'tools/ffmpeg-patches/ffmpeg_extra.sh':
                    patch_script = handle.read()
                    if b'\r\n' in patch_script:
                        raise ValueError('FFmpeg shell patch script must use LF line endings')
                    measured_size = len(patch_script)
                    digest.update(patch_script)
                else:
                    while chunk := handle.read(1024 * 1024):
                        measured_size += len(chunk)
                        digest.update(chunk)
        except OSError as exc:
            raise ValueError(f'missing pinned file: {relative_path}') from exc

        if measured_size != size:
            raise ValueError(
                f'size mismatch for {relative_path}: expected {size}, got {measured_size}'
            )
        actual_digest = digest.hexdigest()
        if actual_digest != expected_digest:
            raise ValueError(
                f'SHA-256 mismatch for {relative_path}: '
                f'expected {expected_digest}, got {actual_digest}'
            )


def main(repo_root=None, *, manifest=None, open_file=open, output=None):
    """Return zero only when every tracked release input matches."""
    output = output or sys.stdout
    root = os.path.abspath(repo_root or os.path.join(os.path.dirname(__file__), '..'))
    try:
        if manifest is None:
            manifest_path = os.path.join(root, 'tools', 'ffmpeg-manifest.json')
            with open(manifest_path, encoding='utf-8') as handle:
                manifest = json.load(handle)
        verify_manifest(root, manifest, open_file=open_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'[ERROR] FFmpeg release input verification failed: {exc}', file=output)
        return 1
    print('[OK] FFmpeg release inputs verified', file=output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
