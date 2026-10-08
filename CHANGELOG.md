# Changelog

This document is the release-note draft for the next version. It is reset to
this header and an empty `Unreleased` section after each new version is pushed.

## Unreleased

### Reliability
- Fixed truncated SDR output filenames for source files with dots in their names.
- Installer validation now requires physical Vulkan GPU smoke tests and rejects software Vulkan devices.
- Fixed Dolby Vision profile 5 preview colors and CPU-selected preview failures.
- Corrected GPU tone mapping brightness for 100-nit SDR output.
