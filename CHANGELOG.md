# HDR to SDR v3.2.4

This release focuses on conversion safety, responsive previews, secure updates,
and reproducible releases.

## Conversion reliability

- Existing output files are replaced only after a successful conversion.
- Input and output paths that refer to the same file through links or mapped
  drives are rejected before conversion and before publishing output.
- FFmpeg, ffprobe, GPU detection, and preview helpers now use bounded waits
  with terminate, kill, and reap cleanup.
- Stale conversion callbacks can no longer affect a later conversion.
- Failed startup restores controls and cleans up partial processes and output.
- Cancel remains available during conversion, and preview controls stay
  disabled until the active conversion ends.
- Pro batch queues yield between items so repeated rejected items cannot
  exhaust the call stack.
- HDR side data is removed from SDR output so players do not misclassify it.
- Variable-frame-rate sources retain their original timestamps.
- GPU 10-bit conversion preserves 10-bit precision through tonemapping.
- Dolby Vision Profile 5 conversion fails safely when required GPU support is
  unavailable.
- Pro MP4 and MOV conversion preserves compatible audio tracks individually,
  transcoding only incompatible tracks.
- MKV conversion preserves embedded attachments such as subtitle fonts.

## Preview and settings

- Metadata and MaxCLL probing no longer blocks the interface or applies stale
  results to a newly selected file.
- Preview caching validates file identity and duration, cancels work for
  discarded files, keeps useful tonemapper variants for the active frame, and
  releases discarded images. It adapts to available memory while supporting
  extraction up to 4K.
- Gamma input is validated and clamped safely, and malformed saved settings
  fall back per setting without discarding valid preferences.
- LUT paths are escaped correctly for FFmpeg filtergraph punctuation.

## Licensing and updates

- Pro import failures are distinguished from an absent Pro package instead of
  silently downgrading a broken installation.
- Packaged builds always use the official licensing endpoint, and malformed
  local license data is rejected without an unnecessary online request.
- Activation storage failures report clearly and roll back remote activation
  when possible.
- Batch processing stops consistently when a license becomes invalid while
  Community single-file conversion remains available.
- Update checks validate release versions, asset size, SHA-256, redirects, and
  installer signatures before launch.
- Failed update launches recover cleanly and stale updater temporary folders
  are removed safely.
- Updates save settings, stop preview work, and wait for active conversion
  cleanup before closing the application.

## Build and deployment

- Release builds verify FFmpeg provenance, dependency integrity, source
  cleanliness, frozen startup, and Authenticode signatures before packaging.
- PyArmor builds package the selected obfuscated source, FFmpeg rebuilds reject
  mismatched local patches, and ambient UPX installs cannot alter artifacts.
- Onedir builds place runtime files beside the executable so bundled FFmpeg,
  LUTs, and other assets are available to both the app and release validator.
- CI and release builds use one hash-locked dependency graph and immutable
  tool and action revisions.
- Installer license notices cover Pillow and tkinterdnd2 in addition to the
  bundled media components.
- Website deployment validates source completeness, uploads entry HTML last,
  preserves unmanaged objects, and stops safely on upload failures.
- Website assets with stable filenames revalidate hourly to prevent stale
  browser copies after deployment.
- Website copy now reflects the current FFmpeg build, hardware encoder names,
  and the planned v3.3 LUT and resolution features.
- Uninstall preserves user-created files in the application directory.

---
**Full Changelog**: https://github.com/TORlN/HDR-to-SDR/compare/v3.2.3...v3.2.4
