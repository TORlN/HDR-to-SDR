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
- Existing output files are now replaced only after a conversion finishes
  successfully. Cancellation, encoding failure, and fallback failure preserve
  the previous file instead of leaving a partial replacement.
- Input and output paths that identify the same file through links, junctions,
  mapped drives, or other filesystem aliases are now rejected before conversion
  and checked again before the completed output is published.
- Mocked conversion tests no longer leave zero-byte temporary output files in
  the working tree.
