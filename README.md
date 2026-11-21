# GenShip — Scientific Paper Repository

Modular LaTeX manuscript repository with automated synchronization from [`instructions.md`](instructions.md), PDF compilation ([`main.pdf`](main.pdf)), and public HTML landing page ([`index.html`](index.html)) via GitHub Pages.

## Repository Structure

```text
GenShip/
├── instructions.md      # Single source of truth for paper metadata, section order, and typography
├── main.tex             # Auto-generated master LaTeX file (2-column scientific journal format)
├── main.pdf             # Compiled PDF output
├── index.html           # Public HTML landing page & PDF preview (GitHub Pages)
├── scripts/refresh.py   # Syncs instructions.md, scaffolds new folders, compiles PDF & HTML
├── introduction/        # Section .tex, images/, and tables/
├── paragraph_1/         # Section .tex, images/, and tables/
├── paragraph_2/         # Section .tex, images/, and tables/
├── conclusions/         # Section .tex, images/, and tables/
├── references/          # references.tex and references.bib
└── appendices/          # Appendix .tex, images/, and tables/
```

## Refresh & Build

Whenever `instructions.md` or any section content is updated:

```bash
python3 scripts/refresh.py
```

This automatically:
1. Creates missing folders (`<section>/`, `images/`, `tables/`) for newly added sections in `instructions.md`.
2. Regenerates `main.tex` with the section order and formatting rules defined in `instructions.md`.
3. Runs `pdflatex` + `bibtex` + `pdflatex` + `pdflatex` to update `main.pdf` and automatically renumber all figures, tables, equations, sections, and citations.
4. Rebuilds `index.html` with synchronized numbering and embedded PDF preview.
