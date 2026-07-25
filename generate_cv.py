#!/usr/bin/env python3
"""Generate cv.typ from the website's markdown files using the modern-cv Typst package.

Reads structured data from pages/about.md, pages/research.md, pages/software.md,
pages/teaching.md, pages/talks.md, pages/awards.md, and pages/services.md, then
generates a complete Typst CV file using the modern-cv package for styling.

Usage: python generate_cv.py
Output: cv.typ (compile with: typst compile cv.typ cv.pdf --font-path ./fonts)
"""

import re
from pathlib import Path

# Configuration
# ============================================================================

# Fonts used when compiling the CV PDF. These must be available in the font
# path passed to Typst (see the "Compile CV PDF" step in
# .github/workflows/deploy.yml, which uses `--font-path ./fonts
# --ignore-system-fonts`). To use a font that isn't already bundled, add its
# .otf/.ttf files to that "Download fonts" step (and the Dockerfile) first,
# then reference the exact family name here.
#
# CV_FONT is the body-text font. CV_HEADER_FONT controls the author's name in
# the header (modern-cv styles the name with `header-font`, so a plain
# `#set text(...)` in the preamble will not change it).
CV_FONT = ("Source Sans Pro",)
CV_HEADER_FONT = "Source Sans Pro"

def _typst_font_value(font):
    """Render a font config value as Typst source (a string or an array)."""
    if isinstance(font, str):
        return f'"{font}"'
    inner = ", ".join(f'"{f}"' for f in font)
    return f"({inner},)" if len(tuple(font)) == 1 else f"({inner})"

# ============================================================================
# Utility functions
# ============================================================================


def read_file(base, filename):
    """Read a markdown file, stripping YAML frontmatter."""
    text = (base / filename).read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :]
    return text.strip()


def _convert_bold_italic(text):
    """Convert markdown **bold** -> Typst *bold* and *italic* -> _italic_."""
    bolds = []

    def save_bold(m):
        bolds.append(m.group(1))
        return f"\x00B{len(bolds) - 1}\x00"

    text = re.sub(r"\*\*(.+?)\*\*", save_bold, text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"_\1_", text)
    for i, b in enumerate(bolds):
        text = text.replace(f"\x00B{i}\x00", f"*{b}*")
    return text


def escape_typst(text):
    """Convert markdown-formatted text to Typst content mode."""
    if not text:
        return ""

    links = []

    def save_bare_url(m):
        url = m.group(1).replace('"', '\\"')
        links.append(f'#link("{url}")')
        return f"\x00L{len(links) - 1}\x00"

    text = re.sub(r"<(https?://[^>]+)>", save_bare_url, text)

    def save_md_link(m):
        lt = m.group(1)
        url = m.group(2).replace('"', '\\"')
        lt = lt.replace("\\", "\\\\")
        lt = lt.replace("#", "\\#")
        lt = lt.replace("@", "\\@")
        lt = lt.replace("$", "\\$")
        lt = _convert_bold_italic(lt)
        links.append(f'#link("{url}")[{lt}]')
        return f"\x00L{len(links) - 1}\x00"

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", save_md_link, text)

    text = text.replace("\\", "\\\\")
    text = text.replace("#", "\\#")
    text = text.replace("@", "\\@")
    text = text.replace("$", "\\$")

    text = _convert_bold_italic(text)

    for i, link in enumerate(links):
        text = text.replace(f"\x00L{i}\x00", link)

    return text


def strip_markdown(text):
    """Remove markdown formatting, returning plain text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<https?://[^>]+>", "", text)
    return text.strip()


def parse_table(text):
    """Parse a markdown table into a list of row dicts."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip().startswith("|")]
    if len(lines) < 3:
        return []

    def split_row(line):
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        return cells

    headers = split_row(lines[0])
    rows = []
    for line in lines[2:]:
        cells = split_row(line)
        row = {}
        for i, h in enumerate(headers):
            row[h] = cells[i] if i < len(cells) else ""
        rows.append(row)
    return rows


