"""
CLI argument injection prevention utilities.

Provides a single, reusable validation function for any value that will be
interpolated into a subprocess command array.  All services that build CLI
commands (helm, git, kubectl, aws, docker) should use ``validate_cli_arg``
instead of rolling their own checks.

The core rule is simple: a positional argument must never look like a flag.
Values starting with ``-`` could be interpreted as options by the target
binary, allowing an attacker to inject arbitrary flags
(e.g. ``--set=image=evil:latest`` via a timeout field).

Usage::

    from utils.security import validate_cli_arg

    validate_cli_arg("release_name", user_input)
    validate_cli_arg("timeout", user_input)

For arguments that should also match a specific format (e.g. Helm timeout
must be ``<digits><unit>``), callers should layer additional validation on
top of this baseline check.
"""

import re

# Helm timeout format: digits + time unit (e.g. "5m", "300s", "1h30m")
# Matches: 5m, 10m0s, 1h, 300s, 2h30m, etc.
_HELM_TIMEOUT_RE = re.compile(r"^\d+[smh](\d+[smh])*$")


def validate_cli_arg(name: str, value: str | None) -> None:
    """
    Validate that *value* is safe to pass as a CLI positional argument.

    Rejects any value that starts with ``-`` (could be interpreted as a flag
    by the target binary).

    Args:
        name:  Human-readable argument name (for the error message).
        value: The value to validate.  ``None`` / empty string are allowed
               (the caller decides whether the arg is required).

    Raises:
        ValueError: If *value* starts with ``-``.
    """
    if value and value.startswith("-"):
        raise ValueError(f"Invalid {name}: cannot start with '-'")


# Heuristic for input metadata that should be treated as sensitive even when
# the upstream manifest forgets the explicit `sensitive: true` flag.
# Matches the suffix of common credential field names; deliberately conservative
# so a benign field like "tokens_per_minute" doesn't get masked.
_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|_)(api[_-]?key|access[_-]?key|secret[_-]?key|secret|"
    r"token|password|passphrase|private[_-]?key)$",
    re.IGNORECASE,
)


def is_sensitive_input(definition: dict) -> bool:
    """Return True if an input definition should be treated as sensitive.

    Honors the explicit ``sensitive: true`` flag, then falls back to a
    name/source-field suffix match for well-known credential fields. This
    closes the gap when manifests omit the flag for credentials we know are
    secret (e.g. ``ibmcloud_api_key`` sourced from a credential template).
    """
    if bool(definition.get("sensitive")):
        return True
    for field in ("name", "source_field"):
        candidate = definition.get(field)
        if isinstance(candidate, str) and _SENSITIVE_NAME_RE.search(candidate):
            return True
    return False


def validate_action_inputs(
    declared_inputs: list | None, provided_inputs: dict | None
) -> dict:
    """Validate an action invocation's inputs against its declared inputs (D-034).

    Runtime filter for manifest-declared action inputs — the values flow into a
    vendor-CLI argv holding cloud credentials, so this is the server-side gate,
    not just the author-side shape check in ``module_metadata``:

    - **Rejects undeclared keys** — any provided key without a matching declared
      input ``name`` (closes the ``{**ctx.variables, **action_inputs}`` merge
      poisoning vector).
    - **Enforces ``choices``** — a declared enum value must be one of its
      ``choices``.
    - **Applies ``validate_cli_arg``** to every free-string value (not
      constrained by ``choices``), rejecting leading-dash flag injection.
    - **Applies declared defaults** for any declared input the caller omitted,
      so the task always receives a complete, validated set.

    Args:
        declared_inputs: The action's declared ``inputs`` list from the manifest.
        provided_inputs: The caller's invocation ``inputs`` dict.

    Returns:
        The effective, validated inputs dict (provided values + applied defaults).

    Raises:
        ValueError: On any undeclared key, out-of-range enum, or unsafe value.
    """
    provided = provided_inputs or {}
    declared_by_name: dict[str, dict] = {}
    for decl in declared_inputs or []:
        if isinstance(decl, dict) and isinstance(decl.get("name"), str):
            declared_by_name[decl["name"]] = decl

    for key in provided:
        if key not in declared_by_name:
            raise ValueError(f"Undeclared action input '{key}'")

    effective: dict = {}
    for name, decl in declared_by_name.items():
        if name in provided:
            value = provided[name]
        elif "default" in decl:
            value = decl["default"]
        else:
            continue

        choices = decl.get("choices")
        if isinstance(choices, list) and choices:
            if value not in choices:
                allowed = ", ".join(str(c) for c in choices)
                raise ValueError(
                    f"Invalid value for action input '{name}': {value!r} (allowed: {allowed})"
                )
            effective[name] = value
            continue

        declared_type = str(decl.get("type") or "string").lower()
        if declared_type in ("bool", "boolean"):
            effective[name] = _coerce_action_bool(name, value)
            continue
        if declared_type in ("int", "integer", "number", "float"):
            coerced = _coerce_action_number(name, value, declared_type)
            validate_cli_arg(name, str(coerced))
            effective[name] = coerced
            continue

        # Free string (or unknown type): ends up as an argv token verbatim.
        #
        # Reject non-scalars rather than str()-ing them. A dict or list passed
        # for a `type: string` input previously slipped through — validate_cli_arg
        # saw the Python repr (a single token with no leading dash, so not
        # flag-injection) and the RAW value was then stored and templated. Not
        # exploitable as argv, but it silently accepts a shape the manifest did
        # not declare, and what reaches the step is a Python repr rather than
        # anything the artifact can parse (issue #96, N2).
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError(
                f"Invalid value for action input '{name}': expected a "
                f"{declared_type}, got {type(value).__name__}"
            )
        validate_cli_arg(name, None if value is None else str(value))
        effective[name] = value

    return effective


def _coerce_action_bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return value.strip().lower() in ("true", "1", "yes")
    raise ValueError(f"Invalid boolean for action input '{name}': {value!r}")


def _coerce_action_number(name: str, value: object, declared_type: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid number for action input '{name}': {value!r}")
    if isinstance(value, int | float):
        number: int | float = value
    elif isinstance(value, str):
        try:
            number = float(value) if ("." in value or "e" in value.lower()) else int(value)
        except ValueError:
            raise ValueError(f"Invalid number for action input '{name}': {value!r}") from None
    else:
        raise ValueError(f"Invalid number for action input '{name}': {value!r}")
    if declared_type in ("int", "integer") and not isinstance(number, int):
        raise ValueError(f"Invalid integer for action input '{name}': {value!r}")
    return number


def validate_helm_timeout(value: str | None) -> None:
    """
    Validate a Helm ``--timeout`` value.

    In addition to the baseline CLI-arg check, this ensures the value matches
    the ``<digits><unit>`` format Helm expects (e.g. ``5m``, ``300s``,
    ``1h30m``).  Without this, an attacker could pass a string like
    ``"--set=image=evil"`` as a timeout.

    Args:
        value: Timeout string to validate.  ``None`` is allowed (caller
               decides whether timeout is required).

    Raises:
        ValueError: If *value* fails CLI-arg check or format check.
    """
    if value is None:
        return
    validate_cli_arg("timeout", value)
    if not _HELM_TIMEOUT_RE.match(value):
        raise ValueError(
            f"Invalid timeout format '{value}': expected <digits><unit> "
            f"(e.g. '5m', '300s', '1h30m')"
        )
