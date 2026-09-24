"""Policy for enabling real Vulkan smoke tests."""

import re


_PHYSICAL_DEVICE = re.compile(
    r'Device \d+ selected: .+ \((?:discrete|integrated)\) \(0x[0-9a-fA-F]+\)\s*$'
)


def is_physical_vulkan_device(log):
    return any(_PHYSICAL_DEVICE.search(line) for line in log.splitlines())


def resolve_vulkan_smoke(mode, probe):
    if mode not in ('skip', 'run', 'require', 'require-physical'):
        raise ValueError(
            'HDR_VULKAN_SMOKE_MODE must be skip, run, require, or require-physical')
    if mode == 'skip':
        return False
    available = probe()
    if mode in ('require', 'require-physical') and not available:
        requirement = 'physical Vulkan GPU' if mode == 'require-physical' else 'Vulkan/libplacebo'
        raise RuntimeError(f'{requirement} smoke tests are required but unavailable')
    return available
