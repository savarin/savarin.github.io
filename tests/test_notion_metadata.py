"""Regression checks for public metadata and offline code-format restoration.

These tests never call Notion. Public record responses are synthetic, and the
snapshot CLI's network boundary is mocked when its file behavior is exercised.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import html
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bs4 import BeautifulSoup
from scripts import notion_metadata as metadata

ROOT = Path(__file__).resolve().parents[1]
CONVERTER = ROOT / "scripts" / "notion_export.py"
SITE = "https://example.notion.site"
PAGE_ID = "33333333-3333-4333-8333-333333333333"
CODE_ID = "11111111-1111-4111-8111-111111111111"
CAPTION_ID = "22222222-2222-4222-8222-222222222222"
IMAGE_ID = "44444444-4444-4444-8444-444444444444"
SECOND_IMAGE_ID = "55555555-5555-4555-8555-555555555555"
# The first identical occurrence is unmarked. The emoji occupies one Python
# code point but two UTF-16 code units, exposing accidental offset conversions.
RUNS = [
    ["🙂 same "],
    ["same", [["h", "red_background"]]],
    ["\n"],
    ["(\n)", [["h", "red_background"]]],
    [";", [["h", "red_background"]]],
    [" + "],
    ["x", [["b"], ["i"], ["s"], ["_"], ["h", "blue"]]],
    ["\n"],
]
TEXT = "🙂 same same\n(\n); + x\n"
CAPTION_TEXT = "caption\n"


def records():
    return {
        CODE_ID: {"id": CODE_ID, "type": "code", "properties": {"title": copy.deepcopy(RUNS)}, "format": {}},
        CAPTION_ID: {"id": CAPTION_ID, "type": "code", "properties": {"title": [[CAPTION_TEXT]]}, "format": {"code_wrap": True}},
    }


def snapshot():
    return metadata.snapshot_records({CODE_ID: TEXT, CAPTION_ID: CAPTION_TEXT}, records(), SITE)


def hashes(directory):
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*") if p.is_file()
    }


class MetadataRegression(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="notion-metadata-test-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.source = self.workspace / "export"
        self.source.mkdir()
        self.output = self.workspace / "site"
        self.metadata_path = self.workspace / "metadata.json"
        self.page_path = self.source / f"Home {PAGE_ID.replace('-', '')}.html"
        self.page_path.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><title>Home</title>'
            '<style>body{white-space:pre-wrap}</style></head><body>'
            f'<article id="{PAGE_ID}" class="page sans"><header><h1 class="page-title">Home</h1>'
            '<p class="page-description"></p></header><div class="page-body">'
            f'<pre id="{CODE_ID}" class="code code-wrap" data-notion-code-syntax="python">'
            '<code class="language-python" style="white-space:pre-wrap;word-break:break-all;overflow-wrap:anywhere">'
            + html.escape(TEXT) + '</code></pre>'
            f'<figure id="{CAPTION_ID}" data-notion-code-syntax="python">'
            '<pre class="code code-wrap" data-notion-code-syntax="python"><code class="language-python">'
            + CAPTION_TEXT + '</code></pre><figcaption>Caption preserved</figcaption></figure>'
            '</div></article></body></html>',
            encoding="utf-8",
        )
        self.before = hashes(self.source)

    def write_metadata(self, data=None):
        self.metadata_path.write_text(json.dumps(snapshot() if data is None else data), encoding="utf-8")

    def convert(self, *, extra=(), ok=True):
        result = subprocess.run(
            [sys.executable, str(CONVERTER), str(self.source), str(self.output), "--metadata", str(self.metadata_path), *extra],
            capture_output=True, text=True, timeout=60,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def pre(self):
        soup = BeautifulSoup(self.page_path.read_text(), "html.parser")
        return soup, soup.find("pre", id=CODE_ID)

    def add_images(self):
        self.image_file = self.source / "diagram.svg"
        self.image_file.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100"><rect width="300" height="100" fill="red"/></svg>')
        (self.source / "second.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"/>')
        figures = (
            f'<figure id="{IMAGE_ID}" class="image"><img src="diagram.svg" style="width:320px"><figcaption>Diagram caption</figcaption></figure>'
            f'<figure id="{SECOND_IMAGE_ID}" class="image"><img src="second.svg" style="width:200.5px"></figure>'
        )
        self.page_path.write_text(self.page_path.read_text().replace('</div></article>', figures + '</div></article>'))
        public = records()
        public.update({
            IMAGE_ID: {"id": IMAGE_ID, "type": "image", "format": {"block_width": 320, "block_page_width": True, "block_full_width": False, "block_alignment": "left"}},
            SECOND_IMAGE_ID: {"id": SECOND_IMAGE_ID, "type": "image", "format": {"block_width": 200.5, "block_page_width": False}},
        })
        images = metadata.export_images(self.source)
        return images, public

    def image_snapshot(self, images, public):
        return metadata.snapshot_records({CODE_ID: TEXT, CAPTION_ID: CAPTION_TEXT}, public, SITE, images)

    def test_snapshot_uses_exact_codepoint_ranges_without_copying_text(self):
        data = snapshot()
        entry = data["code"][CODE_ID]
        self.assertEqual(data["offset_unit"], "unicode-code-points")
        self.assertEqual(entry["sha256"], hashlib.sha256(TEXT.encode("utf-8")).hexdigest())
        self.assertEqual([(r["start"], r["end"]) for r in entry["ranges"]], [(7, 11), (12, 15), (15, 16), (19, 20)])
        self.assertEqual([r["annotations"] for r in entry["ranges"]], [RUNS[i][1] for i in [1, 3, 4, 6]])
        self.assertNotIn("text", entry)
        self.assertNotIn("properties", entry)
        self.assertNotIn(TEXT, json.dumps(data, ensure_ascii=False))

    def test_missing_wrap_means_false_and_explicit_true_is_preserved(self):
        data = snapshot()
        self.assertIs(data["code"][CODE_ID]["wrap"], False)
        self.assertIs(data["code"][CAPTION_ID]["wrap"], True)

    def test_ranges_restore_repeated_text_emoji_newline_and_punctuation(self):
        soup, pre = self.pre()
        count = metadata.apply_code_metadata(pre, snapshot()["code"][CODE_ID], soup)
        self.assertEqual(count, 4)
        self.assertEqual(pre.code.get_text(), TEXT)
        marks = pre.code.select(".notion-code-annotation")
        self.assertEqual([m.get_text() for m in marks], ["same", "(\n)", ";", "x"])
        self.assertEqual(str(pre.code.contents[0]), "🙂 same ")
        self.assertNotIn("code-wrap", pre.get("class", []))
        self.assertTrue(all("highlight-red_background" in m.get("class", []) for m in marks[:3]))

    def test_combined_bold_italic_strike_underline_and_color_are_retained(self):
        soup, pre = self.pre()
        metadata.apply_code_metadata(pre, snapshot()["code"][CODE_ID], soup)
        mark = pre.code.select(".notion-code-annotation")[-1]
        self.assertTrue({"notion-code-bold", "notion-code-italic", "notion-code-strike", "notion-code-underline", "highlight-blue"}.issubset(mark.get("class", [])))

    def test_stale_entry_is_rejected_before_the_code_dom_changes(self):
        soup, pre = self.pre()
        before = str(pre)
        entry = snapshot()["code"][CODE_ID]
        entry["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            metadata.apply_code_metadata(pre, entry, soup)
        self.assertEqual(str(pre), before)

    def test_range_beyond_code_is_rejected_before_mutation(self):
        soup, pre = self.pre()
        before = str(pre)
        entry = snapshot()["code"][CODE_ID]
        entry["ranges"][-1]["end"] = len(TEXT) + 1
        with self.assertRaises(ValueError):
            metadata.apply_code_metadata(pre, entry, soup)
        self.assertEqual(str(pre), before)

    def test_snapshot_refuses_changed_live_text_or_unavailable_blocks(self):
        for case in ["changed", "missing", "wrong type"]:
            with self.subTest(case=case):
                response = records()
                if case == "changed":
                    response[CODE_ID]["properties"]["title"][0][0] += "extra"
                elif case == "missing":
                    del response[CODE_ID]
                else:
                    response[CODE_ID]["type"] = "text"
                with self.assertRaises(ValueError):
                    metadata.snapshot_records({CODE_ID: TEXT}, response, SITE)

    def test_unknown_annotation_is_not_silently_dropped(self):
        response = records()
        response[CODE_ID]["properties"]["title"][1][1] = [["h", "unknown_background"]]
        with self.assertRaises(ValueError):
            metadata.snapshot_records({CODE_ID: TEXT}, response, SITE)

    def test_converter_restores_caption_owned_code_and_removes_inline_wrapping(self):
        self.write_metadata()
        self.convert()
        soup = BeautifulSoup((self.output / "index.html").read_text(), "html.parser")
        pre = soup.find("pre", id=CODE_ID)
        self.assertEqual(pre.code.get_text(), TEXT)
        self.assertNotIn("code-wrap", pre.get("class", []))
        self.assertNotIn("white-space", pre.code.get("style", ""))
        self.assertNotIn("word-break", pre.code.get("style", ""))
        self.assertNotIn("overflow-wrap", pre.code.get("style", ""))
        self.assertEqual([mark.get_text() for mark in pre.code.select(".notion-code-annotation")], ["same", "(\n)", ";", "x"])
        figure = soup.find("figure", id=CAPTION_ID)
        self.assertEqual(figure.code.get_text(), CAPTION_TEXT)
        self.assertIn("code-wrap", figure.pre.get("class", []))
        self.assertEqual(figure.figcaption.get_text(), "Caption preserved")
        self.assertEqual(hashes(self.source), self.before)

    def test_missing_metadata_entry_preserves_the_previous_output(self):
        self.write_metadata()
        self.convert()
        before = hashes(self.output)
        data = snapshot()
        del data["code"][CAPTION_ID]
        self.write_metadata(data)
        result = self.convert(extra=["--replace"], ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(hashes(self.output), before)
        self.assertEqual(hashes(self.source), self.before)

    def test_stale_metadata_preserves_previous_output_and_changed_export(self):
        self.write_metadata()
        self.convert()
        before_output = hashes(self.output)
        self.page_path.write_text(self.page_path.read_text().replace("🙂 same ", "🙂 changed "))
        before_source = hashes(self.source)
        result = self.convert(extra=["--replace"], ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(hashes(self.output), before_output)
        self.assertEqual(hashes(self.source), before_source)

    def test_invalid_ranges_and_offset_units_fail_without_output(self):
        cases = ["negative", "overlap", "boolean offset", "out of bounds", "unknown unit", "missing unit"]
        for case in cases:
            with self.subTest(case=case):
                self.output = self.workspace / ("invalid-" + case.replace(" ", "-"))
                data = snapshot()
                ranges = data["code"][CODE_ID]["ranges"]
                if case == "negative":
                    ranges[0]["start"] = -1
                elif case == "overlap":
                    ranges[1]["start"] = 9
                elif case == "boolean offset":
                    ranges[0]["start"] = True
                elif case == "out of bounds":
                    ranges[-1]["end"] = len(TEXT) + 1
                elif case == "unknown unit":
                    data["offset_unit"] = "utf-16-code-units"
                else:
                    del data["offset_unit"]
                self.write_metadata(data)
                result = self.convert(ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(hashes(self.source), self.before)

    def test_snapshot_cli_only_reads_export_and_writes_metadata_separately(self):
        with mock.patch.object(metadata, "fetch_records", return_value=records()) as fetch:
            with mock.patch.object(sys, "argv", ["notion_metadata.py", str(self.source), str(self.metadata_path), "--notion-site", SITE]):
                with contextlib.redirect_stdout(io.StringIO()):
                    metadata.main()
        fetch.assert_called_once()
        self.assertEqual(set(fetch.call_args.args[0]), {CODE_ID, CAPTION_ID})
        self.assertEqual(hashes(self.source), self.before)
        data = json.loads(self.metadata_path.read_text())
        self.assertEqual(data["code"], snapshot()["code"])

    def test_failed_snapshot_refresh_keeps_existing_metadata(self):
        self.write_metadata()
        before = self.metadata_path.read_bytes()
        response = records()
        response[CODE_ID]["properties"]["title"][0][0] = "changed"
        with mock.patch.object(metadata, "fetch_records", return_value=response):
            with mock.patch.object(sys, "argv", ["notion_metadata.py", str(self.source), str(self.metadata_path), "--notion-site", SITE]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    metadata.main()
        self.assertNotEqual(error.exception.code, 0)
        self.assertEqual(self.metadata_path.read_bytes(), before)
        self.assertEqual(hashes(self.source), self.before)

    def test_snapshot_output_inside_export_is_rejected_before_network(self):
        with mock.patch.object(metadata, "fetch_records") as fetch:
            with mock.patch.object(sys, "argv", ["notion_metadata.py", str(self.source), str(self.source / "metadata.json"), "--notion-site", SITE]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    metadata.main()
        self.assertNotEqual(error.exception.code, 0)
        fetch.assert_not_called()
        self.assertEqual(hashes(self.source), self.before)

    def test_image_snapshot_preserves_width_page_width_alignment_and_hash(self):
        images, public = self.add_images()
        data = self.image_snapshot(images, public)
        self.assertEqual(data['images'][IMAGE_ID], {
            'sha256': hashlib.sha256(self.image_file.read_bytes()).hexdigest(),
            'width': 320, 'page_width': True, 'alignment': 'left',
        })
        self.assertEqual(data['images'][SECOND_IMAGE_ID], {
            'sha256': hashlib.sha256((self.source / 'second.svg').read_bytes()).hexdigest(),
            'width': 200.5, 'page_width': False, 'alignment': 'center',
        })

    def test_snapshot_rejects_live_image_width_changes(self):
        images, public = self.add_images()
        public[IMAGE_ID]['format']['block_width'] = 321
        with self.assertRaisesRegex(ValueError, 'width differs'):
            self.image_snapshot(images, public)

    def test_snapshot_does_not_silently_approximate_full_bleed_images(self):
        images, public = self.add_images()
        public[IMAGE_ID]['format']['block_full_width'] = True
        with self.assertRaisesRegex(ValueError, 'Full-bleed'):
            self.image_snapshot(images, public)

    def test_converter_applies_page_width_and_fixed_width_without_changing_assets(self):
        images, public = self.add_images()
        before = hashes(self.source)
        self.write_metadata(self.image_snapshot(images, public))
        self.convert()
        soup = BeautifulSoup((self.output / 'index.html').read_text(), 'html.parser')
        page_width = soup.find('figure', id=IMAGE_ID)
        fixed_width = soup.find('figure', id=SECOND_IMAGE_ID)
        self.assertIn('--image-block-width:100%', page_width.get('style', ''))
        self.assertIn('--image-block-width:200.5px', fixed_width.get('style', ''))
        self.assertEqual(page_width.get('data-alignment'), 'left')
        self.assertEqual(fixed_width.get('data-alignment'), 'center')
        self.assertEqual(page_width.figcaption.get_text(), 'Diagram caption')
        self.assertEqual((self.output / 'diagram.svg').read_bytes(), self.image_file.read_bytes())
        self.assertEqual(hashes(self.source), before)

    def test_image_width_or_hash_mismatch_preserves_previous_output(self):
        images, public = self.add_images()
        data = self.image_snapshot(images, public)
        self.write_metadata(data)
        self.convert()
        before_output = hashes(self.output)
        before_source = hashes(self.source)
        for field, value in [('width', 321), ('sha256', '0' * 64)]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(data)
                invalid['images'][IMAGE_ID][field] = value
                self.write_metadata(invalid)
                result = self.convert(extra=['--replace'], ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(hashes(self.output), before_output)
                self.assertEqual(hashes(self.source), before_source)

    def test_changed_image_bytes_are_rejected_without_overwriting_previous_site(self):
        images, public = self.add_images()
        self.write_metadata(self.image_snapshot(images, public))
        self.convert()
        before_output = hashes(self.output)
        self.image_file.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        changed_source = hashes(self.source)
        result = self.convert(extra=['--replace'], ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hashes(self.output), before_output)
        self.assertEqual(hashes(self.source), changed_source)


class PublicRecordFetchRegression(unittest.TestCase):
    """Exercise Notion's routing failures without issuing network requests."""

    @staticmethod
    def response(status, payload=None):
        response = mock.Mock(status_code=status)
        response.json.return_value = payload
        return response

    def test_server_error_batches_are_split_without_losing_records(self):
        public = records()

        def post(url, *, json, timeout):
            self.assertEqual(timeout, 30)
            requests = json["requests"]
            self.assertTrue(all(r.get("spaceId") == "public-space" for r in requests))
            if len(requests) > 1:
                return self.response(500, {"name": "MemcachedCrossCellError"})
            return self.response(200, {"results": [{"value": public[requests[0]["id"]]}]})

        with mock.patch("requests.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.post.side_effect = post
            fetched = metadata.fetch_records([CODE_ID, CAPTION_ID], SITE, "public-space")
        self.assertEqual(fetched, public)
        self.assertEqual(session.post.call_count, 4)
        self.assertEqual([len(c.kwargs["json"]["requests"]) for c in session.post.call_args_list], [2, 2, 1, 1])

    def test_canonical_public_origin_and_wrapped_image_records_are_supported(self):
        image = {"id": CODE_ID, "type": "image", "format": {"block_page_width": True, "block_full_width": False}}
        wrapped = {"results": [{"value": {"value": image, "role": "reader"}}]}
        with mock.patch("requests.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.post.side_effect = [self.response(500), self.response(200, wrapped)]
            fetched = metadata.fetch_records([CODE_ID], SITE)
        self.assertEqual(fetched[CODE_ID], image)
        self.assertEqual([c.args[0] for c in session.post.call_args_list], [SITE + "/api/v3/getRecordValues", "https://www.notion.so/api/v3/getRecordValues"])

    def test_access_denied_is_not_retried_on_another_origin(self):
        with mock.patch("requests.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.post.return_value = self.response(403)
            with self.assertRaisesRegex(ValueError, "HTTP 403"):
                metadata.fetch_records([CODE_ID], SITE)
        session.post.assert_called_once()

    def test_incomplete_or_unavailable_results_are_never_accepted(self):
        for payload in [{"results": []}, {"results": [None]}, {"results": [{"value": {"id": CAPTION_ID}}]}]:
            with self.subTest(payload=payload):
                with mock.patch("requests.Session") as session_factory:
                    session = session_factory.return_value.__enter__.return_value
                    session.post.return_value = self.response(200, payload)
                    with self.assertRaises(ValueError):
                        metadata.fetch_records([CODE_ID], SITE)
                session.post.assert_called_once()

    def test_persistent_single_record_server_failure_names_the_block(self):
        with mock.patch("requests.Session") as session_factory:
            session = session_factory.return_value.__enter__.return_value
            session.post.return_value = self.response(500)
            with self.assertRaisesRegex(ValueError, CODE_ID):
                metadata.fetch_records([CODE_ID], SITE)
        self.assertEqual(session.post.call_count, 2)

    def test_transient_transport_failure_is_retried(self):
        import requests

        payload = {"results": [{"value": records()[CODE_ID]}]}
        with mock.patch("requests.Session") as session_factory, mock.patch.object(metadata.time, "sleep") as sleep:
            session = session_factory.return_value.__enter__.return_value
            session.post.side_effect = [requests.Timeout("temporary"), self.response(200, payload)]
            fetched = metadata.fetch_records([CODE_ID], SITE)
        self.assertEqual(fetched[CODE_ID], records()[CODE_ID])
        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
