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


def parse_instructions(path: Path) -> tuple[dict, list[dict], dict]:
    """Parse instructions.md into (metadata, ordered_sections, typography).

    Each item in ordered_sections is:
        {"title": "<paragraph title>", "subparagraphs": ["<sub 1>", ...]}
    Lines starting with '-' define paragraphs; lines starting with '>' define
    subparagraphs belonging to the preceding paragraph.
    """
    content = path.read_text(encoding="utf-8")
    meta = dict(DEFAULT_META)
    typography = {k: dict(v) for k, v in DEFAULT_TYPOGRAPHY.items()}
    sections: list[dict] = []

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
                    sections.append({"title": item, "subparagraphs": []})
            elif line.startswith(">"):
                sub = line.lstrip("> ").rstrip(";,.").strip()
                if sub and sections:
                    sections[-1]["subparagraphs"].append(sub)

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
            {"title": s, "subparagraphs": []}
            for s in ("Introduction", "Paragraph 1", "Paragraph 2", "Conclusions", "References", "Appendices")
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


def latex_escape_text(text: str) -> str:
    return (
        text.replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def resolve_section_folder(slug: str, index: int) -> Path:
    """Return ROOT / '{index:02d}_{slug}', migrating any existing folder for `slug`."""
    target = ROOT / f"{index:02d}_{slug}"
    if target.exists() and target.is_dir():
        return target

    pattern = re.compile(rf"^(?:\d+_)?{re.escape(slug)}$")
    for entry in sorted(ROOT.iterdir()):
        if entry.is_dir() and entry.name != target.name and pattern.match(entry.name):
            print(f"Renaming section folder: {entry.name}/ -> {target.name}/")
            shutil.move(str(entry), str(target))
            return target

    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_abstract_scaffold(default_abstract: str) -> tuple[str, Path]:
    """Ensure 00_abstract/abstract.tex exists without overwriting user edits."""
    folder = resolve_section_folder("abstract", 0)
    tex_file = folder / "abstract.tex"
    if not tex_file.exists():
        tex_file.write_text(default_abstract.strip() + "\n", encoding="utf-8")
    return folder.name, tex_file


def ensure_section_scaffold(
    section_name: str, index: int, subparagraphs: list[str] | None = None
) -> tuple[str, str, Path]:
    """Create (or migrate) numbered folder `{index:02d}_{slug}/` and starter .tex.

    If `subparagraphs` are listed under this section in instructions.md (lines
    starting with `>`), any missing `\\subsection{...}` is appended cleanly to
    the section's `.tex` file without overwriting existing content.
    """
    subparagraphs = subparagraphs or []
    slug = slugify(section_name)
    folder = resolve_section_folder(slug, index)
    folder_name = folder.name

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
        expected_bib_line = f"\\bibliography{{{folder_name}/{slug}}}"
        if not tex_file.exists():
            tex_file.write_text(
                f"""% Automatically managed references section
\\bibliographystyle{{unsrtnat}}
{expected_bib_line}
""",
                encoding="utf-8",
            )
        else:
            existing_ref = tex_file.read_text(encoding="utf-8")
            updated_ref = re.sub(
                r"\\bibliography\{[^}]+\}",
                lambda _: expected_bib_line,
                existing_ref,
            )
            if updated_ref != existing_ref:
                tex_file.write_text(updated_ref, encoding="utf-8")
        return folder_name, slug, tex_file

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
can be placed inside this appendices folder and included here.
""",
                encoding="utf-8",
            )
        else:
            safe_name = latex_escape_text(section_name)
            tex_file.write_text(
                f"""\\section{{{safe_name}}}
\\label{{sec:{slug}}}

Content for \\emph{{{safe_name}}} goes here. Place section figures in this
folder's \\texttt{{images/}} subdirectory and tables in \\texttt{{tables/}}.
""",
                encoding="utf-8",
            )

    # Ensure any '>' subparagraphs from instructions.md exist as \subsection blocks
    if subparagraphs and tex_file.exists():
        existing = tex_file.read_text(encoding="utf-8")
        additions: list[str] = []
        for sub_title in subparagraphs:
            sub_slug = slugify(sub_title)
            safe_sub = latex_escape_text(sub_title)
            label_marker = f"\\label{{subsec:{sub_slug}}}"
            title_marker = f"\\subsection{{{safe_sub}}}"
            if label_marker not in existing and title_marker not in existing:
                additions.append(
                    f"\n\\subsection{{{safe_sub}}}\n"
                    f"\\label{{subsec:{sub_slug}}}\n\n"
                    f"Content for \\emph{{{safe_sub}}} goes here.\n"
                )
        if additions:
            tex_file.write_text(
                existing.rstrip() + "\n" + "".join(additions),
                encoding="utf-8",
            )

    return folder_name, slug, tex_file


def generate_main_tex(meta: dict, sections: list[dict], typography: dict) -> None:
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

    abstract_folder, _ = ensure_abstract_scaffold(meta["abstract"])

    section_records: list[tuple[str, str, str]] = []
    for idx, sec in enumerate(sections, start=1):
        name = sec["title"]
        folder_name, slug, _ = ensure_section_scaffold(
            name, idx, sec.get("subparagraphs", [])
        )
        section_records.append((folder_name, slug, name))

    graphics_paths = []
    for folder_name, slug, _ in section_records:
        if slug not in ("references", "reference"):
            graphics_paths.append(f"{{{folder_name}/images/}}{{{folder_name}/}}")
    graphics_path_str = "".join(graphics_paths)

    input_lines: list[str] = []
    appendix_started = False
    for folder_name, slug, name in section_records:
        if slug in ("appendices", "appendix"):
            if not appendix_started:
                input_lines.append("")
                input_lines.append(r"\appendix")
                appendix_started = True
            input_lines.append(f"\\input{{{folder_name}/{slug}}}")
        elif slug in ("references", "reference"):
            input_lines.append("")
            input_lines.append(f"\\input{{{folder_name}/{slug}}}")
        else:
            input_lines.append(f"\\input{{{folder_name}/{slug}}}")

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
  {{\\raggedright\\fontsize{{{sec_size}}}{{{sec_size + 2.5}}}\\selectfont{sec_fmt}}}
  {{\\thesection.}}{{0.5em}}{{}}
\\titlespacing*{{\\section}}{{0pt}}{{10pt plus 2pt minus 2pt}}{{4pt plus 1pt minus 1pt}}

\\titleformat{{\\subsection}}
  {{\\raggedright\\fontsize{{{subsec_size}}}{{{subsec_size + 2.5}}}\\selectfont{subsec_fmt}}}
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
    \\noindent\\textbf{{Abstract---}}\\input{{{abstract_folder}/abstract}}\\par
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


def render_abstract_html(meta: dict) -> str:
    """Render 00_abstract/abstract.tex to inline HTML (falling back to meta['abstract'])."""
    abstract_tex = ROOT / "00_abstract" / "abstract.tex"
    if not abstract_tex.exists():
        return html.escape(meta["abstract"])

    raw_tex = abstract_tex.read_text(encoding="utf-8").strip()
    if not raw_tex:
        return html.escape(meta["abstract"])

    labels, cites = parse_aux(ROOT / "main.aux")
    prepped = preprocess_tex_for_html(raw_tex, labels, cites)
    pandoc = shutil.which("pandoc")
    if pandoc:
        proc = subprocess.run(
            [pandoc, "--from=latex", "--to=html5", "--mathjax"],
            input=prepped,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            rendered = proc.stdout.strip()
            # Unwrap a single <p>...</p> so Abstract flows inline after "Abstract—"
            m_single = re.fullmatch(r"<p>(.*)</p>", rendered, flags=re.DOTALL)
            if m_single and "<p>" not in m_single.group(1):
                return m_single.group(1).strip()
            return rendered
    return html.escape(raw_tex)


def render_sections_html(sections: list[dict]) -> tuple[str, list[dict]]:
    """Convert section .tex files from numbered folders to clean HTML + TOC."""
    html_parts: list[str] = []
    toc_entries: list[dict] = []
    pandoc = shutil.which("pandoc")
    labels, cites = parse_aux(ROOT / "main.aux")

    for idx, sec in enumerate(sections, start=1):
        name = sec["title"]
        slug = slugify(name)
        folder_name = f"{idx:02d}_{slug}"
        folder = ROOT / folder_name
        tex_file = folder / f"{slug}.tex"
        if not tex_file.exists():
            continue

        if slug in ("references", "reference"):
            toc_entries.append({"id": "references", "label": "References", "children": []})
            bib_file = folder / f"{slug}.bib"
            ref_items = []
            if bib_file.exists():
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
                f'  <h1>References</h1>\n'
                f'  <ol class="ref-list">{refs_html}</ol>\n'
                f'</section>'
            )
            continue

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
            rendered = f"<h1>{html.escape(name)}</h1><pre>{html.escape(raw_tex)}</pre>"

        # Prefix section/subsection headings with their exact LaTeX numbers from main.aux
        def _number_heading(m: re.Match) -> str:
            tag, hid, inner = m.group(1), m.group(2), m.group(3)
            if hid in labels:
                num = labels[hid]["num"]
                sep = ". " if tag == "h1" else " "
                return f'<{tag} id="{hid}">{html.escape(num)}{sep}{inner}</{tag}>'
            return m.group(0)

        rendered = re.sub(r'<(h[123])\s+id="([^"]+)">(.*?)</\1>', _number_heading, rendered)

        # Collect rendered subsections (h2) for nested sidebar TOC
        sub_toc: list[tuple[str, str]] = []
        for m_sub in re.finditer(r'<h2\s+id="([^"]+)">(.*?)</h2>', rendered, flags=re.DOTALL):
            sub_id = m_sub.group(1)
            sub_txt = re.sub(r"<[^>]+>", "", m_sub.group(2)).strip()
            sub_toc.append((sub_id, sub_txt))
        toc_entries.append({"id": slug, "label": name, "children": sub_toc})

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
            r'src="(?!https?://|/|' + re.escape(folder_name) + r'/)([^"]+)"',
            f'src="{folder_name}/images/\\1"',
            rendered,
        )
        html_parts.append(f'<section id="{html.escape(slug)}" class="paper-section">\n{rendered}\n</section>')

    return "\n\n".join(html_parts), toc_entries


def css_font_stack(font_name: str) -> str:
    clean = font_name.strip().replace('"', "")
    low = clean.lower()
    if "times" in low:
        return '"Times New Roman", Times, "Nimbus Roman", "Liberation Serif", Georgia, serif'
    if any(k in low for k in ("arial", "helvetica", "sans", "calibri", "roboto", "inter")):
        return f'"{clean}", Arial, Helvetica, sans-serif'
    if any(k in low for k in ("courier", "mono", "consolas")):
        return f'"{clean}", "Courier New", Courier, monospace'
    return f'"{clean}", "Times New Roman", Times, Georgia, serif'


def css_weight_style(fmt: str) -> tuple[str, str]:
    f = fmt.lower()
    weight = "700" if "bold" in f else "400"
    style = "italic" if "italic" in f else "normal"
    return weight, style


def generate_index_html(meta: dict, sections: list[dict], typography: dict) -> None:
    abstract_html = render_abstract_html(meta)
    body_html, toc_entries = render_sections_html(sections)
    updated_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    toc_lines: list[str] = []
    for entry in toc_entries:
        item_html = f'<li><a href="#{html.escape(entry["id"])}">{html.escape(entry["label"])}</a>'
        if entry["children"]:
            sub_items = "\n".join(
                f'<li><a href="#{html.escape(cid)}">{html.escape(clabel)}</a></li>'
                for cid, clabel in entry["children"]
            )
            item_html += f'\n<ul class="toc-sub">\n{sub_items}\n</ul>'
        item_html += "</li>"
        toc_lines.append(item_html)
    toc_html = "\n".join(toc_lines)

    t_title = typography["title"]
    t_sec = typography["section"]
    t_sub = typography["subsection"]
    t_body = typography["body"]

    title_font = css_font_stack(t_title["font"])
    sec_font = css_font_stack(t_sec["font"])
    sub_font = css_font_stack(t_sub["font"])
    body_font = css_font_stack(t_body["font"])

    title_weight, title_style = css_weight_style(t_title["format"])
    sec_weight, sec_style = css_weight_style(t_sec["format"])
    sub_weight, sub_style = css_weight_style(t_sub["format"])
    body_weight, body_style = css_weight_style(t_body["format"])

    title_size = t_title["size"]
    sec_size = t_sec["size"]
    sub_size = t_sub["size"]
    body_size = t_body["size"]

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
      --ink: #000000;
      --ui-ink: #1a202c;
      --muted: #4a5568;
      --accent: #1d4ed8;
      --latex-link: #000099;
      --accent-soft: #eff6ff;
      --border: #e2e8f0;
      --shadow: 0 4px 24px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--ui-ink);
      line-height: 1.5;
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
      color: var(--ui-ink);
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
      color: var(--ui-ink);
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
      font-size: 0.85rem;
      display: block;
      padding: 0.2rem 0.45rem;
      border-radius: 6px;
    }}
    .toc a:hover {{
      color: var(--accent);
      background: var(--accent-soft);
    }}
    .toc-sub {{
      list-style: none;
      margin: 0.15rem 0 0.25rem;
      padding-left: 0.85rem;
      border-left: 1px solid var(--border);
    }}
    .toc-sub a {{
      font-size: 0.78rem;
      padding: 0.14rem 0.4rem;
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

    /* === Paper Sheet: Mirrors LaTeX main.tex typography from instructions.md === */
    .paper-card {{
      background: var(--paper);
      border: 1px solid #cbd5e1;
      border-radius: 4px;
      max-width: 210mm;
      margin: 0 auto;
      padding: 16mm 18mm 22mm;
      box-shadow: var(--shadow);
      color: var(--ink);
      font-family: {body_font};
      font-size: {body_size}pt;
      font-weight: {body_weight};
      font-style: {body_style};
      line-height: 1.25;
    }}
    .paper-running-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-family: {body_font};
      font-size: {max(body_size - 1, 7)}pt;
      border-bottom: 0.4pt solid #000;
      padding-bottom: 3pt;
      margin-bottom: 14pt;
    }}
    .paper-running-author {{
      font-style: italic;
    }}
    .paper-header {{
      text-align: center;
      margin-bottom: 10pt;
    }}
    .paper-title {{
      font-family: {title_font};
      font-size: {title_size}pt;
      font-weight: {title_weight};
      font-style: {title_style};
      line-height: 1.22;
      margin: 0 0 8pt;
      color: var(--ink);
    }}
    .paper-author {{
      font-family: {body_font};
      font-size: 10pt;
      font-weight: 400;
      line-height: 1.2;
      margin: 0 0 3pt;
    }}
    .paper-affiliation {{
      font-family: {body_font};
      font-size: 9pt;
      font-style: italic;
      line-height: 1.22;
      color: var(--ink);
      margin: 0 0 2pt;
    }}
    .paper-contacts {{
      font-family: "Courier New", Courier, ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 9pt;
      line-height: 1.22;
      color: var(--ink);
    }}
    .frontmatter-rule-box {{
      border-top: 0.4pt solid #000;
      border-bottom: 0.4pt solid #000;
      padding: 6pt 0;
      margin: 8pt 0 12pt;
      font-family: {body_font};
      font-size: {body_size}pt;
      font-weight: {body_weight};
      font-style: {body_style};
      line-height: 1.28;
      text-align: justify;
    }}
    .abstract-line {{
      margin: 0 0 5pt;
    }}
    .keywords-line {{
      margin: 0;
    }}
    .keywords-value {{
      font-style: italic;
    }}
    .paper-columns {{
      column-count: 2;
      column-gap: 6mm;
      font-family: {body_font};
      font-size: {body_size}pt;
      font-weight: {body_weight};
      font-style: {body_style};
      line-height: 1.25;
      text-align: justify;
      hyphens: auto;
    }}
    .paper-columns a {{
      color: var(--latex-link);
      text-decoration: none;
    }}
    .paper-columns a:hover {{
      text-decoration: underline;
    }}
    .paper-columns code {{
      font-family: "Courier New", Courier, ui-monospace, monospace;
      font-size: 0.95em;
    }}
    .paper-section {{
      margin-bottom: 8pt;
    }}
    .paper-section p {{
      margin: 0 0 5pt;
    }}
    /* Paragraph Titles (\\section) */
    .paper-section h1 {{
      font-family: {sec_font};
      font-size: {sec_size}pt;
      font-weight: {sec_weight};
      font-style: {sec_style};
      line-height: 1.22;
      margin: 10pt 0 4pt;
      color: var(--ink);
      break-after: avoid;
      text-align: left;
      hyphens: none;
    }}
    .paper-section:first-child h1:first-child {{
      margin-top: 0;
    }}
    /* Paragraph Subtitles (\\subsection) */
    .paper-section h2 {{
      font-family: {sub_font};
      font-size: {sub_size}pt;
      font-weight: {sub_weight};
      font-style: {sub_style};
      line-height: 1.22;
      margin: 8pt 0 3pt;
      color: var(--ink);
      break-after: avoid;
      text-align: left;
      hyphens: none;
    }}
    .paper-section h3 {{
      font-family: {sub_font};
      font-size: {body_size}pt;
      font-weight: 700;
      font-style: italic;
      line-height: 1.22;
      margin: 6pt 0 2pt;
      color: var(--ink);
      break-after: avoid;
      text-align: left;
    }}
    .paper-section figure {{
      margin: 8pt 0;
      text-align: center;
      break-inside: avoid;
    }}
    .paper-section figure img {{
      max-width: 100%;
      height: auto;
    }}
    .paper-section figcaption,
    .paper-section caption {{
      font-family: {body_font};
      font-size: {max(body_size - 0.5, 7)}pt;
      margin-bottom: 4pt;
      text-align: center;
      color: var(--ink);
    }}
    .paper-section table {{
      width: 100%;
      border-collapse: collapse;
      margin: 8pt 0;
      font-family: {body_font};
      font-size: {body_size}pt;
      break-inside: avoid;
      text-align: left;
    }}
    .paper-section th, .paper-section td {{
      padding: 2.5pt 4pt;
    }}
    .paper-section thead th {{
      border-top: 0.9pt solid #000;
      border-bottom: 0.5pt solid #000;
      font-weight: 700;
    }}
    .paper-section tbody tr:last-child td {{
      border-bottom: 0.9pt solid #000;
    }}
    .ref-list {{
      list-style: none;
      padding-left: 0;
      margin: 4pt 0 0;
      font-family: {body_font};
      font-size: {body_size}pt;
      line-height: 1.25;
      text-align: left;
    }}
    .ref-list li {{
      margin-bottom: 4pt;
      padding-left: 16pt;
      text-indent: -16pt;
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
      .paper-card {{ padding: 1.25rem; }}
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
        <div><strong>Layout:</strong> 2-Column Journal (A4)</div>
        <div><strong>Font:</strong> {html.escape(t_body["font"])}</div>
        <div><strong>Sizes:</strong> {title_size} / {sec_size} / {sub_size} / {body_size} pt</div>
        <div><strong>Updated:</strong> {updated_utc}</div>
      </div>
    </aside>

    <div>
      <article id="view-html" class="paper-card">
        <div class="paper-running-header">
          <span class="paper-running-author">{html.escape(meta["author"])}</span>
          <span>1</span>
        </div>

        <div class="paper-header">
          <h1 class="paper-title">{html.escape(meta["title"])}</h1>
          <div class="paper-author">{html.escape(meta["author"])}</div>
          <div class="paper-affiliation">{html.escape(meta["affiliation"])}</div>
          <div class="paper-contacts">{html.escape(meta["contacts"])}</div>
        </div>

        <section id="abstract" class="frontmatter-rule-box">
          <p class="abstract-line"><strong>Abstract&mdash;</strong>{abstract_html}</p>
          <p class="keywords-line"><strong>Keywords&mdash;</strong><span class="keywords-value">{html.escape(meta["keywords"])}</span></p>
        </section>

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
    summary = ", ".join(
        f"{s['title']} ({len(s['subparagraphs'])} sub)" if s["subparagraphs"] else s["title"]
        for s in sections
    )
    print(f"Parsed {len(sections)} sections from instructions.md: {summary}")
    generate_main_tex(meta, sections, typography)
    print("Generated main.tex")
    compile_pdf()
    print(f"Compiled {MAIN_PDF.name} ({MAIN_PDF.stat().st_size:,} bytes)")
    generate_index_html(meta, sections, typography)
    print(f"Generated {INDEX_HTML.name} ({INDEX_HTML.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
