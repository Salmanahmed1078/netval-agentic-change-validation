# Phase 4: Research paper

- `23i-3019_23i-6029_Phase4_Research_Paper.pdf` is the compiled paper. It uses the Springer Nature `sn-jnl` template (v3.1, Dec 2024) with the two-column `iicol` option and numbered references (`sn-mathphys-num`).
- `23i-3019_23i-6029_Phase4_LaTeX_Source.zip` contains the editable source. You can upload it to Overleaf with **New Project → Upload Project**; it compiles with pdfLaTeX.
- `tex/` holds the same source files: `main.tex`, `references.bib`, the class and style files, and `figures/`.

## Rebuild locally

The paper is built with Tectonic (`brew install tectonic`):

```bash
cd paper/tex && tectonic -X compile main.tex
```

## Figures

The figures are the vector PDFs that `analyze.py` writes to `figures/*.pdf`, copied into `tex/figures/`. The architecture diagram is drawn in TikZ inside `main.tex`.

## Results

All numbers in the paper come from the logged Phase 3 runs in `data/results/*.jsonl` and from `results/all_results.json`.
