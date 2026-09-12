# Changelog

This document is the release-note draft for the next version. It is reset to
this header and an empty `Unreleased` section after each new version is pushed.

## Unreleased

### Fixed

- File metadata and MaxCLL probing now run off the interface thread. Late
  results cannot overwrite metadata, bitrate defaults, or bit-depth choices
  for a newly selected file, including during Pro batch processing.
- FFmpeg, ffprobe, GPU detection, and preview helper processes now have
  bounded waits and terminate-kill-reap cleanup when they stop responding.
- Update checks now accept only strict `X.Y.Z` release tags, ignoring
  malformed version metadata safely.
- LUT paths now escape FFmpeg filtergraph-special characters, including
  punctuation in installation directories.
- Pro import failures are no longer silently treated as Community-only mode;
  genuine absence still supports fork and development builds.
- Invalid typed gamma values now safely restore the last valid value, while
  finite values outside the supported range are clamped before preview or conversion.
- Malformed saved preferences now fall back safely per setting, preserving
  other valid preferences instead of letting bad persisted values reach the UI.
- Website assets with stable filenames now revalidate hourly instead of being
  treated as immutable for a year, preventing stale browser copies after deploy.
- Website deployments now stop before stale-file deletion or cache invalidation
  when an upload fails, upload entry HTML last, and report partial S3 deletions.
- Website copy now reflects the patched FFmpeg build, current hardware encoder
  names, and the planned v3.3 LUT and resolution features.
- Website deployments now reject untracked or incomplete source directories,
  preserve unmanaged S3 objects, and require explicit production confirmation.
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
- Dolby Vision profile 5 conversion now fails safely when RPU-aware GPU
  tonemapping is unavailable, preventing output with incorrect colors.
- Pro MP4 and MOV conversion now handles each audio track individually,
  preserving compatible tracks while transcoding only incompatible ones.
- MKV conversions now preserve embedded attachments, including subtitle fonts.
- PyArmor release builds now package the selected obfuscated entry point
  instead of silently analyzing the unobfuscated source tree.
- Documentation now clarifies that normal releases use one freemium installer
  and that the FREE-ONLY build is an emergency fallback, not a release.
- Release builds now require clean source repositories and record a manifest
  with paired commit IDs, build inputs, tool versions, and installer hashes.
- FFmpeg rebuilds now stop when their local source patches no longer match the
  checked source, preventing a silently incomplete patched binary.
- Release builds now run coverage and type checks, smoke-test the frozen
  application without opening its UI, and verify Authenticode signatures.
- CI and release builds now install Python dependencies from one hash-locked
  dependency graph and verify the installed packages are consistent.
- CI now uses immutable action revisions, a fixed runner and tool versions,
  least-privilege checkout permissions, and verified packaged FFmpeg inputs.
- Release artifacts no longer vary based on whether UPX is installed on the
  build machine.
- Uninstall now preserves user-created files in the application directory.
