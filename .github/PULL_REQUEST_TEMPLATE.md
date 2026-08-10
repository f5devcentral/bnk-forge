## Description

A clear and concise description of the changes in this Pull Request.

Fixes / Implements: #[Issue Number]

---

## Architectural Decision Record (ADR)
- [ ] This PR includes or updates an ADR in `docs/adr/ADR-<issue>-<title>.md` (Required for non-trivial feature/architecture changes).
- [ ] N/A (Bug fix or minor docs tweak).

---

## Type of Change
- [ ] Bug fix (non-breaking change fixing an issue)
- [ ] New feature (non-breaking change adding functionality)
- [ ] Breaking change (fix or feature causing existing functionality to break)
- [ ] Documentation update

---

## Verification & Testing
Describe the tests you ran to verify your changes:
1. `make quick-check` passes cleanly
2. `make pre-push` passes cleanly
3. Added unit/component tests for new logic

---

## Checklist
- [ ] My code follows the project's code style and formatting guidelines.
- [ ] I have updated documentation where necessary.
- [ ] I have generated updated openapi types if modifying backend routes/schemas (`make openapi-types`).
