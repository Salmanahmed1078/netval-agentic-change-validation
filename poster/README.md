# Phase 5: Research poster and standee

| File | Product | Size |
|---|---|---|
| `NetVal_Poster_A3.pdf` | Research poster, landscape | A3, 420 × 297 mm |
| `NetVal_Standee_2.5x5ft.pdf` | Roll-up standee, portrait | 30 × 60 in (762 × 1524 mm) |

Both PDFs are vector files (text, diagram and charts), so they print sharply at any size. The only raster image is the university logo.

## One design system for both

- `style.css` holds the colours, typefaces (IBM Plex Sans and Mono), header, section style, tables and key-number tiles. Both pages load it.
- `arch.svg` is the method diagram, shared by both pages.
- `charts.py` draws three charts (`assets/chart_*.svg`) from `results/all_results.json`, so every number matches the paper.
- `build.py` holds the shared wording and the two layouts. It prints both PDFs with headless Chrome.

## Rebuild

```bash
.venv/bin/python poster/charts.py
.venv/bin/python poster/build.py
```

The QR code encodes the GitHub repository URL; it was decoded and checked after rendering.
