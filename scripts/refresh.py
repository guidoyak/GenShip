#!/usr/bin/env python3
"""
GenShip Scientific Paper Build & Sync Script.

Reads `instructions.md` as the single source of truth to:
  1. Ensure each section/paragraph has its own folder (<slug>/, images/, tables/).
  2. Regenerate `main.tex` with the requested two-column scientific journal layout,
     typography rules, running header (author + page number), and section ordering.
  3. Compile `main.tex` -> `main.pdf` (pdflatex -> bibtex -> pdflatex -> pdflatex)
     so all figure, table, equation, section, and literature references renumber automatically.
  4. Build `index.html` as the public GitHub Pages landing page combining a clean
     HTML rendering of the paper with an embedded PDF viewer and download link.
"""

from __future__ import annotations

import datetime
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_FILE = ROOT / "instructions.md"
MAIN_TEX = ROOT / "main.tex"
MAIN_PDF = ROOT / "main.pdf"
INDEX_HTML = ROOT / "index.html"

DEFAULT_META = {
    "title": "Generation Ship: Scientific Paper Working Draft",
    "short_title": "GenShip Working Draft",
    "author": "Author Name",
    "affiliation": "Department / Institution, City, Country",
    "contacts": "author@example.com",
    "abstract": (
        "This provisional working draft initializes the automated LaTeX scientific "
        "paper workflow for the GenShip project. All sections, typography rules, "
        "cross-references (figures, tables, equations), and bibliographic citations "
        "are synchronized directly from instructions.md."
    ),
    "keywords": "Generation Ship, Interstellar Travel, Scientific Paper, LaTeX",
}

