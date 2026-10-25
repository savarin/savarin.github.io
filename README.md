# Notion → static website

`scripts/notion_export.py` converts an extracted **Notion HTML export with subpages** into a portable static site. It reads the export without changing it, retains the existing `index.html` / `index/…` URL layout, and recreates the public Notion site's typography, spacing, colors, icons, covers, code, columns, callouts, and mobile layout.

The reference is [ezzeriesa.notion.site](https://ezzeriesa.notion.site/), compared with the previous [ezzeriesa.com](https://www.ezzeriesa.com/) build on October 24, 2026. The previous build's embedded `notionify` CSS approximated the theme but changed paragraph wrapping and vertical rhythm, omitted page-link icons, and undersized code. This converter keeps the semantic export and applies a measured stylesheet and working site navigation.

## Build and preview

Requires Python 3.11 or newer. [uv](https://docs.astral.sh/uv/) installs the script's pinned Beautiful Soup dependency automatically:

```sh
uv run scripts/notion_export.py \
  "$HOME/Downloads/708eb286-196f-4608-bd84-718c43ede187%2FExportBlock-cbc49819-16fd-4732-81e9-8cc599be158d" \
  .notion-preview \
  --replace \
  --metadata notion-metadata.json \
  --site-url https://www.ezzeriesa.com \
  --notion-site https://ezzeriesa.notion.site \
  --domain www.ezzeriesa.com

python3 -m http.server 8765 --bind 127.0.0.1 --directory .notion-preview
```

Open **http://127.0.0.1:8765/**. The output is also readable by opening `.notion-preview/index.html` directly; search uses a local script rather than a fetch request. The first build accepts a nonexistent or empty output directory. `--replace` only replaces an unmodified directory previously produced by this converter. Edited files, extra files/directories, symlinks, and source/output overlap cause a clear error. A failed build leaves the previous output intact.

Without uv, install `beautifulsoup4==4.14.3` in a Python environment and run the same command with `python3 scripts/notion_export.py`.

For future exports, unzip the HTML export, refresh the formatting snapshot as described below, point the first argument at the directory containing the top-level page, and rerun. Keep `scripts/notion_assets/` and `scripts/notion_metadata.py` beside the converter.

## Formatting that Notion omits from HTML

Notion's HTML export drops author-applied formatting inside code blocks and labels every code block as wrapping. The supplied export loses **46 red-background annotation ranges in ten SICP code blocks**, and incorrectly wraps **98 blocks**. It also omits the distinction between a fixed-width image and one sized to the page.

The included `notion-metadata.json` restores those details for this export: 252 code wrapping settings, 46 annotation ranges, and 39 image layouts. Ordinary builds use this local snapshot and work offline. After obtaining a new export, refresh it from the **public** Notion site:

```sh
uv run scripts/notion_metadata.py \
  "/path/to/extracted/html-export" \
  notion-metadata.json \
  --notion-site https://ezzeriesa.notion.site
```

This read-only command fetches public block records without login cookies or credentials. It saves only formatting, character ranges, and hashes. It verifies that live code exactly matches the export before saving, and the converter verifies those hashes again before applying highlights. A stale snapshot or failed refresh leaves the previous output intact. The metadata tool handles Notion's intermittent server errors with bounded retries, the canonical public endpoint, and smaller batches. Its public API is not an officially supported Notion integration API, so an existing snapshot remains useful if Notion changes that interface.

Author annotations are emitted as HTML spans and preserved through Prism using its Keep Markup plugin. Highlights also remain visible when JavaScript is disabled. Without `--metadata`, conversion still works in export-only mode, but cannot recreate formatting absent from the HTML.

## Output and hosting

The generated directory is the complete deployable site. It contains HTML, original images and CSV downloads, local styles/scripts/fonts, `.nojekyll`, and optional `CNAME` / `sitemap.xml`. It needs no application server, Notion credentials, build-time Notion API calls, or CDN assets. Links to external sites remain external.

The converter deliberately builds into a separate directory. To deploy to GitHub Pages, review the preview and copy the generated `index.html`, `index/`, `assets/`, `.nojekyll`, `CNAME`, and `sitemap.xml` into the repository root, then review the Git diff before committing and pushing to `main`. GitHub Pages serves the generated files from that branch's root. Keep `scripts/`, `tests/`, and this README. `build-report.json` and `.notion-build.json` are local build records and do not need to be published.

## Options

| Option | Purpose |
| --- | --- |
| `--replace` | Rebuild an unchanged, converter-owned output directory. |
| `--root PATH_OR_ID` | Choose the homepage if an export has several top-level HTML files. |
| `--theme dark\|light\|auto` | Initial appearance; dark matches the supplied reference. Visitors can change it in the menu. |
| `--site-url URL` | Canonical URLs and sitemap; supports a hosting subdirectory. |
| `--notion-site URL` | Adds an “Open in Notion” link for the current page. |
| `--domain HOSTNAME` | Writes a GitHub Pages `CNAME`. |
| `--layout FILE` | Optional page-format overrides missing from some exports. |
| `--metadata FILE` | Restore original code highlighting/wrapping and image layout from a verified local snapshot. |

An example layout file:

```json
{
  "pages": {
    "01234567-89ab-cdef-0123-456789abcdef": {
      "page_full_width": true,
      "page_small_text": false
    }
  }
}
```

The 134 pages in the supplied export use the normal width and text size, so this option is unnecessary for this site. Original font classes and cover cropping remain in the HTML.

## Fidelity and behavior

The desktop column is 720px wide with 8px block insets. Titles use 40px type; body text is 16px/24px. Blank blocks remain intentional 40px blocks. `white-space: break-spaces` reproduces Notion's line breaking. On phones, titles become 28px, body lines 22px, blank blocks 38px, and columns stack. Covers, heading hierarchy, grouped lists and page links, syntax colors, and the floating outline follow the current public site. Code/image captions use 14px/19.6px text, 6px vertical padding and a 2px left inset. Nested lists keep 32px indentation; nested quote paragraphs keep their ordinary padding. Callout titles and divider heights follow the live layout.

Breadcrumbs, full-text search, link copying, appearance selection, print, native toggles, and heading navigation work locally. All page/block IDs survive conversion, including links to headings inside closed toggles. Syntax highlighting and equation fonts are bundled. The content remains readable without JavaScript; enhancements such as search require it.

Some details cannot be reconstructed from an HTML export. Database views become the exported table; interactive filters and editing are unavailable. This export contains **8 embed fallbacks** (6 GitHub Gists and 2 Vimeo embeds), which Notion exported as links. They remain accessible links and are listed in `build-report.json`; the converter does not invent missing embed content or execute remote embed scripts. Notion's account controls are replaced by local navigation. The mobile toolbar is compact and functional rather than a copy of Notion's account menu. Native system fonts and emoji can vary across operating systems.

## Verification

Every build checks local HTML links, assets, page fragments, and CSS font URLs before installing the output. The supplied export builds **134 pages and 49 assets** (46 images and 3 CSV files), with **2,368 local references verified**.

The export audit verified all 134 page IDs and 3,895 block IDs, all 252 code blocks with exact whitespace, all 7 equation payloads, and original paragraph/heading/quote text. All 49 assets match the source byte for byte, and all 133 previously served HTML paths remain available. Visual checks covered the homepage, Writing index, cover articles, mobile text and code pages, callouts, stacked columns, equations, and navigation. The live reference and preview were compared at the same browser zoom; the homepage title/quote geometry and Writing index heading positions align.

Run the regression suite:

```sh
uv run --with beautifulsoup4==4.14.3 python -m unittest discover -s tests -v
```

The 53 tests cover content preservation, unusual filenames, relative links and fragments, Notion links, nested breadcrumbs, icons, favicons, deterministic builds, and safe replacement. Metadata tests exercise repeated substrings, Unicode offsets, multiline highlights, captioned code IDs, wrapping, image hashes/widths, stale snapshots, and public API error recovery. Build reports record restored metadata and export limitations.

The second visual pass measured the live and generated SICP code card and caption at the same viewport: 656px card width, 439.171875px card height, and 31.59375px caption height, with matching padding. The small Stack image also matches exactly: 240px outer box, 224px image, and a caption constrained to the image width. A nested numbered-list example matches the live 162px parent and 55px/53px child heights. Browser inspection confirms all 46 author highlight spans (887 characters) survive syntax highlighting with the live background color.

Implementation: `scripts/notion_export.py`; metadata refresh and validation: `scripts/notion_metadata.py`; styling: `scripts/notion_assets/notion.css`; browser enhancements: `scripts/notion_assets/notion.js`. Third-party asset versions and licenses are recorded in `scripts/notion_assets/THIRD_PARTY.md`.
