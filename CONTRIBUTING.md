# Contributing to BNK Forge

Thank you for your interest in contributing to BNK Forge! We welcome contributions from the community to help improve the project.

---

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you are expected to uphold it.

---

## How to Contribute

### 1. Reporting Bugs
Before opening a new issue, please search existing issues to see if it has already been reported.
When creating a bug report, please include:
- A clear, descriptive title.
- Steps to reproduce the issue.
- Expected vs. actual behavior.
- Relevant logs, environment details, and configuration (redacting any credentials or private data).

### 2. Suggesting Enhancements
Feature requests are welcome! Please provide:
- A clear description of the feature and its intended use case.
- Any architectural considerations or tradeoffs.

### 3. Submitting Pull Requests & ADR Workflow

We follow a lightweight **Issue ➔ ADR ➔ Branch ➔ PR** design workflow for feature development:

1. **Open an Issue**: Identify the problem or feature requirement on GitHub.
2. **Initialize ADR & Work Branch**: Run the provided helper script to create a design doc and branch:
   ```bash
   ./scripts/new-adr.sh --issue 123 --title "feature description"
   ```
   This generates `docs/adr/ADR-123-feature-description.md` and checks out branch `feat/adr-123-feature-description` off `staging`.
3. **Draft the Design & Code**: Fill in the ADR document and implement code changes.
4. **Local Validation**: Run tests and linting:
   ```bash
   make quick-check
   make pre-push
   ```
5. **Submit PR**: Open a Pull Request targeting `staging` referencing the issue and linking the ADR.

---

## Development & Code Style

Full build, test, architecture, and style reference: **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**. Summary:

- **Backend**: Python 3.11+ using FastAPI and SQLAlchemy. Code MUST comply with `ruff` formatting (`line-length = 120`) and type annotations (`mypy`).
- **Frontend**: React 18 + TypeScript + Vite + TailwindCSS. Code MUST pass `eslint` and `tsc --noEmit`.
- **API Contracts**: If you modify any route or Pydantic model, regenerate TypeScript types using:
  ```bash
  make openapi-types
  ```

---

## Licensing

By contributing to this project, you agree that your contributions will be licensed under the project's [Apache 2.0 License](LICENSE).
