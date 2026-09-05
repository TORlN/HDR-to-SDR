# Changelog

This document is the release-note draft for the next version. It is reset to
this header and an empty `Unreleased` section after each new version is pushed.

## Unreleased

### Fixed

- Packaged builds now use the official licensing service endpoint even if a
  development override is present in the environment.
- A license activation that cannot be saved locally now reports the storage
  failure and rolls back a newly created remote activation when possible.
- Batch processing now stops consistently when a license is no longer valid,
  while Community single-file conversion remains available.
- Malformed local license data is now rejected safely instead of causing a
  startup error or an unnecessary online request.

