# HDR to SDR v3.2.5

This hotfix corrects preview caching behavior introduced in v3.2.4.

## Fixed

- Preset previews no longer become blank after changing the tonemapper and
  selecting another frame.
- Preview extraction now protects the original and converted images being
  handed to the interface from cache eviction.

## Performance

- The normal 4K preview cache budget is now 512 MiB, and the limited-memory
  1080p budget is now 128 MiB, allowing the intended preset, custom-seek, and
  active-tonemapper working set to remain responsive.
- Severe-memory mode remains capped at 32 MiB with 720p extraction and
  prewarming disabled.

---
**Full Changelog**: https://github.com/TORlN/HDR-to-SDR/compare/v3.2.4...v3.2.5
