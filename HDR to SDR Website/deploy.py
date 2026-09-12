#!/usr/bin/env python3
"""
HDR to SDR Website - AWS Deployment Script
Syncs static files to S3 and invalidates CloudFront cache.

Usage:
    python deploy.py --dry-run
    python deploy.py --confirm-production

The source must be this script's complete, tracked site directory. Production
deployments require explicit confirmation.
"""

from __future__ import annotations

import sys
import time
import mimetypes
import argparse
from pathlib import Path

# Ensure Unicode status symbols (✓ ✗ →) work on Windows consoles that default
# to cp1252. reconfigure is a no-op on stdout streams that are already UTF-8.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]

_SCRIPT_DIR = Path(__file__).parent
SITE_ROOT = _SCRIPT_DIR.resolve()
SITE_MARKER = "index.html"
MANAGED_SITE_FILES = frozenset({
    "hdr-frame.png",
    "icon.png",
    "index.html",
    "robots.txt",
    "script.js",
    "sdr-frame.png",
    "sitemap.xml",
})

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("ERROR: boto3 not installed.  Run:  pip install boto3")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

BUCKET_NAME     = "hdr-to-sdr-website"
DISTRIBUTION_ID = "E1WBXGO4C77I4Y"
AWS_REGION      = "us-east-1"

# Paths / names that are never uploaded
EXCLUDE_NAMES = {
    "deploy.py", "test_deploy.py", "deploy.sh",
    ".git", ".gitignore", ".DS_Store", "__pycache__",
    "node_modules", ".env", ".venv", "venv",
    "Thumbs.db", "desktop.ini",
}
EXCLUDE_EXTENSIONS = {".pyc", ".log", ".tmp", ".swp"}

MIME_MAP: dict[str, str] = {
    ".html":        "text/html; charset=utf-8",
    ".css":         "text/css; charset=utf-8",
    ".js":          "application/javascript; charset=utf-8",
    ".json":        "application/json; charset=utf-8",
    ".svg":         "image/svg+xml",
    ".ico":         "image/x-icon",
    ".png":         "image/png",
    ".jpg":         "image/jpeg",
    ".jpeg":        "image/jpeg",
    ".webp":        "image/webp",
    ".gif":         "image/gif",
    ".woff":        "font/woff",
    ".woff2":       "font/woff2",
    ".ttf":         "font/ttf",
    ".txt":         "text/plain; charset=utf-8",
    ".xml":         "application/xml",
    ".webmanifest": "application/manifest+json",
    ".map":         "application/json",
}

# Long-lived cache for hashed assets; HTML always revalidated
CACHE_CONTROL_MAP: dict[str, str] = {
    ".html":        "public, max-age=0, must-revalidate",
    ".css":         "public, max-age=31536000, immutable",
    ".js":          "public, max-age=31536000, immutable",
    ".png":         "public, max-age=31536000, immutable",
    ".jpg":         "public, max-age=31536000, immutable",
    ".jpeg":        "public, max-age=31536000, immutable",
    ".webp":        "public, max-age=31536000, immutable",
    ".svg":         "public, max-age=31536000, immutable",
    ".gif":         "public, max-age=31536000, immutable",
    ".ico":         "public, max-age=86400",
    ".woff":        "public, max-age=31536000, immutable",
    ".woff2":       "public, max-age=31536000, immutable",
}
DEFAULT_CACHE_CONTROL = "public, max-age=3600"

# ── Pure helper functions (tested in test_deploy.py) ──────────────────────────

def get_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_MAP:
        return MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def get_cache_control(path: Path) -> str:
    return CACHE_CONTROL_MAP.get(path.suffix.lower(), DEFAULT_CACHE_CONTROL)


