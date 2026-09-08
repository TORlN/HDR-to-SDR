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
- Release builds now verify the exact Git LFS-tracked FFmpeg and ffprobe inputs
  against a source-provenance manifest before packaging or signing.
- In-app updates now verify GitHub's asset size and SHA-256 digest, restrict
  download redirects to GitHub hosts, and require a valid installer signature
  from the expected publisher before launch.
- Packaged builds now fail safely when a bundled FFmpeg executable is missing
  instead of using an unrelated executable found on `PATH`.
- Update downloads now recover cleanly if the installer cannot launch, and
  stale updater-only temporary directories are removed safely after 24 hours.
- A cancelled or completed conversion can no longer let stale monitor, retry,
  or completion callbacks affect a later conversion.
- Conversion-launch tests now reject unmocked temporary-file allocation, so
  test runs cannot leave `.hdr-to-sdr-*` artifacts in the repository.
- Failed FFmpeg or monitor startup now restores conversion controls and cleans
  up any partially started process and temporary output.
- Cancel remains available throughout an active conversion, even when preview
  layout refreshes, and preview-mutating controls are disabled meanwhile.
- Updates now save settings and stop preview work before closing, and wait for
  an active conversion monitor to reap its process before the app exits.
- Pro batch queues now yield between items, preventing long synchronous
  rejection runs from exhausting the call stack.
- Tone-mapped HDR video now removes HDR10, HDR10+, and Dolby Vision side data
  from SDR output so players do not misclassify it as HDR.
- HDR metadata cleanup now uses filters supported by the FFmpeg versions used
  in both packaged builds and CI.
- GPU 10-bit conversion now preserves 10-bit precision through tonemapping
  and the color LUT instead of reducing frames to 8-bit before encoding.
- Variable-frame-rate video now preserves its original frame timestamps
  instead of being forced to a single average frame rate during conversion.
