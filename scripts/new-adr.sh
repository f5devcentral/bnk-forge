#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# BNK Forge ADR & Feature Branch Initialization Helper
# ==============================================================================
# Codifies the lightweight workflow:
# 1. GitHub Issue #<ID> created
# 2. Run: ./scripts/new-adr.sh --issue <ID> --title "short description"
# 3. Generates docs/adr/ADR-<ID>-<title>.md from template
# 4. Creates git branch feat/adr-<ID>-<title> off staging
# ==============================================================================

ISSUE_ID=""
TITLE=""
BASE_BRANCH="staging"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue)
      ISSUE_ID="$2"
      shift 2
      ;;
    --title)
      TITLE="$2"
      shift 2
      ;;
    --base)
      BASE_BRANCH="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 --issue <issue_number> --title <short_title> [--base staging]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "${ISSUE_ID}" || -z "${TITLE}" ]]; then
  echo "Error: Both --issue <number> and --title <title> are required."
  echo "Example: $0 --issue 494 --title release-management"
  exit 1
fi

# Canonical repo URL for ADR links — derived from this checkout's origin so
# forks and internal mirrors produce correct links. Override with BNKFORGE_REPO_URL.
REPO_URL="${BNKFORGE_REPO_URL:-$(git remote get-url origin 2>/dev/null || true)}"
REPO_URL="${REPO_URL%.git}"
REPO_URL="${REPO_URL/git@github.com:/https://github.com/}"
REPO_URL="${REPO_URL:-https://github.com/f5devcentral/bnk-forge}"

# Sanitize title to lowercase slug
SLUG=$(echo "${TITLE}" | tr '[:upper:]' '[:lower:]' | tr ' _' '-' | sed 's/[^a-z0-9-]//g')
BRANCH_NAME="feat/adr-${ISSUE_ID}-${SLUG}"
ADR_FILE="docs/adr/ADR-${ISSUE_ID}-${SLUG}.md"

echo "======================================================================"
echo "Creating ADR Design Doc & Work Branch"
echo "Issue #:       ${ISSUE_ID}"
echo "ADR File:      ${ADR_FILE}"
echo "Git Branch:    ${BRANCH_NAME}"
echo "Base Branch:   ${BASE_BRANCH}"
echo "======================================================================"

# Ensure on base branch and up to date
git checkout "${BASE_BRANCH}"
git pull origin "${BASE_BRANCH}" 2>/dev/null || true

# Create feature branch
git checkout -b "${BRANCH_NAME}"

# Create ADR document template if not already existing
mkdir -p docs/adr

if [[ ! -f "${ADR_FILE}" ]]; then
  cat <<EOF > "${ADR_FILE}"
# ADR-${ISSUE_ID}: ${TITLE}

* **Status**: Proposed
* **Issue**: [#${ISSUE_ID}](${REPO_URL}/issues/${ISSUE_ID})
* **Date**: $(date +%Y-%m-%d)
* **Authors**: Maintainers / Contributors

## 1. Context & Problem Statement
Describe the problem or opportunity driving this architectural change.

## 2. Decision Drivers
- Requirement 1
- Requirement 2

## 3. Proposed Architecture & Design
Detailed technical design, API signatures, and data model changes.

## 4. Consequences & Tradeoffs
- Positive impact
- Tradeoffs or operational risks
EOF
  echo "Created ADR template at ${ADR_FILE}"
fi

git add "${ADR_FILE}"
git commit -m "docs(adr): initialize ADR-${ISSUE_ID} for ${TITLE}"

echo "======================================================================"
echo "SUCCESS: Branch '${BRANCH_NAME}' created and ADR initialized."
echo "Next Steps:"
echo "1. Complete the design in '${ADR_FILE}'."
echo "2. Implement code changes and write tests."
echo "3. Submit Pull Request targeting '${BASE_BRANCH}'."
echo "======================================================================"
