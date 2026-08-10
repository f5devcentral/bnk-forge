#!/usr/bin/env bash
#
# book_to_pdf.sh — convert The BNK Forge Developer's Guide (or any other
# Pandoc-friendly Markdown file) to a PDF using Pandoc + xelatex.
#
# Usage:
#   ./book_to_pdf.sh
#       Convert the default guide:
#         in : The_BNK_Forge_Developers_Guide.md
#         out: The_BNK_Forge_Developers_Guide.pdf
#
#   ./book_to_pdf.sh path/to/input.md
#       Convert input.md to input.pdf in the script's working dir.
#
#   ./book_to_pdf.sh path/to/input.md path/to/output.pdf
#       Explicit input + output paths.
#
# Prerequisites (Ubuntu / Debian / WSL):
#   sudo apt update
#   sudo apt install -y pandoc texlive-xetex \
#     texlive-latex-recommended texlive-fonts-recommended \
#     texlive-latex-extra lmodern
#
# The YAML metadata block at the top of the input file drives most of the
# rendering (title, TOC, geometry, colorlinks, numbersections). This script
# layers in syntax-highlighted code blocks and the xelatex engine.

set -euo pipefail

# cd to the directory of this script so relative paths resolve predictably.
cd "$(dirname "$(readlink -f "$0")")"

# --- Inputs ------------------------------------------------------------------

DEFAULT_INPUT="The_BNK_Forge_Developers_Guide.md"
INPUT="${1:-$DEFAULT_INPUT}"

if [[ ! -f "$INPUT" ]]; then
    echo "ERROR: input file not found: $INPUT" >&2
    echo "" >&2
    echo "Usage:" >&2
    echo "  $0 [input.md] [output.pdf]" >&2
    exit 1
fi

OUTPUT="${2:-$(basename "$INPUT" .md).pdf}"

# --- Tool checks -------------------------------------------------------------

missing=()
for tool in pandoc xelatex; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing+=("$tool")
    fi
done

if (( ${#missing[@]} > 0 )); then
    echo "ERROR: missing required tool(s): ${missing[*]}" >&2
    echo "" >&2
    echo "Install with:" >&2
    echo "  sudo apt update" >&2
    echo "  sudo apt install -y pandoc texlive-xetex \\" >&2
    echo "      texlive-latex-recommended texlive-fonts-recommended \\" >&2
    echo "      texlive-latex-extra lmodern" >&2
    exit 1
fi

# --- Convert -----------------------------------------------------------------

echo "Building PDF"
echo "  input : $INPUT"
echo "  output: $OUTPUT"
echo ""

pandoc "$INPUT" \
    --output "$OUTPUT" \
    --from markdown \
    --pdf-engine=xelatex \
    --highlight-style=tango \
    --toc-depth=3 \
    --variable=geometry:margin=1in \
    --variable=mainfont:"Latin Modern Roman" \
    --variable=monofont:"Latin Modern Mono"

# --- Report ------------------------------------------------------------------

if [[ -f "$OUTPUT" ]]; then
    size=$(du -h "$OUTPUT" | cut -f1)
    pages=$(pdfinfo "$OUTPUT" 2>/dev/null | awk '/^Pages:/ {print $2}')
    pages_str="${pages:+, $pages pages}"
    echo ""
    echo "Done: $OUTPUT  (${size}${pages_str})"
else
    echo "ERROR: pandoc completed but $OUTPUT was not produced." >&2
    exit 1
fi