def extract_section(text, heading):
    """Extract content between a heading and the next heading of same/higher level or ---."""
    escaped = re.escape(heading)
    m = re.search(rf"^{escaped}\s*$", text, re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    level = len(re.match(r"^#+", heading).group())
    end_pat = rf"^(?:#{{{1},{level}}}\s|---\s*$)"
    end_m = re.search(end_pat, text[start:], re.MULTILINE)
    if end_m:
        return text[start : start + end_m.start()].strip()
    return text[start:].strip()


def parse_dropdowns(text):
    """Parse :::{dropdown} blocks into list of (label, content) tuples."""
    results = []
    for m in re.finditer(
        r":::\{dropdown\}\s*(.+?)\n(?::open:\n)?(.*?)\n\s*:::[ \t]*$",
        text,
        re.DOTALL | re.MULTILINE,
    ):
        results.append((m.group(1).strip(), m.group(2).strip()))
    return results


def split_entries(text):
    """Split text into entries separated by blank lines."""
    return [e.strip() for e in re.split(r"\n\s*\n", text.strip()) if e.strip()]


def parse_bullets(text):
    """Parse bullet list items, joining continuation lines."""
    items = []
    current = None
    for line in text.split("\n"):
        m = re.match(r"^[\-\*]\s+(.+)", line)
        if m:
            if current is not None:
                items.append(current)
            current = m.group(1)
        elif current is not None and line.strip():
            current += " " + line.strip()
    if current is not None:
        items.append(current)
    return items


def find_subsections(text):
    """Split text at ### headings, returning list of (title, content)."""
    parts = re.split(r"^###\s+", text, flags=re.MULTILINE)
    results = []
    for part in parts[1:]:
        lines = part.split("\n", 1)
        title = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""
        results.append((title, content))
    return results


def parse_cards(text):
    """Parse :::{card} blocks into list of (name, link, description)."""
    results = []
    for m in re.finditer(
        r":::\{card\}[ \t]+([^\n]+?)\n:link:\s*(.+?)\n(.*?)\n:::(?![:\{])",
        text,
        re.DOTALL,
    ):
        name = m.group(1).strip()
        link = m.group(2).strip()
        desc = m.group(3).strip()
        desc = re.sub(r"```\{image\}.*?```", "", desc, flags=re.DOTALL)
        desc = re.sub(r"\n+", " ", desc).strip()
        if name:
            results.append((name, link, desc))
    return results


def table_to_items(text):
    """Convert markdown table rows to Typst resume-item bullet list."""
    rows = parse_table(text)
    if not rows:
        return ""
    items = []
    for row in rows:
        vals = [escape_typst(v) for v in row.values() if v.strip()]
        if len(vals) >= 2:
            items.append(f"  - {vals[0]}: {', '.join(vals[1:])}")
        elif vals:
            items.append(f"  - {vals[0]}")
    return "#resume-item[\n" + "\n".join(items) + "\n]" if items else ""


def content_with_table(text):
    """Convert text with optional intro paragraph and table to Typst."""
    table_match = re.search(r"^\|", text, re.MULTILINE)
    parts = []
    if table_match:
        before = text[: table_match.start()].strip()
        if before:
            parts.append(escape_typst(before))
        result = table_to_items(text[table_match.start() :])
        if result:
            parts.append(result)
    elif text.strip():
        parts.append(escape_typst(text.strip()))
    return "\n\n".join(parts)


# ============================================================================
# Section generators
# ============================================================================


def gen_preamble():
    """Generate Typst preamble with modern-cv import and author config.
    To change the fonts, edit CV_FONT / CV_HEADER_FONT at the top of this file.
    TODO: Update author information below with your own details.
    """
    return f"""#import "@preview/modern-cv:0.10.0": *

// Use Font Awesome 6 icons and replace "Résumé" with "CV" in footer
#fa-version("6")
#show "Résumé": "CV"

#show: resume.with(
  font: {_typst_font_value(CV_FONT)},
  header-font: {_typst_font_value(CV_HEADER_FONT)},
  author: (
    firstname: "Hongyu",
    lastname: "Zhang",
    email: "honzhang@umass.edu",
    phone: "(+1) 413-545-9365",
    homepage: "https://hzhang.us",
    github: "hzhangic",
    address: "100 Carlson Ave, Newton, MA 02459",
    positions: (
      "Lecturer",
    ),
    custom: (
      (text: "hzhangus", icon: "twitter", link: "https://x.com/hzhangus"),
      (text: "Hongyu Zhang", icon: "linkedin", link: "https://www.linkedin.com/in/hongyu-zhang/"),
      (text: "Hongyu Zhang", icon: "google-scholar", link: "https://scholar.google.ca/citations?user=sWBgI7UAAAAJ&hl=en"),
      (text: "0000-0002-5137-6177", icon: "orcid", link: "https://orcid.org/my-orcid?orcid=0000-0002-5137-6177"),
    ),
  ),
  profile-picture: none,
  date: datetime.today().display(),
  language: "en",
  paper-size: "us-letter",
  accent-color: default-accent-color,
  colored-headers: true,
  show-footer: true,
)

// Enable PDF bookmarks for section navigation
#set heading(bookmarked: true)

// Set PDF document title
#set document(title: "Hongyu Zhang - CV")"""


def gen_education(about):
    """Generate Education section from about.md."""
    section = extract_section(about, "## Education")
    rows = parse_table(section)
    if not rows:
        return ""
    lines = ["= Education\n"]
    for row in rows:
        year = strip_markdown(row.get("Year", ""))
        degree = escape_typst(row.get("Degree", ""))
        institution = escape_typst(row.get("Institution", ""))
        dissertation = escape_typst(row.get("Dissertation/Thesis", ""))
        lines.append(
            f"#resume-entry(\n"
            f"  title: [{degree}],\n"
            f"  location: [{institution}],\n"
            f"  date: [{year}],\n"
            f"  description: [{dissertation}],\n"
            f")"
        )
    return "\n\n".join(lines)


def gen_appointments(about):
    """Generate Academic Appointments section from about.md."""
    section = extract_section(about, "## Appointments")
    rows = parse_table(section)
    if not rows:
        return ""
    lines = ["= Academic Appointments\n"]
    items = []
    for row in rows:
        period = escape_typst(row.get("Period", ""))
        position = escape_typst(row.get("Position", ""))
        items.append(f"  - {period}: {position}")
    lines.append("#resume-item[\n" + "\n".join(items) + "\n]")
    return "\n\n".join(lines)


def gen_research_areas(research):
    """Generate Research Areas section from research.md."""
    section = extract_section(research, "## Research Areas")
    if not section:
        return ""
    bullets = parse_bullets(section)
    if not bullets:
        return ""
    items = tuple(f'"{b}"' for b in bullets)
    return (
        "= Research Areas\n\n"
        "#resume-skill-item(\n"
        '  "Research Focus",\n'
        f"  ({', '.join(items)}),\n"
        ")"
    )

def gen_awards(awards_text):
    """Generate Awards & Honors section from awards.md."""
    rows = parse_table(awards_text)
    if not rows:
        return ""
    lines = ["= Awards & Honors\n"]
    items = []
    for row in rows:
        year = strip_markdown(row.get("Year", ""))
        award = escape_typst(row.get("Award", ""))
        items.append(f"  - {year}: {award}")
    lines.append("#resume-item[\n" + "\n".join(items) + "\n]")
    return "\n\n".join(lines)

def gen_publications(research):
    """Generate Publications section with subsections from research.md."""

    publication_sections = [
        "Work in Progress",
        "Refereed Journal Articles",
        "Papers in Refereed Conference Proceedings",
        "Book Chapters",
        "Refereed Short Papers",
        "Non-refereed Publications",
    ]

    output = ["= Publications", ""]

    for title in publication_sections:
        section = extract_section(research, f"## {title}")
        if not section:
            continue

        # Handle "Non-refereed Publications" separately because it contains ### subsections
        if title == "Non-refereed Publications":
            output.append(f"== {escape_typst(title)}")
            output.append("")

            subheadings = re.findall(r"^### (.+)$", section, flags=re.MULTILINE)

            for subtitle in subheadings:
                subsection = extract_section(section, f"### {subtitle}")
                bullets = parse_bullets(subsection)
                if not bullets:
                    continue

                output.append(f"=== {escape_typst(subtitle)}")
                output.append("")
                output.append("#resume-item[")
                output.extend(f"  - {escape_typst(item)}" for item in bullets)
                output.append("]")
                output.append("")

        else:
            bullets = parse_bullets(section)
            if not bullets:
                continue

            output.append(f"== {escape_typst(title)}")
            output.append("")
            output.append("#resume-item[")
            output.extend(f"  - {escape_typst(item)}" for item in bullets)
            output.append("]")
            output.append("")

    return "\n".join(output)

def gen_grants(grants):
    """Generate Grants section from grants.md."""

    section = extract_section(grants, "## Internal Grants")
    if not section:
        return ""

    lines = ["= Internal Grants"]

    # Process each dropdown (e.g., As PI)
    for label, content in parse_dropdowns(section):
        entries = split_entries(content)

        # Remove existing markdown bullets
        items = [
            f"  - {escape_typst(e.lstrip('- ').strip())}"
            for e in entries
            if e.strip()
        ]

        if items:
            lines.append(f"\n== {escape_typst(label)}\n")
            lines.append("#resume-item[\n" + "\n\n".join(items) + "\n]")

    return "\n".join(lines)

def gen_teaching(teaching):
    """Generate Teaching section from teaching.md."""
    lines = ["= Teaching"]

    # Self-Paced Online Courses subsection
    online = extract_section(teaching, "## Self-Paced Online Courses")
    if online:
        rows = parse_table(online)
        if rows:
            lines.append("\n== Self-Paced Online Courses\n")
            items = []
            for row in rows:
                course = escape_typst(row.get("Course", ""))
                title = escape_typst(row.get("Title", ""))
                website = escape_typst(row.get("Website", ""))
                parts = [f"{course}: {title}"]
                if website:
                    parts.append(website)
                items.append(f"  - {', '.join(parts)}")
            lines.append("#resume-item[\n" + "\n".join(items) + "\n]")

    # Look for course sections with "Courses at" pattern
    for m in re.finditer(r"^## (Courses at .+)$", teaching, re.MULTILINE):
        heading = m.group(0)
        label = m.group(1)
        section = extract_section(teaching, heading)
        if not section:
            continue
        rows = parse_table(section)
        if not rows:
            continue
        lines.append(f"\n== {label}\n")
        items = []
        for row in rows:
            course = escape_typst(row.get("Course", ""))
            title = escape_typst(row.get("Title", ""))
            semesters = escape_typst(row.get("Semesters", ""))
            items.append(f"  - {course}: {title} ({semesters})")
        lines.append("#resume-item[\n" + "\n".join(items) + "\n]")

    return "\n".join(lines)

def gen_mentoring(teaching):
    """Generate Mentoring section from teaching.md."""
    mentoring = extract_section(teaching, "## Mentoring")
    if not mentoring:
        return ""

    lines = ["= Mentoring"]

    subsections = find_subsections(mentoring)
    for title, content in subsections:
        if "Past" in title:
            lines.append(f"\n== {escape_typst(title)}\n")
            dropdowns = parse_dropdowns(content)
            for dd_label, dd_content in dropdowns:
                lines.append(f"\n=== {escape_typst(dd_label)}\n")
                rows = parse_table(dd_content)
                if rows:
                    items = []
                    for row in rows:
                        vals = [escape_typst(v) for v in row.values() if v.strip()]
                        items.append(f"  - {': '.join(vals)}")
                    lines.append("#resume-item[\n" + "\n".join(items) + "\n]")
        else:
            lines.append(f"\n== {escape_typst(title)}\n")
            rows = parse_table(content)
            if rows:
                items = []
                for row in rows:
                    vals = [escape_typst(v) for v in row.values() if v.strip()]
                    items.append(f"  - {': '.join(vals)}")
                lines.append("#resume-item[\n" + "\n".join(items) + "\n]")

    return "\n".join(lines)


def _gen_talks_section(talks, heading, cv_title, include_summary=False):
    """Generate a talks section (workshops, invited talks, or presentations)."""
    section = extract_section(talks, heading)
    if not section:
        return ""

    lines = [f"= {cv_title}"]

    if include_summary:
        m = re.search(r"^\(.+\)$", section, re.MULTILINE)
        if m:
            lines.append(f"\n{escape_typst(m.group())}")

    dropdowns = parse_dropdowns(section)
    for label, content in dropdowns:
        bullets = parse_bullets(content)
        if bullets:
            items = [f"  - {escape_typst(b)}" for b in bullets]
            lines.append(f"\n== {label}\n")
            lines.append("#resume-item[\n" + "\n".join(items) + "\n]")

    return "\n".join(lines)


def gen_talks(talks):
    """Generate Talks section with subsections from talks.md."""

    publication_sections = [
        "Invited Talks",
        "Conference Presentations",
        "Workshop Hosts",
    ]

    output = ["= Talks", ""]

    for title in publication_sections:
        section = extract_section(talks, f"## {title}")
        if not section:
            continue

        # Handle "Other Talks" separately because it contains ### subsections
        if title == "Other Talks":
            output.append(f"== {escape_typst(title)}")
            output.append("")

            subheadings = re.findall(r"^### (.+)$", section, flags=re.MULTILINE)

            for subtitle in subheadings:
                subsection = extract_section(section, f"### {subtitle}")
                bullets = parse_bullets(subsection)
                if not bullets:
                    continue

                output.append(f"=== {escape_typst(subtitle)}")
                output.append("")
                output.append("#resume-item[")
                output.extend(f"  - {escape_typst(item)}" for item in bullets)
                output.append("]")
                output.append("")

        else:
            bullets = parse_bullets(section)
            if not bullets:
                continue

            output.append(f"== {escape_typst(title)}")
            output.append("")
            output.append("#resume-item[")
            output.extend(f"  - {escape_typst(item)}" for item in bullets)
            output.append("]")
            output.append("")

    return "\n".join(output)

def gen_services(services):
    """Generate Services section with subsections from services.md."""

    service_sections = [
        "Departmental Services",
        "Professional Services",
        "Editorial Activities",
        "Conference Activities",
        "Peer Review Activities",
        "Memberships",
    ]

    output = ["= Services", ""]

    for title in service_sections:
        section = extract_section(services, f"## {title}")
        if not section:
            continue

        # Handle "Conference Activities" separately because it contains ### subsections
        if title == "Conference Activities":
            output.append(f"== {escape_typst(title)}")
            output.append("")

            subheadings = re.findall(r"^### (.+)$", section, flags=re.MULTILINE)

            for subtitle in subheadings:
                subsection = extract_section(section, f"### {subtitle}")
                if not subsection:
                    continue

                result = table_to_items(subsection)
                if not result:
                    continue

                output.append(f"=== {escape_typst(subtitle)}")
                output.append("")
                output.append(result)
                output.append("")

        elif title == "Memberships":
            bullets = parse_bullets(section)
            if not bullets:
                continue

            output.append(f"== {escape_typst(title)}")
            output.append("")
            output.append("#resume-item[")
            output.extend(f"  - {escape_typst(item)}" for item in bullets)
            output.append("]")
            output.append("")

        else:
            result = table_to_items(section)
            if not result:
                continue

            output.append(f"== {escape_typst(title)}")
            output.append("")
            output.append(result)
            output.append("")

    return "\n".join(output)


# ============================================================================
# Main
# ============================================================================


def main():
    """Read website markdown files and generate cv.typ."""
    base = Path(__file__).parent
    pages = base / "pages"

    about = read_file(pages, "about.md")
    research = read_file(pages, "publications.md")
    grants = read_file(pages, "grants.md")
    teaching = read_file(pages, "teaching.md")
    talks = read_file(pages, "talks.md")
    awards = read_file(pages, "awards.md")
    services = read_file(pages, "services.md")

    sections = [
        gen_preamble(),
        gen_education(about),
        gen_appointments(about),
        gen_research_areas(research),
        gen_publications(research),
        gen_grants(grants),
        gen_awards(awards),
        gen_teaching(teaching),
        gen_mentoring(teaching),
        gen_talks(talks),
        gen_services(services),
    ]

    output = "\n\n".join(s for s in sections if s)
    out_path = base / "cv.typ"
    out_path.write_text(output, encoding="utf-8")
    print(f"Generated {out_path} ({len(output):,} bytes)")


if __name__ == "__main__":
    main()
