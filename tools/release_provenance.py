"""Create a reproducible source-and-artifact record for a release build."""

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys


_INPUTS = ('requirements.txt', 'requirements-dev.txt', 'src/ffmpeg.exe', 'src/ffprobe.exe')


def _file_metadata(path, *, open_file=open):
    digest = hashlib.sha256()
    size = 0
    with open_file(path, 'rb') as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {'size': size, 'sha256': digest.hexdigest()}


def _commit(label, root, *, run):
    status = run(['git', '-C', root, 'status', '--porcelain'],
                 capture_output=True, text=True, check=False)
    if status.returncode or status.stdout.strip():
        raise ValueError(f'{label} repository is dirty or unavailable')
    revision = run(['git', '-C', root, 'rev-parse', 'HEAD'],
                   capture_output=True, text=True, check=False)
    if revision.returncode or not revision.stdout.strip():
        raise ValueError(f'{label} repository commit is unavailable')
    return revision.stdout.strip()


def _tool_versions():
    versions = {}
    for package in ('pyinstaller', 'pyarmor'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def collect_manifest(repo_root, installer_path, *, free_only=False,
                     run=subprocess.run, open_file=open, python_version=None,
                     tool_versions=None):
    """Return release provenance after rejecting dirty source repositories."""
    root = os.fspath(repo_root)
    repositories = {'public': _commit('public', root, run=run)}
    if not free_only:
        repositories['pro'] = _commit('private', os.path.join(root, 'src', 'pro'), run=run)

    inputs = {}
    for relative_path in _INPUTS:
        inputs[relative_path.replace('\\', '/')] = _file_metadata(
            os.path.join(root, relative_path), open_file=open_file)

    return {
        'schema_version': 1,
        'free_only': bool(free_only),
        'repositories': repositories,
        'python_version': python_version or sys.version.split()[0],
        'tool_versions': _tool_versions() if tool_versions is None else tool_versions,
        'inputs': inputs,
        'artifacts': {
            'installer': {
                'path': os.path.basename(os.fspath(installer_path)),
                **_file_metadata(installer_path, open_file=open_file),
            },
        },
    }


def main(repo_root=None, installer_path=None, manifest_path=None, *, free_only=False):
    """Write release provenance and return zero, or return one on failure."""
    root = os.path.abspath(repo_root or os.path.join(os.path.dirname(__file__), '..'))
    if not installer_path or not manifest_path:
        print('[ERROR] installer and manifest paths are required')
        return 1
    try:
        manifest = collect_manifest(root, installer_path, free_only=free_only)
        with open(manifest_path, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write('\n')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'[ERROR] Release provenance failed: {exc}')
        return 1
    print(f'[OK] Release provenance written: {manifest_path}')
    return 0


if __name__ == '__main__':
    arguments = [argument for argument in sys.argv[1:] if argument != '--free-only']
    raise SystemExit(main(*arguments, free_only='--free-only' in sys.argv[1:]))