def should_exclude(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    for part in rel.parts:
        if part in EXCLUDE_NAMES:
            return True

    if path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return True

    return False


def s3_key(path: Path, root: Path) -> str:
    """Convert a local file path to an S3 key (always forward slashes)."""
    rel = path.relative_to(root)
    return str(rel).replace("\\", "/")


def collect_files(root: Path) -> list[Path]:
    """Return the tracked site assets in deterministic order."""
    return sorted(root / key for key in MANAGED_SITE_FILES if (root / key).is_file())


def validate_source(source_dir: Path) -> bool:
    """Reject directories that cannot be the complete production website."""
    if source_dir.resolve() != SITE_ROOT:
        print(f"ERROR: Production deployments must use the fixed site root: {SITE_ROOT}")
        return False

    if not (source_dir / SITE_MARKER).is_file():
        print(f"ERROR: Site marker is missing: {SITE_MARKER}")
        return False

    missing = sorted(key for key in MANAGED_SITE_FILES if not (source_dir / key).is_file())
    if missing:
        print("ERROR: Site root is incomplete. Missing tracked assets:")
        for key in missing:
            print(f"  - {key}")
        return False

    return True


# ── Deploy logic ──────────────────────────────────────────────────────────────

def delete_stale_files(
    s3_client,
    local_keys: set[str],
    dry_run: bool,
) -> tuple[int, list[str]]:
    """Delete S3 objects not present in local_keys. Returns (deleted_count, failed_keys)."""
    paginator = s3_client.get_paginator("list_objects_v2")
    stale: list[str] = []

    for page in paginator.paginate(Bucket=BUCKET_NAME):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key in MANAGED_SITE_FILES and key not in local_keys:
                stale.append(key)

    if not stale:
        return 0, []

    print(f"\n  {len(stale)} stale object(s) to remove from S3:")
    for key in stale:
        print(f"    - {key}")

    if dry_run:
        return 0, []

    deleted = 0
    failed: list[str] = []

    for i in range(0, len(stale), 1000):
        chunk = stale[i : i + 1000]
        try:
            response = s3_client.delete_objects(
                Bucket=BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in chunk]},
            )
            deleted_keys = {
                item.get("Key") for item in response.get("Deleted", [])
                if item.get("Key") in chunk
            }
            failed_keys = {
                item.get("Key") for item in response.get("Errors", [])
                if item.get("Key") in chunk
            }
            failed_keys.update(set(chunk) - deleted_keys - failed_keys)
            deleted += len(deleted_keys)
            failed.extend(sorted(failed_keys))
        except ClientError as exc:
            print(f"  ✗  Delete failed: {exc}")
            failed.extend(chunk)

    return deleted, failed


def upload_files(
    s3_client,
    files: list[Path],
    root: Path,
    dry_run: bool,
) -> tuple[int, list[str]]:
    """Upload files to S3. Returns (uploaded_count, failed_keys)."""
    uploaded = 0
    failed: list[str] = []

    for i, file_path in enumerate(files, 1):
        key         = s3_key(file_path, root)
        mime        = get_mime_type(file_path)
        cache_ctrl  = get_cache_control(file_path)
        size_kb     = file_path.stat().st_size / 1024

        if dry_run:
            print(f"  [DRY]  {key:<55}  {size_kb:>7.1f} KB  {mime}")
            continue

        try:
            s3_client.upload_file(
                Filename  = str(file_path),
                Bucket    = BUCKET_NAME,
                Key       = key,
                ExtraArgs = {
                    "ContentType":  mime,
                    "CacheControl": cache_ctrl,
                },
            )
            print(f"  [{i:>3}/{len(files)}]  ✓  {key:<50}  {size_kb:>6.1f} KB")
            uploaded += 1
        except (ClientError, OSError) as exc:
            print(f"  [{i:>3}/{len(files)}]  ✗  {key}  →  {exc}")
            failed.append(key)

    return uploaded, failed


