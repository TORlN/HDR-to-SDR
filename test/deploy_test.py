#!/usr/bin/env python3
"""Tests for the website deploy script (S3 upload + CloudFront invalidation)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../HDR to SDR Website')))

# Stub boto3/botocore before importing deploy so the module loads without
# the real packages installed. ClientError must be a real exception class
# so Python 3.13's strict `except` validation doesn't reject it.
class _FakeClientError(Exception):
    pass

boto3_stub                        = MagicMock()
botocore_stub                     = MagicMock()
botocore_exc_stub                 = MagicMock()
botocore_exc_stub.ClientError     = _FakeClientError

sys.modules.setdefault("boto3",               boto3_stub)
sys.modules.setdefault("botocore",            botocore_stub)
sys.modules.setdefault("botocore.exceptions", botocore_exc_stub)

import deploy  # noqa: E402  (must come after the sys.modules patch)

from deploy import (
    get_mime_type,
    get_cache_control,
    should_exclude,
    s3_key,
    collect_files,
    upload_files,
    delete_stale_files,
    invalidate_cloudfront,
    BUCKET_NAME,
    DISTRIBUTION_ID,
    DEFAULT_CACHE_CONTROL,
)


class TestGetMimeType(unittest.TestCase):

    def _path(self, name: str) -> Path:
        return Path(f"/fake/{name}")

    def test_html_returns_charset(self):
        self.assertEqual(get_mime_type(self._path("index.html")), "text/html; charset=utf-8")

    def test_css_returns_charset(self):
        self.assertEqual(get_mime_type(self._path("style.css")), "text/css; charset=utf-8")

    def test_js_returns_javascript(self):
        self.assertIn("javascript", get_mime_type(self._path("app.js")))

    def test_png_returns_image(self):
        self.assertEqual(get_mime_type(self._path("logo.png")), "image/png")

    def test_svg_correct(self):
        self.assertEqual(get_mime_type(self._path("icon.svg")), "image/svg+xml")

    def test_woff2_correct(self):
        self.assertEqual(get_mime_type(self._path("font.woff2")), "font/woff2")

    def test_unknown_extension_returns_octet_stream(self):
        self.assertEqual(get_mime_type(self._path("data.xyz")), "application/octet-stream")

    def test_case_insensitive_extension(self):
        self.assertEqual(get_mime_type(self._path("IMAGE.PNG")), "image/png")

    def test_webmanifest(self):
        self.assertEqual(get_mime_type(self._path("site.webmanifest")), "application/manifest+json")


class TestGetCacheControl(unittest.TestCase):

    def _path(self, name: str) -> Path:
        return Path(f"/fake/{name}")

    def test_html_must_revalidate(self):
        cc = get_cache_control(self._path("index.html"))
        self.assertIn("must-revalidate", cc)
        self.assertIn("max-age=0", cc)

    def test_css_immutable(self):
        cc = get_cache_control(self._path("style.css"))
        self.assertIn("immutable", cc)
        self.assertIn("31536000", cc)

    def test_js_immutable(self):
        self.assertIn("immutable", get_cache_control(self._path("bundle.js")))

    def test_unknown_uses_default(self):
        self.assertEqual(get_cache_control(self._path("data.bin")), DEFAULT_CACHE_CONTROL)

    def test_ico_one_day(self):
        self.assertIn("86400", get_cache_control(self._path("favicon.ico")))


class TestShouldExclude(unittest.TestCase):

    def setUp(self):
        self.root = Path("/project")

    def _p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def test_deploy_script_excluded(self):
        self.assertTrue(should_exclude(self._p("deploy.py"), self.root))

    def test_test_file_excluded(self):
        self.assertTrue(should_exclude(self._p("test_deploy.py"), self.root))

    def test_git_directory_excluded(self):
        self.assertTrue(should_exclude(self._p(".git", "config"), self.root))

    def test_pycache_excluded(self):
        self.assertTrue(should_exclude(self._p("__pycache__", "deploy.cpython-311.pyc"), self.root))

    def test_pyc_extension_excluded(self):
        self.assertTrue(should_exclude(self._p("module.pyc"), self.root))

    def test_node_modules_excluded(self):
        self.assertTrue(should_exclude(self._p("node_modules", "lodash", "index.js"), self.root))

    def test_ds_store_excluded(self):
        self.assertTrue(should_exclude(self._p(".DS_Store"), self.root))

    def test_env_excluded(self):
        self.assertTrue(should_exclude(self._p(".env"), self.root))

    def test_html_not_excluded(self):
        self.assertFalse(should_exclude(self._p("index.html"), self.root))

    def test_css_not_excluded(self):
        self.assertFalse(should_exclude(self._p("style.css"), self.root))

    def test_js_not_excluded(self):
        self.assertFalse(should_exclude(self._p("script.js"), self.root))

    def test_nested_asset_not_excluded(self):
        self.assertFalse(should_exclude(self._p("assets", "logo.png"), self.root))

    def test_log_extension_excluded(self):
        self.assertTrue(should_exclude(self._p("debug.log"), self.root))


class TestS3Key(unittest.TestCase):

    def test_root_file(self):
        root = Path("/project")
        self.assertEqual(s3_key(Path("/project/index.html"), root), "index.html")

    def test_nested_file_uses_forward_slashes(self):
        root = Path("/project")
        key  = s3_key(root / "assets" / "images" / "hero.png", root)
        self.assertNotIn("\\", key)
        self.assertEqual(key, "assets/images/hero.png")

    def test_deeply_nested(self):
        root = Path("/project")
        self.assertEqual(s3_key(root / "css" / "vendor" / "normalize.css", root), "css/vendor/normalize.css")


class TestCollectFiles(unittest.TestCase):

    def test_collects_html_css_js(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<html/>")
            (root / "style.css").write_text("body{}")
            (root / "script.js").write_text("console.log(1)")
            names = {f.name for f in collect_files(root)}
            self.assertIn("index.html", names)
            self.assertIn("style.css", names)
            self.assertIn("script.js", names)

    def test_excludes_deploy_script(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<html/>")
            (root / "deploy.py").write_text("# deploy")
            names = {f.name for f in collect_files(root)}
            self.assertNotIn("deploy.py", names)
            self.assertIn("index.html", names)

    def test_excludes_pyc_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<html/>")
            (root / "cache.pyc").write_bytes(b"\x00")
            names = {f.name for f in collect_files(root)}
            self.assertNotIn("cache.pyc", names)

    def test_result_is_sorted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("z.html", "a.html", "m.html"):
                (root / name).write_text(name)
            names = [f.name for f in collect_files(root)]
            self.assertEqual(names, sorted(names))

    def test_empty_directory(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(collect_files(Path(tmp)), [])

    def test_nested_assets_included(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "assets" / "images"
            assets.mkdir(parents=True)
            (assets / "hero.png").write_bytes(b"\x89PNG")
            self.assertTrue(any(f.name == "hero.png" for f in collect_files(root)))


class TestUploadFiles(unittest.TestCase):

    def _make_files(self, tmp: str, names: list[str]) -> list[Path]:
        root = Path(tmp)
        for name in names:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("content")
        return [root / n for n in names]

    def test_uploads_all_files(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html", "style.css"])
            s3    = MagicMock()
            uploaded, failed = upload_files(s3, files, root, dry_run=False)
            self.assertEqual(uploaded, 2)
            self.assertEqual(failed, [])
            self.assertEqual(s3.upload_file.call_count, 2)

    def test_sets_correct_content_type(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html"])
            s3    = MagicMock()
            upload_files(s3, files, root, dry_run=False)
            self.assertIn("text/html", s3.upload_file.call_args.kwargs["ExtraArgs"]["ContentType"])

    def test_sets_cache_control(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html"])
            s3    = MagicMock()
            upload_files(s3, files, root, dry_run=False)
            self.assertIn("CacheControl", s3.upload_file.call_args.kwargs["ExtraArgs"])

    def test_dry_run_does_not_call_s3(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html", "style.css"])
            s3    = MagicMock()
            uploaded, failed = upload_files(s3, files, root, dry_run=True)
            s3.upload_file.assert_not_called()
            self.assertEqual(uploaded, 0)
            self.assertEqual(failed, [])

    def test_failed_uploads_collected(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html", "style.css"])
            s3    = MagicMock()
            s3.upload_file.side_effect = _FakeClientError("Network error")
            uploaded, failed = upload_files(s3, files, root, dry_run=False)
            self.assertEqual(uploaded, 0)
            self.assertEqual(len(failed), 2)

    def test_partial_failure_tracked(self):
        with TemporaryDirectory() as tmp:
            root  = Path(tmp)
            files = self._make_files(tmp, ["index.html", "style.css", "script.js"])
            s3    = MagicMock()

            def side_effect(*args, **kwargs):
                if "style.css" in kwargs.get("Key", ""):
                    raise _FakeClientError("upload failed")

            s3.upload_file.side_effect = side_effect
            uploaded, failed = upload_files(s3, files, root, dry_run=False)
            self.assertEqual(uploaded, 2)
            self.assertEqual(len(failed), 1)


class TestDeleteStaleFiles(unittest.TestCase):

    def _make_s3(self, s3_objects: list[str]) -> MagicMock:
        s3 = MagicMock()
        page = {"Contents": [{"Key": k} for k in s3_objects]} if s3_objects else {}
        paginator = MagicMock()
        paginator.paginate.return_value = [page]
        s3.get_paginator.return_value = paginator
        return s3

    def test_deletes_stale_keys(self):
        s3 = self._make_s3(["old.html", "index.html"])
        deleted, failed = delete_stale_files(s3, {"index.html"}, dry_run=False)
        self.assertEqual(deleted, 1)
        self.assertEqual(failed, [])
        deleted_keys = [o["Key"] for o in s3.delete_objects.call_args.kwargs["Delete"]["Objects"]]
        self.assertIn("old.html", deleted_keys)
        self.assertNotIn("index.html", deleted_keys)

    def test_keeps_local_keys(self):
        s3 = self._make_s3(["index.html", "script.js"])
        deleted, failed = delete_stale_files(s3, {"index.html", "script.js"}, dry_run=False)
        self.assertEqual(deleted, 0)
        s3.delete_objects.assert_not_called()

    def test_dry_run_does_not_delete(self):
        s3 = self._make_s3(["old.html", "index.html"])
        deleted, failed = delete_stale_files(s3, {"index.html"}, dry_run=True)
        self.assertEqual(deleted, 0)
        s3.delete_objects.assert_not_called()

    def test_empty_bucket_no_deletions(self):
        s3 = self._make_s3([])
        deleted, failed = delete_stale_files(s3, {"index.html"}, dry_run=False)
        self.assertEqual(deleted, 0)
        s3.delete_objects.assert_not_called()

    def test_failed_delete_collected(self):
        s3 = self._make_s3(["stale.html"])
        s3.delete_objects.side_effect = _FakeClientError("Access denied")
        deleted, failed = delete_stale_files(s3, set(), dry_run=False)
        self.assertEqual(deleted, 0)
        self.assertEqual(len(failed), 1)

    def test_all_local_keys_removes_nothing(self):
        keys = {"index.html", "style.css", "script.js"}
        s3 = self._make_s3(list(keys))
        deleted, failed = delete_stale_files(s3, keys, dry_run=False)
        self.assertEqual(deleted, 0)
        s3.delete_objects.assert_not_called()


class TestInvalidateCloudFront(unittest.TestCase):

    def _make_cf(self, inv_id: str = "INVA1B2C3D4E5F") -> MagicMock:
        cf = MagicMock()
        cf.create_invalidation.return_value = {
            "Invalidation": {"Id": inv_id, "Status": "InProgress"}
        }
        return cf

    def test_returns_invalidation_id(self):
        self.assertEqual(invalidate_cloudfront(self._make_cf("INV123"), DISTRIBUTION_ID), "INV123")

    def test_calls_correct_distribution(self):
        cf = self._make_cf()
        invalidate_cloudfront(cf, DISTRIBUTION_ID)
        self.assertEqual(
            cf.create_invalidation.call_args.kwargs["DistributionId"],
            DISTRIBUTION_ID,
        )

    def test_invalidates_all_paths(self):
        cf = self._make_cf()
        invalidate_cloudfront(cf, DISTRIBUTION_ID)
        batch = cf.create_invalidation.call_args.kwargs["InvalidationBatch"]
        self.assertIn("/*", batch["Paths"]["Items"])

    def test_returns_none_on_client_error(self):
        cf = MagicMock()
        cf.create_invalidation.side_effect = _FakeClientError("AccessDenied")
        self.assertIsNone(invalidate_cloudfront(cf, DISTRIBUTION_ID))

    def test_caller_reference_unique(self):
        from unittest.mock import patch
        cf1, cf2 = self._make_cf(), self._make_cf()
        with patch("deploy.time.time", side_effect=[1000, 2000]):
            invalidate_cloudfront(cf1, DISTRIBUTION_ID)
            invalidate_cloudfront(cf2, DISTRIBUTION_ID)
        ref1 = cf1.create_invalidation.call_args.kwargs["InvalidationBatch"]["CallerReference"]
        ref2 = cf2.create_invalidation.call_args.kwargs["InvalidationBatch"]["CallerReference"]
        self.assertTrue(ref1.startswith("deploy-"))
        self.assertTrue(ref2.startswith("deploy-"))
        self.assertNotEqual(ref1, ref2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
