# Changelog

This document is the release-note draft for the next version. It is reset to
this header and an empty `Unreleased` section after each new version is pushed.

## Unreleased

- Fixed preset previews going blank after changing tonemappers by preserving
  visible frames during cache trimming.
- Increased normal 4K and limited-memory 1080p preview cache budgets so the
  intended preset, custom-seek, and active-tonemapper working set can fit.