def invalidate_cloudfront(cf_client, distribution_id: str) -> str | None:
    """Fire a /* invalidation. Returns the invalidation ID or None on error."""
    caller_ref = f"deploy-{int(time.time())}"
    try:
        resp = cf_client.create_invalidation(
            DistributionId    = distribution_id,
            InvalidationBatch = {
                "Paths":           {"Quantity": 1, "Items": ["/*"]},
                "CallerReference": caller_ref,
            },
        )
        inv = resp["Invalidation"]
        print(f"  ✓  Invalidation created")
        print(f"     ID     : {inv['Id']}")
        print(f"     Status : {inv['Status']}")
        print(f"     (Global propagation typically completes within ~60 seconds)")
        return inv["Id"]
    except ClientError as exc:
        print(f"  ✗  CloudFront invalidation failed: {exc}")
        return None


def deploy(
    source_dir: Path,
    dry_run: bool = False,
    confirm_production: bool = False,
) -> bool:
    """
    Full deploy pipeline.
    Returns True on success (all files uploaded + invalidation fired).
    """
    if not validate_source(source_dir):
        return False

    if not dry_run and not confirm_production:
        print("ERROR: Production deployment requires --confirm-production.")
        return False

    print(f"\n{'=' * 62}")
    print(f"  HDR to SDR Website - AWS Deployment")
    print(f"{'=' * 62}")
    print(f"  Source : {source_dir.resolve()}")
    print(f"  Bucket : s3://{BUCKET_NAME}")
    print(f"  CDN    : {DISTRIBUTION_ID}")
    print(f"  Region : {AWS_REGION}")
    if dry_run:
        print(f"  Mode   : DRY RUN - no changes will be made")
    print(f"{'=' * 62}\n")

    files = collect_files(source_dir)
    if not files:
        print("ERROR: No deployable files found in source directory.")
        return False
    files.sort(key=lambda path: s3_key(path, source_dir) == SITE_MARKER)

    local_keys = {s3_key(f, source_dir) for f in files}

    print(f"  {len(files)} file(s) queued for upload:\n")

    try:
        session = boto3.Session(region_name=AWS_REGION)
        s3      = session.client("s3")
        cf      = session.client("cloudfront")
    except NoCredentialsError:
        print("ERROR: AWS credentials not configured.")
        print("       Run 'aws configure' or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.")
        return False

    uploaded, upload_failed = upload_files(s3, files, source_dir, dry_run)

    if dry_run:
        delete_stale_files(s3, local_keys, dry_run)
        print(f"\n  [DRY RUN] Would upload {len(files)} file(s) to s3://{BUCKET_NAME}")
        return True

    if upload_failed:
        print("\n  Upload failed. Skipping stale-file deletion and cache invalidation.")
        print("  Failed uploads:")
        for key in upload_failed:
            print(f"    - {key}")
        return False

    deleted, delete_failed = delete_stale_files(s3, local_keys, dry_run)

    print(f"\n  Upload complete – {uploaded}/{len(files)} succeeded, {deleted} stale removed")

    if delete_failed:
        print("  Stale-file deletion failed. Skipping cache invalidation.")
        print("  Failed deletions:")
        for key in delete_failed:
            print(f"    - {key}")
        return False

    print(f"\n{'─' * 62}")
    print(f"  Invalidating CloudFront cache …")
    inv_id = invalidate_cloudfront(cf, DISTRIBUTION_ID)

    success = inv_id is not None
    print(f"\n{'=' * 62}")
    if success:
        print(f"  Deployment complete.")
        print(f"  Live at: https://hdrtosdr.com")
    else:
        print(f"  Deployment finished with errors - review output above.")
    print(f"{'=' * 62}\n")

    return success


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy HDR to SDR website to S3 + CloudFront",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source", "-s",
        default=str(_SCRIPT_DIR),
        metavar="DIR",
        help="Must resolve to this script's directory (default: that directory)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be uploaded without making any changes",
    )
    parser.add_argument(
        "--confirm-production",
        action="store_true",
        help="Confirm upload, stale managed-file deletion, and cache invalidation",
    )
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"ERROR: Source directory not found: {source}")
        sys.exit(1)

    success = deploy(
        source,
        dry_run=args.dry_run,
        confirm_production=args.confirm_production,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
