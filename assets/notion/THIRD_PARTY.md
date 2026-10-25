# Bundled assets

The generated site loads these assets locally. Building or reading the output does not request a CDN.

| Asset | Version / source | License |
| --- | --- | --- |
| `vendor/prism.js` | [Prism 1.30.0](https://github.com/PrismJS/prism/tree/v1.30.0), npm `prismjs` | MIT; `vendor/prism-LICENSE` |
| `vendor/prism-keep-markup.js` | [Prism Keep Markup 1.30.0](https://github.com/PrismJS/prism/tree/v1.30.0/plugins/keep-markup) | MIT; `vendor/prism-LICENSE` |
| `vendor/katex/katex.min.css`, `vendor/katex/fonts/` | [KaTeX 0.16.25](https://github.com/KaTeX/KaTeX/tree/v0.16.25), npm `katex` | MIT; `vendor/katex/LICENSE` |
| `vendor/font_gray.svg` | [Original Notion property icon](https://app.notion.com/icons/font_gray.svg) referenced by the export | Original asset retained |
| `vendor/description_gray.svg` | [Original Notion property icon](https://app.notion.com/icons/description_gray.svg) referenced by the export | Original asset retained |

Prism combines the minified core with these grammars, in dependency order: markup, clike, javascript, python, scheme, bash, rust, sql, kotlin, yaml, jsx, css. The HTML export already includes rendered KaTeX equations; only KaTeX's stylesheet and fonts are needed, with no math runtime or formula re-parsing.

`notion.css`, `notion.js`, and `theme.js` are the independent static implementation. The stylesheet was calibrated against the public site on 2026-10-24. No Notion application JavaScript is bundled. The two property icons above are the original assets explicitly referenced in the user's exported HTML.
