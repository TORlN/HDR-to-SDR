"""Policy for enabling real Vulkan smoke tests."""


def resolve_vulkan_smoke(mode, probe):
    if mode not in ('skip', 'run', 'require'):
        raise ValueError('HDR_VULKAN_SMOKE_MODE must be skip, run, or require')
    if mode == 'skip':
        return False
    available = probe()
    if mode == 'require' and not available:
        raise RuntimeError('Vulkan/libplacebo smoke tests are required but unavailable')
    return available
