# Fonts

Self-hosted so neither page's first paint depends on a third-party origin. Each
file is the latin subset Google Fonts serves for that family, downloaded once and
committed. The @font-face rules are in `../landing.css` (landing page) and
`../../styles.css` (console).

| File | Family | Axes kept | Size | Source | License |
|---|---|---|---|---|---|
| `newsreader-latin.woff2` | Newsreader, roman | wght 400–500, opsz pinned at 72 (display) | 57 KB | Google Fonts, v26 | SIL OFL 1.1 |
| `newsreader-italic-latin.woff2` | Newsreader, italic | wght 400, opsz 72 | 23 KB | Google Fonts, v26 | SIL OFL 1.1 |
| `schibsted-grotesk-latin.woff2` | Schibsted Grotesk | wght 400–700 | 47 KB | Google Fonts, v7 | SIL OFL 1.1 |
| `martian-mono-latin.woff2` | Martian Mono | wght 300–600 | 24 KB | Google Fonts, v6 | SIL OFL 1.1 |

Newsreader's full variable file, with its optical-size axis, is about 280 KB for
both styles. Pinning the optical size to its display end, where the face is
used, brings that to 80 KB.

All four families are released under the SIL Open Font License 1.1, which
permits redistribution with the software.
