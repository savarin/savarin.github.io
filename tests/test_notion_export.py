#!/usr/bin/env python3
"""Black-box regression tests for the Notion HTML export converter.

Run from the repository root with BeautifulSoup installed:
    python3 -m unittest discover -s tests -v

NOTION_CONVERTER can point to another converter script. The fixtures exercise
actual export structures while assertions check content, public URLs, and
filesystem safety rather than generated HTML serialization details.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup

CONVERTER = os.environ.get(
    "NOTION_CONVERTER",
    str(Path(__file__).resolve().parents[1] / "scripts" / "notion_export.py"),
)
COMMAND = json.loads(os.environ.get("NOTION_CONVERTER_COMMAND", "null")) or [sys.executable, CONVERTER]
HOME_ID = "9b392f33-cd0a-4ef9-814d-35cbbea10d1b"
LEARNING_ID = "f8bf3625-5b44-44d0-9417-0ce63cefe757"
NESTED_ID = "a38e136d-592c-4f7b-be7f-802e22d5bd79"
TITLE = "🌉❄️📝"
HOME = f"{TITLE} {HOME_ID.replace('-', '')}.html"
LEARNING = f"Learning {LEARNING_ID.replace('-', '')}.html"
NESTED = f"Nested & More {NESTED_ID.replace('-', '')}.html"
CODE = 'def f(x):\n    # café & <tag>\n    return "\\n" + str(x)\n\n\t# actual tab; two trailing spaces  \n'
EQUATION = r"\binom{n + k - 1}{k - 1}"
ASSET_NAME = "plot #1 + café.svg"
ASSET_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="2"><rect width="4" height="2" fill="red"/></svg>'


def esc(text):
    import html
    return html.escape(text, quote=True)


def page(title, page_id, content, extra_header=""):
    # Newlines inside blank p blocks are authored content; indentation between
    # elements is intentionally absent, matching Notion's minified body output.
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>' + esc(title) +
        '</title><style>body{white-space:pre-wrap}.icon[data-emoji]::before{content:attr(data-emoji)}</style></head><body>' +
        f'<article id="{page_id}" class="page sans"><header><h1 class="page-title">{esc(title)}</h1>' +
        '<p class="page-description"></p>' + extra_header + '</header><div class="page-body">' + content +
        '</div></article><span class="sans" style="font-size:14px;padding-top:2em"></span></body></html>'
    )


def snapshot(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*") if p.is_file()
    }


class NotionConversionRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(CONVERTER).is_file() and "NOTION_CONVERTER_COMMAND" not in os.environ:
            raise FileNotFoundError(f"Converter not found: {CONVERTER}")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="notion-regression-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.source = self.workspace / "export"
        self.output = self.workspace / "site"
        self.source.mkdir()
        self.section = self.source / TITLE
        self.section.mkdir()
        self.nested_dir = self.section / "Learning"
        self.nested_dir.mkdir()
        self.asset_dir = self.nested_dir / "Nested & More"
        self.asset_dir.mkdir()
        (self.asset_dir / ASSET_NAME).write_bytes(ASSET_BYTES)
        (self.section / "table.csv").write_bytes(b'Author,Title\r\n"A, B",Example\r\n')
        learning_href = quote(f"{TITLE}/{LEARNING}")
        home_body = (
            '<blockquote id="quote">Exact prose: A &amp; B — café.</blockquote>'
            '<p id="blank">\n</p>'
            f'<figure class="link-to-page"><a id="to-learning" data-notion-page-id="{LEARNING_ID}" href="{learning_href}">Learning</a></figure>'
            '<p id="formatted">one <strong>two</strong><br>three <em>four</em> five.</p>'
            '<p><a id="external" href="https://example.com/path?q=A%2BB&amp;z=2#anchor">external</a> '
            '<a id="email" href="mailto:hello@example.com?subject=Hello%20world">email</a></p>'
            f'<p><a id="download" href="{quote(TITLE)}/table.csv">CSV</a></p>'
        )
        (self.source / HOME).write_text(page(TITLE, HOME_ID, home_body))
        nested_href = quote(f"Learning/{NESTED}")
        learning_body = (
            '<h2 id="section-one">A section</h2>'
            f'<p><a id="to-home" href="../{quote(HOME)}#quote">Home</a></p>'
            f'<p><a id="to-nested" href="{nested_href}?view=table#equation">Nested</a></p>'
            '<p><a id="self" href="#section-one">This section</a></p>'
            '<p><a id="not-exported" href="https://www.notion.so/Unexported-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">External Notion page</a></p>'
        )
        (self.section / LEARNING).write_text(page("Learning", LEARNING_ID, learning_body))
        nested_body = (
            f'<p><a id="nested-home" href="../../{quote(HOME)}">Home</a> '
            f'<a id="nested-parent" href="../{quote(LEARNING)}#section-one">Parent</a></p>'
            f'<pre class="code code-wrap" data-notion-code-syntax="python" id="source-code"><code class="language-python" style="white-space:pre-wrap;word-break:break-all">{esc(CODE)}</code></pre>'
            '<p id="equation">Formula '
            f'<span class="notion-text-equation-token" data-notion-inline-equation="{esc(EQUATION)}">'
            '<span class="katex"><span class="katex-html" aria-hidden="true"><span class="mord">n</span></span></span>'
            '</span> is preserved.</p>'
            f'<figure class="image" id="picture"><a href="{quote("Nested & More/" + ASSET_NAME)}"><img alt="A diagram" src="{quote("Nested & More/" + ASSET_NAME)}"></a><figcaption>Caption &amp; detail</figcaption></figure>'
            '<details class="toggle" id="toggle" open=""><summary>Visible summary</summary><div class="indented"><p id="inside-toggle">Nested content</p></div></details>'
            '<ol class="numbered-list" start="9"><li>Nine</li></ol><ol class="numbered-list numbered-list-digits-2" start="10"><li>Ten</li></ol>'
        )
        properties = '<table class="properties"><tbody><tr class="property-row property-row-text"><th>Title</th><td>Property value</td></tr></tbody></table>'
        (self.nested_dir / NESTED).write_text(page("Nested & More", NESTED_ID, nested_body, properties))
        self.before = snapshot(self.source)

    def convert(self, output=None, ok=True, extra=()):
        result = subprocess.run(
            COMMAND + [str(self.source), str(output or self.output)] + list(extra),
            text=True, capture_output=True, timeout=60,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        return result

    def document(self, relative):
        path = self.output / relative
        self.assertTrue(path.is_file(), f"Missing generated page: {relative}")
        return BeautifulSoup(path.read_text(), "html.parser")

    def resolved_link(self, relative, element_id, attribute="href"):
        element = self.document(relative).find(id=element_id)
        self.assertIsNotNone(element, f"Lost block id {element_id}")
        value = element[attribute]
        url = urlsplit(value)
        target = ((self.output / relative).parent / unquote(url.path)).resolve() if url.path else (self.output / relative).resolve()
        return target, url.query, unquote(url.fragment)

    def test_existing_site_urls_and_nested_hierarchy_are_preserved(self):
        self.convert()
        expected = ["index.html", f"index/{LEARNING}", f"index/Learning/{NESTED}"]
        for path in expected:
            self.document(path)
        # This is the existing deployed URL contract, not a proposed new slug scheme.
        self.assertEqual(self.document("index.html").select_one('.page-title').get_text(), TITLE)

    def test_exported_emoji_link_keeps_its_rendering_class(self):
        # Notion stores some emoji as data attributes rendered by .icon::before.
        # Losing that class produces a silent, empty space beside the page link.
        source = self.source / HOME
        source.write_text(source.read_text().replace('>Learning</a>', '><span class="icon" data-emoji="🟧"></span>Learning</a>'))
        self.convert()
        anchor = self.document("index.html").select_one('#to-learning')
        icon = anchor.select_one('.icon[data-emoji]')
        self.assertIsNotNone(icon)
        self.assertEqual(icon['data-emoji'], '🟧')
        self.assertEqual(anchor.select_one('.page-link-label').get_text(), 'Learning')

    def test_prose_formatting_and_intentional_blank_block_survive(self):
        self.convert()
        doc = self.document("index.html")
        self.assertEqual(doc.select_one('#quote').get_text(), "Exact prose: A & B — café.")
        self.assertEqual(doc.select_one('#formatted').get_text(), "one twothree four five.")
        self.assertEqual(doc.select_one('#formatted strong').get_text(), "two")
        self.assertEqual(doc.select_one('#formatted em').get_text(), "four")
        self.assertIsNotNone(doc.select_one('#formatted br'))
        # An empty block may be represented by <br> or a class, but cannot vanish.
        self.assertIsNotNone(doc.select_one('#blank'))
        self.assertEqual(doc.select_one('#blank').get_text(strip=True), "")

    def test_code_preserves_exact_whitespace_and_literal_escapes(self):
        self.convert()
        doc = self.document(f"index/Learning/{NESTED}")
        # Highlighting may add spans but must never modify tabs, escapes, trailing
        # spaces, leading indentation, or terminal blank lines.
        self.assertEqual(doc.select_one('#source-code code').get_text(), CODE)

    def test_equation_markup_and_tex_survive(self):
        self.convert()
        doc = self.document(f"index/Learning/{NESTED}")
        equation = doc.select_one('[data-notion-inline-equation]')
        self.assertIsNotNone(equation)
        self.assertEqual(equation['data-notion-inline-equation'], EQUATION)
        self.assertIsNotNone(equation.select_one('.katex .katex-html .mord'))
        self.assertEqual(equation.select_one('.mord').get_text(), "n")

    def test_relative_navigation_and_query_fragment_are_correct(self):
        self.convert()
        cases = [
            ('index.html', 'to-learning', f'index/{LEARNING}', '', ''),
            (f'index/{LEARNING}', 'to-home', 'index.html', '', 'quote'),
            (f'index/{LEARNING}', 'to-nested', f'index/Learning/{NESTED}', 'view=table', 'equation'),
            (f'index/{LEARNING}', 'self', f'index/{LEARNING}', '', 'section-one'),
            (f'index/Learning/{NESTED}', 'nested-home', 'index.html', '', ''),
            (f'index/Learning/{NESTED}', 'nested-parent', f'index/{LEARNING}', '', 'section-one'),
        ]
        for page_path, element_id, target, query, fragment in cases:
            with self.subTest(page=page_path, link=element_id):
                self.assertEqual(self.resolved_link(page_path, element_id), ((self.output / target).resolve(), query, fragment))

    def test_external_links_are_not_accidentally_rewritten(self):
        self.convert()
        doc = self.document('index.html')
        self.assertEqual(doc.select_one('#external')['href'], 'https://example.com/path?q=A%2BB&z=2#anchor')
        self.assertEqual(doc.select_one('#email')['href'], 'mailto:hello@example.com?subject=Hello%20world')
        doc = self.document(f'index/{LEARNING}')
        self.assertEqual(doc.select_one('#not-exported')['href'], 'https://www.notion.so/Unexported-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')

    def test_assets_with_reserved_characters_are_copied_and_resolve(self):
        self.convert()
        doc = self.document(f'index/Learning/{NESTED}')
        image = doc.select_one('#picture img')
        src = urlsplit(image['src'])
        target = (self.output / 'index/Learning' / unquote(src.path)).resolve()
        self.assertEqual(src.query, '', 'A literal ? in a file name must be encoded')
        self.assertEqual(src.fragment, '', 'A literal # in a file name must be encoded')
        self.assertTrue(target.is_file(), f'Broken image link: {image["src"]}')
        self.assertEqual(target.read_bytes(), ASSET_BYTES)
        csv_path, _, _ = self.resolved_link('index.html', 'download')
        self.assertEqual(csv_path.read_bytes(), b'Author,Title\r\n"A, B",Example\r\n')

    def test_native_toggle_numbering_and_header_properties_survive(self):
        self.convert()
        doc = self.document(f'index/Learning/{NESTED}')
        toggle = doc.select_one('#toggle')
        self.assertIsNotNone(toggle)
        self.assertIn('open', toggle.attrs)
        self.assertEqual(toggle.select_one('summary').get_text(), 'Visible summary')
        self.assertEqual(doc.select_one('#inside-toggle').get_text(), 'Nested content')
        self.assertEqual([ol.get('start') for ol in doc.select('ol.numbered-list')], ['9', '10'])
        self.assertEqual(doc.select_one('header .properties td').get_text(), 'Property value')

    def test_source_tree_is_unchanged(self):
        self.convert()
        self.assertEqual(snapshot(self.source), self.before)

    def test_unowned_nonempty_output_cannot_be_destroyed(self):
        self.output.mkdir()
        (self.output / 'index.html').write_bytes(b'user-maintained index; do not overwrite')
        (self.output / 'CNAME').write_bytes(b'custom.example.com\n')
        (self.output / 'unrelated.txt').write_bytes(b'valuable user content\x00')
        before = snapshot(self.output)
        result = self.convert(ok=False)
        # The safe default is refusal; permission to replace generated output
        # must be explicit and bounded to files recorded as converter-owned.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(snapshot(self.output), before)

    def test_source_and_output_cannot_alias(self):
        result = self.convert(output=self.source, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.source), self.before)

    def test_output_cannot_contain_the_source_tree(self):
        result = self.convert(output=self.workspace, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.source), self.before)

    def test_output_inside_source_is_refused_without_recursive_copy(self):
        result = self.convert(output=self.source / 'site', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.source), self.before)

    def test_equivalent_new_builds_are_deterministic(self):
        self.convert()
        second = self.workspace / 'site-second'
        self.convert(output=second)
        self.assertEqual(snapshot(self.output), snapshot(second))

    def test_replace_only_accepts_a_previously_generated_output(self):
        self.output.mkdir()
        (self.output / 'index.html').write_bytes(b'Hand-authored website')
        before = snapshot(self.output)
        result = self.convert(ok=False, extra=['--replace'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.output), before)

    def test_replace_rebuilds_converter_owned_output(self):
        self.convert()
        source_page = self.source / HOME
        source_page.write_text(source_page.read_text().replace('Exact prose:', 'Updated prose:'))
        self.convert(extra=['--replace'])
        self.assertEqual(self.document('index.html').select_one('#quote').get_text(), 'Updated prose: A & B — café.')
        self.assertEqual(source_page.read_text().count('Updated prose:'), 1)

    def test_replace_never_deletes_an_unowned_file_added_to_output(self):
        self.convert()
        unowned = self.output / 'handwritten-notes.txt'
        unowned.write_bytes(b'User-created file; not in the build manifest')
        before = snapshot(self.output)
        result = self.convert(ok=False, extra=['--replace'])
        # Safe implementations may refuse an unexpected file, or preserve it.
        self.assertTrue(unowned.is_file(), 'Rebuild deleted a file the converter did not create')
        self.assertEqual(unowned.read_bytes(), b'User-created file; not in the build manifest')
        if result.returncode != 0:
            self.assertEqual(snapshot(self.output), before, 'Refused rebuild must leave previous output intact')

    def test_symlinked_asset_cannot_escape_the_source_directory(self):
        secret = self.workspace / 'outside.svg'
        secret.write_bytes(b'Outside-source bytes must never be copied')
        asset = self.asset_dir / ASSET_NAME
        asset.unlink()
        asset.symlink_to(secret)
        before = snapshot(self.source)
        result = self.convert(ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.source), before)
        if self.output.exists():
            self.assertFalse(any(b'Outside-source bytes' in p.read_bytes() for p in self.output.rglob('*') if p.is_file()))

    def test_output_symlink_to_source_is_rejected(self):
        alias = self.workspace / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        result = self.convert(output=alias, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.source), self.before)

    def test_image_page_icons_are_rebased_for_links_and_breadcrumbs(self):
        # Learning's local icon is also rendered in the homepage link and in the
        # nested child's ancestor breadcrumb, which use different base paths.
        source_page = self.section / LEARNING
        image_src = quote('Learning/Nested & More/' + ASSET_NAME)
        source_page.write_text(source_page.read_text().replace(
            '<header>', '<header><div class="page-header-icon"><img class="icon" src="' + image_src + '"></div>'
        ))
        self.convert()
        found = 0
        for path in ['index.html', f'index/{LEARNING}', f'index/Learning/{NESTED}']:
            for image in self.document(path).select('img'):
                u = urlsplit(image['src'])
                if u.scheme or u.netloc:
                    continue
                target = ((self.output / path).parent / unquote(u.path)).resolve()
                self.assertTrue(target.is_file(), f'Image icon broken on {path}: {image["src"]}')
                found += 1
        self.assertGreaterEqual(found, 4)

    def test_exported_favicon_is_preserved(self):
        source_page = self.section / LEARNING
        icon_url = 'data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3C%2Fsvg%3E'
        source_page.write_text(source_page.read_text().replace(
            '</head>', '<link rel="icon" href="' + icon_url + '"></head>'
        ))
        self.convert()
        icon = self.document(f'index/{LEARNING}').head.select_one('link[rel=icon]')
        self.assertIsNotNone(icon)
        self.assertEqual(icon['href'], icon_url)

    def test_replace_refuses_unowned_directory_symlinks(self):
        self.convert()
        target = self.workspace / 'outside'
        target.mkdir()
        (target / 'precious.txt').write_bytes(b'Untouched')
        link = self.output / 'user-created-directory-link'
        link.symlink_to(target, target_is_directory=True)
        result = self.convert(ok=False, extra=['--replace'])
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual((target / 'precious.txt').read_bytes(), b'Untouched')

    def test_replace_refuses_an_edited_generated_page(self):
        self.convert()
        edited = self.output / 'index.html'
        edited.write_bytes(edited.read_bytes() + b'<!-- User edit -->')
        before = snapshot(self.output)
        result = self.convert(ok=False, extra=['--replace'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.output), before)

    def test_output_itself_cannot_be_a_symlink_to_a_generated_site(self):
        self.convert()
        before = snapshot(self.output)
        alias = self.workspace / 'published-site'
        alias.symlink_to(self.output, target_is_directory=True)
        result = self.convert(output=alias, ok=False, extra=['--replace'])
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(snapshot(self.output), before)

    def test_invalid_layout_shapes_fail_cleanly_without_touching_output(self):
        invalid_shapes = [
            [],
            None,
            {'pages': []},
            {LEARNING_ID: []},
            {LEARNING_ID: {'format': 'not a mapping'}},
        ]
        layout = self.workspace / 'layout.json'
        for value in invalid_shapes:
            with self.subTest(layout=value):
                layout.write_text(json.dumps(value))
                result = self.convert(ok=False, extra=['--layout', str(layout)])
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(self.output.exists())
                self.assertEqual(snapshot(self.source), self.before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