DEFAULT_TYPOGRAPHY = {
    "title": {"size": 14, "font": "Times New Roman", "format": "Bold"},
    "section": {"size": 12, "font": "Times New Roman", "format": "Bold"},
    "subsection": {"size": 11, "font": "Times New Roman", "format": "Bold"},
    "body": {"size": 9, "font": "Times New Roman", "format": "Normal"},
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "section"


def parse_style_line(text: str, defaults: dict) -> dict:
    style = dict(defaults)
    m_size = re.search(r"size\s*=\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m_size:
        style["size"] = int(float(m_size.group(1)))
    m_font = re.search(r'font\s*=\s*"([^"]+)"', text, re.IGNORECASE)
    if m_font:
        style["font"] = m_font.group(1).strip()
    m_fmt = re.search(r'format\s*=\s*"([^"]+)"', text, re.IGNORECASE)
    if m_fmt:
        style["format"] = m_fmt.group(1).strip()
    return style


def parse_instructions(path: Path) -> tuple[dict, list[str], dict]:
    """Parse instructions.md into (metadata, ordered_sections, typography)."""
    content = path.read_text(encoding="utf-8")
    meta = dict(DEFAULT_META)
    typography = {k: dict(v) for k, v in DEFAULT_TYPOGRAPHY.items()}
    sections: list[str] = []

    mode = "none"
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        low = line.lower()
        if low.startswith("initial sections"):
            mode = "initial"
            continue
        if "starts the paragraphs" in low or low.startswith("paragraphs:"):
            mode = "paragraphs"
            continue
        if "instructions for the formatting" in low or low.startswith("formatting"):
            mode = "formatting"
            continue

        if mode == "initial":
            # Optional key: value overrides inside Initial sections
            m_kv = re.match(r"^-?\s*([A-Za-z][A-Za-z\s/&]+?)\s*:\s*(.+?);?\s*$", line)
            if m_kv:
                key = m_kv.group(1).strip().lower()
                val = m_kv.group(2).strip().rstrip(";")
                if val:
                    if key in ("title", "paper title", "main title"):
                        meta["title"] = val
                    elif key in ("short title", "running title"):
                        meta["short_title"] = val
                    elif key in ("author", "authors"):
                        meta["author"] = val
                    elif "affiliation" in key:
                        meta["affiliation"] = val
                    elif "contact" in key or "email" in key:
                        meta["contacts"] = val
                    elif key == "abstract":
                        meta["abstract"] = val
                    elif key == "keywords":
                        meta["keywords"] = val

        elif mode == "paragraphs":
            if line.startswith("-") or line.startswith("*"):
                item = line.lstrip("-* ").rstrip(";,.").strip()
                if item:
                    sections.append(item)

        elif mode == "formatting":
            if low.startswith("title:"):
                typography["title"] = parse_style_line(line, typography["title"])
            elif low.startswith("paragraphs titles:") or low.startswith("paragraph titles:"):
                typography["section"] = parse_style_line(line, typography["section"])
            elif low.startswith("paragraphs subtitles:") or low.startswith("paragraph subtitles:"):
                typography["subsection"] = parse_style_line(line, typography["subsection"])
            elif low.startswith("main text:"):
                typography["body"] = parse_style_line(line, typography["body"])

    # Also allow top-level metadata keys anywhere outside formatting block
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if "size=" in line and "font=" in line:
            continue
        m_kv = re.match(r"^(Paper Title|Main Title|Author|Authors|Affiliation|Contacts|Email|Abstract|Keywords)\s*:\s*(.+)$", line, re.IGNORECASE)
        if m_kv:
            key = m_kv.group(1).strip().lower()
            val = m_kv.group(2).strip()
            if val:
                if key in ("paper title", "main title"):
                    meta["title"] = val
                elif key in ("author", "authors"):
                    meta["author"] = val
                elif key == "affiliation":
                    meta["affiliation"] = val
                elif key in ("contacts", "email"):
                    meta["contacts"] = val
                elif key == "abstract":
                    meta["abstract"] = val
                elif key == "keywords":
                    meta["keywords"] = val

    if not sections:
        sections = [
            "Introduction",
            "Paragraph 1",
            "Paragraph 2",
            "Conclusions",
            "References",
            "Appendices",
        ]

    return meta, sections, typography


def latex_format_cmd(fmt: str) -> str:
    f = fmt.lower()
    cmds = []
    if "bold" in f:
        cmds.append(r"\bfseries")
    if "italic" in f:
        cmds.append(r"\itshape")
    if not cmds:
        cmds.append(r"\normalfont")
    return "".join(cmds)


def ensure_section_scaffold(section_name: str) -> tuple[str, Path]:
    """Create folder and starter .tex (and .bib for references) if missing."""
    slug = slugify(section_name)
    folder = ROOT / slug
    folder.mkdir(parents=True, exist_ok=True)

    if slug in ("references", "reference"):
        tex_file = folder / f"{slug}.tex"
        bib_file = folder / f"{slug}.bib"
        if not bib_file.exists():
            bib_file.write_text(
                """@article{hein2012world,
  author  = {Hein, Andreas M. and Pak, Mikhail and P{\\"u}tz, Daniel and B{\\"u}hler, Christian and Reiss, Philipp},
  title   = {World Ships -- Architectures \\& Feasibility Revisited},
  journal = {Journal of the British Interplanetary Society},
  year    = {2012},
  volume  = {65},
  pages   = {119--133}
}

@book{smith2014star,
  author    = {Smith, Cameron M. and Davies, Evan T.},
  title     = {Emigrating Beyond Earth: Human Adaptation and Space Colonization},
  publisher = {Springer},
  year      = {2012}
}
""",
                encoding="utf-8",
            )
        if not tex_file.exists():
            tex_file.write_text(
                f"""% Automatically managed references section
\\bibliographystyle{{unsrtnat}}
\\bibliography{{{slug}/{slug}}}
""",
                encoding="utf-8",
            )
        return slug, tex_file

    # Regular section or appendix
    (folder / "images").mkdir(exist_ok=True)
    (folder / "tables").mkdir(exist_ok=True)
    for sub in ("images", "tables"):
        gitkeep = folder / sub / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

    tex_file = folder / f"{slug}.tex"
    if not tex_file.exists():
        if slug == "introduction":
            tex_file.write_text(
                r"""\section{Introduction}
\label{sec:introduction}

This provisional introduction outlines the structure and workflow of the paper.
Scientific concepts, propulsion architectures, and habitat design requirements
for interstellar generation ships have been extensively explored in the
literature~\cite{hein2012world,smith2014star}.

\subsection{Scope and Objectives}
\label{subsec:scope}

Each section of this manuscript lives in its own dedicated directory containing
its LaTeX source, figures (\texttt{images/}), and tables (\texttt{tables/}).
Cross-references such as \cref{sec:paragraph_1} and \cref{tab:sample_parameters}
update automatically whenever sections, figures, tables, or citations are added
or reordered.
""",
                encoding="utf-8",
            )
        elif slug == "paragraph_1":
            tex_file.write_text(
                r"""\section{Paragraph 1}
\label{sec:paragraph_1}

Content for Paragraph 1 will be developed here. You can organize subsections
using \texttt{\textbackslash subsection\{...\}}, which automatically inherits
the 11\,pt bold Times New Roman styling configured in \texttt{instructions.md}.

\subsection{Methodology Overview}
\label{subsec:methodology}

\Cref{tab:sample_parameters} provides a placeholder table demonstrating
standard journal formatting and automatic cross-referencing across paragraphs.

\begin{table}[htbp]
  \centering
  \caption{Provisional baseline parameters for template verification.}
  \label{tab:sample_parameters}
  \begin{tabular}{@{}lcc@{}}
    \toprule
    Parameter & Symbol & Provisional Value \\
    \midrule
    Target Distance & $d$ & $4.24\text{ ly}$ \\
    Cruise Velocity & $v_c$ & $0.05\,c$ \\
    Mission Duration & $T$ & $\sim 85\text{ yr}$ \\
    \bottomrule
  \end{tabular}
\end{table}
""",
                encoding="utf-8",
            )
        elif slug == "paragraph_2":
            tex_file.write_text(
                r"""\section{Paragraph 2}
\label{sec:paragraph_2}

Content for Paragraph 2 will be developed here. Notice how references to
elements in other folders—such as \cref{tab:sample_parameters} in
\cref{sec:paragraph_1} or literature entries~\cite{hein2012world}—remain
automatically synchronized after every rebuild.

\subsection{Preliminary Discussion}
\label{subsec:discussion}

Figures placed inside \texttt{paragraph\_2/images/} can be included with
\texttt{\textbackslash includegraphics} and referenced anywhere in the document
using \texttt{\textbackslash label\{fig:...\}} and \texttt{\textbackslash cref\{fig:...\}}.
""",
                encoding="utf-8",
            )
        elif slug == "conclusions":
            tex_file.write_text(
                r"""\section{Conclusions}
\label{sec:conclusions}

This provisional manuscript confirms that the modular folder layout, two-column
scientific journal styling, running page headers, automatic numbering of
figures/tables/citations, PDF generation, and public HTML landing page pipeline
are operational.
""",
                encoding="utf-8",
            )
        elif slug in ("appendices", "appendix"):
            tex_file.write_text(
                r"""\section{Supplementary Notes}
\label{app:supplementary}

Additional derivations, extended data tables, and supporting technical notes
can be placed inside the \texttt{appendices/} folder and included here.
""",
                encoding="utf-8",
            )
        else:
            tex_file.write_text(
                f"""\\section{{{section_name}}}
\\label{{sec:{slug}}}

Content for {section_name} goes here. Place section figures in
\\texttt{{{slug}/images/}} and tables in \\texttt{{{slug}/tables/}}.
""",
                encoding="utf-8",
            )

    return slug, tex_file


def generate_main_tex(meta: dict, sections: list[str], typography: dict) -> None:
    body_size = typography["body"]["size"]
    title_size = typography["title"]["size"]
    sec_size = typography["section"]["size"]
    subsec_size = typography["subsection"]["size"]

    title_fmt = latex_format_cmd(typography["title"]["format"])
    sec_fmt = latex_format_cmd(typography["section"]["format"])
    subsec_fmt = latex_format_cmd(typography["subsection"]["format"])

    # extarticle supports 8pt, 9pt, 10pt, 11pt, 12pt, 14pt, 17pt, 20pt
    ext_sizes = {8, 9, 10, 11, 12, 14, 17, 20}
    doc_size_opt = f"{body_size}pt" if body_size in ext_sizes else "9pt"

    slugs_and_names: list[tuple[str, str]] = []
    for name in sections:
        slug, _ = ensure_section_scaffold(name)
        slugs_and_names.append((slug, name))

    graphics_paths = []
    for slug, _ in slugs_and_names:
        if slug not in ("references", "reference"):
            graphics_paths.append(f"{{{slug}/images/}}{{{slug}/}}")
    graphics_path_str = "".join(graphics_paths)

    input_lines: list[str] = []
    appendix_started = False
    for slug, name in slugs_and_names:
        if slug in ("appendices", "appendix"):
            if not appendix_started:
                input_lines.append("")
                input_lines.append(r"\appendix")
                appendix_started = True
            input_lines.append(f"\\input{{{slug}/{slug}}}")
        elif slug in ("references", "reference"):
            input_lines.append("")
            input_lines.append(f"\\input{{{slug}/{slug}}}")
        else:
            input_lines.append(f"\\input{{{slug}/{slug}}}")

    inputs_block = "\n".join(input_lines)

    tex_content = f"""% !TEX program = pdflatex
% ==============================================================================
% Auto-generated from instructions.md by scripts/refresh.py
% Do not edit section ordering manually here; update instructions.md instead.
% ==============================================================================
\\documentclass[{doc_size_opt},twocolumn,a4paper]{{extarticle}}

% --- Encoding & Times New Roman Font ---
\\usepackage[utf8]{{inputenc}}
\\usepackage[T1]{{fontenc}}
\\usepackage{{mathptmx}} % Times New Roman metric-compatible font
\\usepackage{{microtype}}

% --- Page Geometry (Two-Column Scientific Journal Layout) ---
\\usepackage[
  a4paper,
  top=22mm,
  bottom=22mm,
  left=18mm,
  right=18mm,
  columnsep=6mm,
  headheight=14pt,
  headsep=8mm
]{{geometry}}

% --- Running Header (Author + Page Number at Top of Pages) ---
\\usepackage{{fancyhdr}}
\\pagestyle{{fancy}}
\\fancyhf{{}}
\\fancyhead[L]{{\\small\\textit{{{meta["author"]}}}}}
\\fancyhead[R]{{\\small\\thepage}}
\\renewcommand{{\\headrulewidth}}{{0.4pt}}
\\renewcommand{{\\footrulewidth}}{{0pt}}
\\fancypagestyle{{plain}}{{%
  \\fancyhf{{}}%
  \\fancyhead[L]{{\\small\\textit{{{meta["author"]}}}}}%
  \\fancyhead[R]{{\\small\\thepage}}%
  \\renewcommand{{\\headrulewidth}}{{0.4pt}}%
  \\renewcommand{{\\footrulewidth}}{{0pt}}%
}}

% --- Typography Rules from instructions.md ---
% Title: {title_size}pt, {typography["title"]["font"]}, {typography["title"]["format"]}
% Section (Paragraph Title): {sec_size}pt, {typography["section"]["font"]}, {typography["section"]["format"]}
% Subsection (Paragraph Subtitle): {subsec_size}pt, {typography["subsection"]["font"]}, {typography["subsection"]["format"]}
% Main Text: {body_size}pt, {typography["body"]["font"]}, {typography["body"]["format"]}
\\usepackage{{titlesec}}
\\titleformat{{\\section}}
  {{\\fontsize{{{sec_size}}}{{{sec_size + 2.5}}}\\selectfont{sec_fmt}}}
  {{\\thesection.}}{{0.5em}}{{}}
\\titlespacing*{{\\section}}{{0pt}}{{10pt plus 2pt minus 2pt}}{{4pt plus 1pt minus 1pt}}

\\titleformat{{\\subsection}}
  {{\\fontsize{{{subsec_size}}}{{{subsec_size + 2.5}}}\\selectfont{subsec_fmt}}}
  {{\\thesubsection}}{{0.5em}}{{}}
\\titlespacing*{{\\subsection}}{{0pt}}{{8pt plus 2pt minus 1pt}}{{3pt plus 1pt minus 1pt}}

% --- Mathematics, Tables, Figures & Captions ---
\\usepackage{{amsmath,amssymb}}
\\usepackage{{graphicx}}
\\graphicspath{{{graphics_path_str}}}
\\usepackage{{booktabs}}
\\usepackage[font=small,labelfont=bf]{{caption}}
\\usepackage{{subcaption}}
\\usepackage{{balance}}

% --- References & Automatic Cross-Referencing ---
\\usepackage[numbers,sort&compress]{{natbib}}
\\usepackage[colorlinks=true,linkcolor=blue!60!black,citecolor=blue!60!black,urlcolor=blue!60!black]{{hyperref}}
\\usepackage[nameinlink,noabbrev]{{cleveref}}
\\usepackage{{xcolor}}

% --- Paper Metadata ---
\\newcommand{{\\PaperTitle}}{{{meta["title"]}}}
\\newcommand{{\\PaperAuthor}}{{{meta["author"]}}}
\\newcommand{{\\PaperAffiliation}}{{{meta["affiliation"]}}}
\\newcommand{{\\PaperContacts}}{{{meta["contacts"]}}}
\\newcommand{{\\PaperKeywords}}{{{meta["keywords"]}}}

\\begin{{document}}
\\thispagestyle{{fancy}}

% --- Full-Width Title, Author, Affiliation, Abstract & Keywords ---
\\twocolumn[
  \\begin{{@twocolumnfalse}}
    \\begin{{center}}
      {{\\fontsize{{{title_size}}}{{{title_size + 3}}}\\selectfont{title_fmt} \\PaperTitle \\par}}
      \\vspace{{8pt}}
      {{\\fontsize{{10}}{{12}}\\selectfont \\PaperAuthor \\par}}
      \\vspace{{3pt}}
      {{\\fontsize{{9}}{{11}}\\selectfont\\textit{{\\PaperAffiliation}} \\par}}
      {{\\fontsize{{9}}{{11}}\\selectfont\\texttt{{\\PaperContacts}} \\par}}
    \\end{{center}}
    \\vspace{{6pt}}
    \\noindent\\rule{{\\textwidth}}{{0.4pt}}\\par
    \\vspace{{5pt}}
    \\noindent\\textbf{{Abstract---}}{meta["abstract"]}\\par
    \\vspace{{5pt}}
    \\noindent\\textbf{{Keywords---}}\\textit{{\\PaperKeywords}}\\par
    \\vspace{{5pt}}
    \\noindent\\rule{{\\textwidth}}{{0.4pt}}\\par
    \\vspace{{10pt}}
  \\end{{@twocolumnfalse}}
]

% --- Paper Sections (Ordered as in instructions.md) ---
{inputs_block}

\\end{{document}}
"""
    MAIN_TEX.write_text(tex_content, encoding="utf-8")


def run_cmd(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        print(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
        print(proc.stdout[-3000:])
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return proc


def compile_pdf() -> None:
    """Run full LaTeX + BibTeX build pass to resolve all numbers & references."""
    pdflatex_cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
    run_cmd(pdflatex_cmd, ROOT)
    # Run bibtex if aux contains citations/bibdata
    aux_path = ROOT / "main.aux"
    if aux_path.exists() and "\\bibdata" in aux_path.read_text(encoding="utf-8", errors="ignore"):
        proc = subprocess.run(
            ["bibtex", "main"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0:
            print("BibTeX notice/warning:")
            print(proc.stdout)
    run_cmd(pdflatex_cmd, ROOT)
    run_cmd(pdflatex_cmd, ROOT)


def parse_aux(aux_path: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Extract resolved label numbers/types and citation numbers from main.aux."""
    labels: dict[str, dict[str, str]] = {}
    cites: dict[str, str] = {}
    if not aux_path.exists():
        return labels, cites

    text = aux_path.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"\\bibcite\{([^}]+)\}\{\{([^}]+)\}", text):
        cites[m.group(1)] = m.group(2)

    for m in re.finditer(r"\\newlabel\{([^}@]+)\}\{\{([^}]+)\}\{[^}]*\}\{([^}]*)\}", text):
        key, num, title = m.group(1), m.group(2), m.group(3)
        labels[key] = {"num": num, "title": title, "type": "section"}

    for m in re.finditer(r"\\newlabel\{([^}]+)@cref\}\{\{\[([^\]]+)\][^\]]*\]?([^}]*)\}", text):
        key = m.group(1)
        ltype = m.group(2).strip().lower()
        if key in labels:
            labels[key]["type"] = ltype
    return labels, cites


def preprocess_tex_for_html(
    tex: str, labels: dict[str, dict[str, str]], cites: dict[str, str]
) -> str:
    """Replace LaTeX \\cref, \\Cref, \\ref, \\cite with exact resolved markers before pandoc."""
    type_names = {
        "section": "section",
        "subsection": "section",
        "subsubsection": "section",
        "appendix": "appendix",
        "figure": "figure",
        "table": "table",
        "equation": "equation",
    }

    def _ref_replace(m: re.Match, capitalize: bool, include_type: bool) -> str:
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        rendered = []
        for k in keys:
            info = labels.get(k)
            if info:
                num = info["num"]
                tname = type_names.get(info["type"], info["type"])
                if capitalize:
                    tname = tname.capitalize()
                label_txt = f"{tname}~{num}" if include_type else num
                rendered.append(f"\\hyperref[{k}]{{{label_txt}}}")
            else:
                rendered.append(k)
        return ", ".join(rendered)

    tex = re.sub(r"\\Cref\{([^}]+)\}", lambda m: _ref_replace(m, True, True), tex)
    tex = re.sub(r"\\cref\{([^}]+)\}", lambda m: _ref_replace(m, False, True), tex)
    tex = re.sub(r"\\ref\{([^}]+)\}", lambda m: _ref_replace(m, False, False), tex)

    def _cite_replace(m: re.Match) -> str:
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        nums = [cites.get(k, "?") for k in keys]
        first_key = keys[0] if keys else "references"
        return f"\\hyperref[ref-{first_key}]{{[{', '.join(nums)}]}}"

    tex = re.sub(r"\\cite[pt]?\{([^}]+)\}", _cite_replace, tex)
    return tex


def render_sections_html(sections: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Convert section .tex files to clean HTML using pandoc + main.aux numbers."""
    html_parts: list[str] = []
    toc_entries: list[tuple[str, str]] = []
    pandoc = shutil.which("pandoc")
    labels, cites = parse_aux(ROOT / "main.aux")

    bib_candidates = [ROOT / "references" / "references.bib", ROOT / "reference" / "reference.bib"]
    bib_file = next((b for b in bib_candidates if b.exists()), None)

    for name in sections:
        slug = slugify(name)
        tex_file = ROOT / slug / f"{slug}.tex"
        if not tex_file.exists():
            continue

        if slug in ("references", "reference"):
            toc_entries.append(("references", "References"))
            ref_items = []
            if bib_file and bib_file.exists():
                bib_text = bib_file.read_text(encoding="utf-8", errors="ignore")
                entries = re.split(r"@\w+\s*\{", bib_text)[1:]
                parsed_entries = []
                for entry in entries:
                    key = entry.split(",", 1)[0].strip()
                    fields = {}
                    for m in re.finditer(r"(\w+)\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", entry):
                        val = re.sub(r"[{}]", "", m.group(2)).replace(r"\&", "&").strip()
                        val = val.replace('\\"', "")
                        fields[m.group(1).lower()] = val
                    order = int(cites[key]) if key in cites and cites[key].isdigit() else 9999
                    parsed_entries.append((order, key, fields))
                parsed_entries.sort(key=lambda x: x[0])
                for i, (order, key, fields) in enumerate(parsed_entries, start=1):
                    num = str(order) if order < 9999 else str(i)
                    author = fields.get("author", "Unknown Author")
                    title = fields.get("title", key)
                    venue = fields.get("journal") or fields.get("publisher") or ""
                    year = fields.get("year", "")
                    pages = f", pp. {fields['pages']}" if "pages" in fields else ""
                    vol = f", vol. {fields['volume']}" if "volume" in fields else ""
                    ref_items.append(
                        f'<li id="ref-{html.escape(key)}"><strong>[{html.escape(num)}]</strong> '
                        f'{html.escape(author)}, <em>{html.escape(title)}</em>'
                        f'{", " + html.escape(venue) if venue else ""}'
                        f'{html.escape(vol)}{html.escape(pages)}'
                        f'{", " + html.escape(year) if year else ""}.</li>'
                    )
            refs_html = "\n".join(ref_items) if ref_items else "<li>References will appear here as citations are added.</li>"
            html_parts.append(
                f'<section id="references" class="paper-section">\n'
                f'  <h2>References</h2>\n'
                f'  <ol class="ref-list">{refs_html}</ol>\n'
                f'</section>'
            )
            continue

        toc_entries.append((slug, name))
        raw_tex = tex_file.read_text(encoding="utf-8")
        prepped_tex = preprocess_tex_for_html(raw_tex, labels, cites)
        rendered = ""
        if pandoc:
            cmd = [pandoc, "--from=latex", "--to=html5", "--mathjax"]
            proc = subprocess.run(
                cmd,
                input=prepped_tex,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                rendered = proc.stdout.strip()

        if not rendered:
            rendered = f"<h2>{html.escape(name)}</h2><pre>{html.escape(raw_tex)}</pre>"

        # Prefix section/subsection headings with their exact LaTeX numbers from main.aux
        def _number_heading(m: re.Match) -> str:
            tag, hid, inner = m.group(1), m.group(2), m.group(3)
            if hid in labels:
                num = labels[hid]["num"]
                sep = ". " if tag == "h1" else " "
                return f'<{tag} id="{hid}">{html.escape(num)}{sep}{inner}</{tag}>'
            return m.group(0)

        rendered = re.sub(r'<(h[123])\s+id="([^"]+)">(.*?)</\1>', _number_heading, rendered)
        # Prefix table captions with "Table N: "
        def _number_table_caption(m: re.Match) -> str:
            tid, caption_inner = m.group(1), m.group(2)
            if tid in labels:
                num = labels[tid]["num"]
                return f'<table id="{tid}">\n<caption><strong>Table {html.escape(num)}:</strong> {caption_inner}</caption>'
            return m.group(0)

        rendered = re.sub(
            r'<table\s+id="([^"]+)">\s*<caption>(.*?)</caption>',
            _number_table_caption,
            rendered,
            flags=re.DOTALL,
        )
        # Resolve relative image paths in HTML if figures are referenced by filename
        rendered = re.sub(
            r'src="(?!https?://|/|' + re.escape(slug) + r'/)([^"]+)"',
            f'src="{slug}/images/\\1"',
            rendered,
        )
        html_parts.append(f'<section id="{html.escape(slug)}" class="paper-section">\n{rendered}\n</section>')

    return "\n\n".join(html_parts), toc_entries


def generate_index_html(meta: dict, sections: list[str], typography: dict) -> None:
    body_html, toc_entries = render_sections_html(sections)
    updated_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    toc_html = "\n".join(
        f'<li><a href="#{html.escape(slug)}">{html.escape(label)}</a></li>'
        for slug, label in toc_entries
    )
    kw_badges = "\n".join(
        f'<span class="kw-badge">{html.escape(k.strip())}</span>'
        for k in meta["keywords"].split(",")
        if k.strip()
    )

    page_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(meta["title"])}</title>
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }}
    }};
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
  <style>
    :root {{
      --bg: #f6f7f9;
      --paper: #ffffff;
      --ink: #1a202c;
      --muted: #4a5568;
      --accent: #1d4ed8;
      --accent-soft: #eff6ff;
      --border: #e2e8f0;
      --shadow: 0 4px 24px rgba(15, 23, 42, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.65;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
    }}
    .topbar-inner {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 0.75rem 1.5rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--ink);
    }}
    .status-pill {{
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 0.2rem 0.55rem;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid #bfdbfe;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.45rem 0.9rem;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--ink);
      transition: all 0.15s ease;
    }}
    .btn:hover {{
      border-color: #cbd5e1;
      background: #f8fafc;
    }}
    .btn-primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .btn-primary:hover {{
      background: #1e40af;
      border-color: #1e40af;
    }}
    .btn.active-tab {{
      background: var(--accent-soft);
      color: var(--accent);
      border-color: #93c5fd;
    }}
    .layout {{
      max-width: 1180px;
      margin: 1.75rem auto 3rem;
      padding: 0 1.5rem;
      display: grid;
      grid-template-columns: 240px minmax(0, 1fr);
      gap: 1.75rem;
      align-items: start;
    }}
    .sidebar {{
      position: sticky;
      top: 4.5rem;
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.15rem 1.2rem;
      box-shadow: var(--shadow);
    }}
    .sidebar h3 {{
      margin: 0 0 0.7rem;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .toc {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .toc li {{
      margin: 0.35rem 0;
    }}
    .toc a {{
      color: var(--muted);
      text-decoration: none;
      font-size: 0.88rem;
      display: block;
      padding: 0.2rem 0.45rem;
      border-radius: 6px;
    }}
    .toc a:hover {{
      color: var(--accent);
      background: var(--accent-soft);
    }}
    .meta-box {{
      margin-top: 1rem;
      padding-top: 0.9rem;
      border-top: 1px solid var(--border);
      font-size: 0.78rem;
      color: var(--muted);
    }}
    .meta-box div {{
      margin-bottom: 0.35rem;
    }}
    .paper-card {{
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 2.5rem 3rem;
      box-shadow: var(--shadow);
      font-family: "Times New Roman", Times, Georgia, serif;
    }}
    .paper-header {{
      text-align: center;
      padding-bottom: 1.5rem;
      margin-bottom: 1.75rem;
      border-bottom: 1px solid var(--border);
    }}
    .paper-title {{
      font-size: 1.85rem;
      line-height: 1.25;
      margin: 0 0 0.65rem;
      font-weight: 700;
      color: #0f172a;
    }}
    .paper-author {{
      font-size: 1.08rem;
      margin: 0.25rem 0;
      font-weight: 500;
    }}
    .paper-affiliation {{
      font-size: 0.92rem;
      font-style: italic;
      color: var(--muted);
      margin: 0.2rem 0;
    }}
    .paper-contacts {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.82rem;
      color: var(--muted);
    }}
    .abstract-box {{
      background: #f8fafc;
      border-left: 3px solid var(--accent);
      padding: 1rem 1.25rem;
      border-radius: 0 8px 8px 0;
      margin-bottom: 1.25rem;
      font-size: 0.96rem;
    }}
    .keywords {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      align-items: center;
      margin-bottom: 1.75rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .kw-label {{
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      margin-right: 0.2rem;
    }}
    .kw-badge {{
      font-size: 0.78rem;
      background: #f1f5f9;
      color: #334155;
      padding: 0.18rem 0.55rem;
      border-radius: 999px;
      border: 1px solid #e2e8f0;
    }}
    .paper-columns {{
      column-count: 2;
      column-gap: 2.2rem;
      font-size: 0.98rem;
      text-align: justify;
    }}
    .paper-section {{
      margin-bottom: 1.4rem;
    }}
    .paper-section h1, .paper-section h2 {{
      font-size: 1.25rem;
      font-weight: 700;
      margin: 1.2rem 0 0.5rem;
      color: #0f172a;
      break-after: avoid;
      text-align: left;
    }}
    .paper-section h3 {{
      font-size: 1.08rem;
      font-weight: 700;
      margin: 0.9rem 0 0.35rem;
      color: #1e293b;
      break-after: avoid;
      text-align: left;
    }}
    .paper-section table {{
      width: 100%;
      border-collapse: collapse;
      margin: 1rem 0;
      font-size: 0.86rem;
      break-inside: avoid;
      text-align: left;
    }}
    .paper-section th, .paper-section td {{
      padding: 0.4rem 0.5rem;
      border-bottom: 1px solid var(--border);
    }}
    .paper-section thead th {{
      border-top: 2px solid #0f172a;
      border-bottom: 1px solid #0f172a;
      font-weight: 700;
    }}
    .paper-section tbody tr:last-child td {{
      border-bottom: 2px solid #0f172a;
    }}
    .ref-list {{
      list-style: none;
      padding-left: 0;
      font-size: 0.9rem;
      text-align: left;
    }}
    .ref-list li {{
      margin-bottom: 0.5rem;
    }}
    .pdf-view {{
      display: none;
      background: var(--paper);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }}
    .pdf-toolbar {{
      padding: 0.75rem 1.2rem;
      background: #f8fafc;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .pdf-frame {{
      width: 100%;
      height: 82vh;
      border: none;
      display: block;
    }}
    @media (max-width: 960px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      .paper-card {{ padding: 1.5rem; }}
      .paper-columns {{ column-count: 1; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <span>GenShip Paper</span>
        <span class="status-pill">Working Draft</span>
      </div>
      <div class="actions">
        <button id="btn-html" class="btn active-tab" type="button" onclick="showView('html')">HTML Paper View</button>
        <button id="btn-pdf" class="btn" type="button" onclick="showView('pdf')">PDF Preview</button>
        <a class="btn btn-primary" href="main.pdf" target="_blank" rel="noopener">Download PDF</a>
        <a class="btn" href="https://github.com/guidoyak/GenShip" target="_blank" rel="noopener">GitHub Repo</a>
      </div>
    </div>
  </header>

  <main class="layout">
    <aside class="sidebar">
      <h3>Contents</h3>
      <ul class="toc">
        <li><a href="#abstract">Abstract</a></li>
        {toc_html}
      </ul>
      <div class="meta-box">
        <div><strong>Layout:</strong> 2-Column Journal</div>
        <div><strong>Typography:</strong> Times New Roman ({typography["title"]["size"]}/{typography["section"]["size"]}/{typography["subsection"]["size"]}/{typography["body"]["size"]} pt)</div>
        <div><strong>Updated:</strong> {updated_utc}</div>
      </div>
    </aside>

    <div>
      <article id="view-html" class="paper-card">
        <div class="paper-header">
          <h1 class="paper-title">{html.escape(meta["title"])}</h1>
          <div class="paper-author">{html.escape(meta["author"])}</div>
          <div class="paper-affiliation">{html.escape(meta["affiliation"])}</div>
          <div class="paper-contacts">{html.escape(meta["contacts"])}</div>
        </div>

        <section id="abstract" class="abstract-box">
          <strong>Abstract&mdash;</strong>{html.escape(meta["abstract"])}
        </section>

        <div class="keywords">
          <span class="kw-label">Keywords:</span>
          {kw_badges}
        </div>

        <div class="paper-columns">
          {body_html}
        </div>
      </article>

      <section id="view-pdf" class="pdf-view">
        <div class="pdf-toolbar">
          <span>Compiled LaTeX Output (<code>main.pdf</code>)</span>
          <a class="btn" href="main.pdf" download>Save main.pdf</a>
        </div>
        <iframe class="pdf-frame" src="main.pdf" title="Compiled Scientific Paper PDF"></iframe>
      </section>
    </div>
  </main>

  <script>
    function showView(mode) {{
      const htmlEl = document.getElementById('view-html');
      const pdfEl = document.getElementById('view-pdf');
      const btnHtml = document.getElementById('btn-html');
      const btnPdf = document.getElementById('btn-pdf');
      if (mode === 'pdf') {{
        htmlEl.style.display = 'none';
        pdfEl.style.display = 'block';
        btnHtml.classList.remove('active-tab');
        btnPdf.classList.add('active-tab');
      }} else {{
        htmlEl.style.display = 'block';
        pdfEl.style.display = 'none';
        btnHtml.classList.add('active-tab');
        btnPdf.classList.remove('active-tab');
      }}
    }}
  </script>
</body>
</html>
"""
    INDEX_HTML.write_text(page_html, encoding="utf-8")


def main() -> int:
    if not INSTRUCTIONS_FILE.exists():
        print(f"Error: {INSTRUCTIONS_FILE} not found.", file=sys.stderr)
        return 1

    meta, sections, typography = parse_instructions(INSTRUCTIONS_FILE)
    print(f"Parsed {len(sections)} sections from instructions.md: {', '.join(sections)}")
    generate_main_tex(meta, sections, typography)
    print("Generated main.tex")
    compile_pdf()
    print(f"Compiled {MAIN_PDF.name} ({MAIN_PDF.stat().st_size:,} bytes)")
    generate_index_html(meta, sections, typography)
    print(f"Generated {INDEX_HTML.name} ({INDEX_HTML.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
