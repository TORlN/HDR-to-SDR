#!/bin/bash
# HDR to SDR Converter: keep our local ffmpeg source patches applied across
# every `ffmpegUpdate=y` rebuild. See tools/ffmpeg-patches/ in the app repo
# for the full writeup of what/why.

# Force the suite to think ffmpeg needs recompiling even if the git HEAD
# didn't move, so a patch-only change still takes effect on the next run.
touch custom_updated

_pre_configure(){
    local f=libavutil/hwcontext_vulkan.c

    # vulkan-video-encode-src: neutralize hwcontext_vulkan.c's opportunistic
    # VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR usage-add. This app never uses
    # ffmpeg's native Vulkan video encoders, so the capability is pure
    # downside here. (Turned out NOT to be the cause of the "No memory type
    # found for flags 0x1" crash below -- supported_usage never even had
    # this bit set on the format/config that crashes -- but it's still
    # correct to disable, so kept.) A known marker makes re-running safe;
    # an unknown source shape stops the build for deliberate review.
    if [[ ! -f $f ]]; then
        echo "ERROR: vulkan-video-encode-src: missing $f"
        exit 1
    elif grep -Eq '^[[:space:]]*hwctx->usage \|= VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR;[[:space:]]*$' "$f"; then
        sed -i.bak 's/^\([[:space:]]*\)hwctx->usage |= VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR;[[:space:]]*$/\1;  \/* HDR-to-SDR: disabled encode-src, see tools\/ffmpeg-patches\/README.md *\//' "$f" || exit 1
        rm -f "$f.bak"
        echo "vulkan-video-encode-src: neutralized VIDEO_ENCODE_SRC_BIT_KHR usage-add"
    elif grep -Fq 'HDR-to-SDR: disabled encode-src' "$f"; then
        echo "vulkan-video-encode-src: already neutralized"
    else
        echo "ERROR: vulkan-video-encode-src: unrecognized source, review tools/ffmpeg-patches/README.md"
        exit 1
    fi

    # vulkan-host-transfer: neutralize the VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT
    # usage-add. Confirmed via debug instrumentation (2026-08-29) to be the
    # actual cause of "No memory type found for flags 0x1" / vulkan_pool_alloc
    # failing on this dev's NVIDIA RTX 4090 (driver 610.88): with this bit
    # set, the resulting image's VkMemoryRequirements.memoryTypeBits only
    # allows memory-type indices 2/3 (this driver's host-visible/staging
    # pool), none of which carry VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT --
    # so the device-local allocation upload/download needs always fails.
    # Upstream already has a probe-and-drop guard for this
    # (vulkan_host_transfer_usable(), a few hundred lines below), but it
    # reports this config as usable when it demonstrably isn't -- a gap in
    # that probe, not something we can fix from here. This app never issues
    # host-side image copies (no --enable use of FF_VK_EXT_HOST_IMAGE_COPY),
    # so the capability is pure downside, same reasoning as encode-src above.
    if [[ ! -f $f ]]; then
        echo "ERROR: vulkan-host-transfer: missing $f"
        exit 1
    elif grep -Eq '^[[:space:]]*hwctx->usage \|= supported_usage & VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT;[[:space:]]*$' "$f"; then
        sed -i.bak 's/^\([[:space:]]*\)hwctx->usage |= supported_usage & VK_IMAGE_USAGE_HOST_TRANSFER_BIT_EXT;[[:space:]]*$/\1;  \/* HDR-to-SDR: disabled host-transfer, see tools\/ffmpeg-patches\/README.md *\//' "$f" || exit 1
        rm -f "$f.bak"
        echo "vulkan-host-transfer: neutralized HOST_TRANSFER_BIT_EXT usage-add"
    elif grep -Fq 'HDR-to-SDR: disabled host-transfer' "$f"; then
        echo "vulkan-host-transfer: already neutralized"
    else
        echo "ERROR: vulkan-host-transfer: unrecognized source, review tools/ffmpeg-patches/README.md"
        exit 1
    fi
}
