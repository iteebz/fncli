"""fncli — function signature as CLI spec.

    from fncli import cli, run, invoke, Result, UsageError, StateError

    @cli("myapp")
    def status(all: bool = False):
        \"\"\"show status\"\"\"
        ...

The function IS the interface. Signature → parser. Docstring → help.
"""

import dataclasses
import difflib
import importlib
import inspect
import io
import os
import sys
import traceback
import types
import typing
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

_EMPTY = inspect.Parameter.empty

# _REGISTRY: command key → Entry
# _DEFAULTS: namespace → default command key
# _BARE:     namespace → bare callback
_REGISTRY: dict[str, "Entry"] = {}
_DEFAULTS: dict[str, str] = {}
_BARE: dict[str, Callable[..., Any]] = {}

RESERVED: frozenset[str] = frozenset({"selftest", "completions", "__complete"})
_HELP_FLAGS: frozenset[str] = frozenset(("-h", "--help"))


class UsageError(Exception):
    pass


class StateError(Exception):
    """Command was understood, but the object is in the wrong state.

    Unlike UsageError, this does NOT append 'Run --help for usage.' —
    the caller used the CLI correctly; the issue is state, not syntax.
    """


class RegistrationError(Exception):
    pass


# --- Parameter model ---


@dataclasses.dataclass(frozen=True, slots=True)
class Param:
    name: str  # python parameter name
    clean: str  # display name (trailing _ stripped)
    type: type  # target type for conversion
    default: Any  # _EMPTY if required
    is_list: bool  # list[X] annotation
    is_bool: bool  # bool annotation
    bool_value: bool  # value to set when flag is present (True for --x, False for --no-x)
    flags: list[str]  # e.g. ["--verbose", "-v"] or [] for positionals
    positional: bool  # consumed by position (no -- prefix)
    help: str  # per-param help string

    @property
    def required(self) -> bool:
        return self.default is _EMPTY


def _unwrap_optional(ann: Any) -> Any:
    if ann is type(None):
        return str
    if isinstance(ann, types.UnionType):
        args = [a for a in ann.__args__ if a is not type(None)]
        return args[0] if args else str
    if typing.get_origin(ann) is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return args[0] if args else str
    return ann if callable(ann) else str


def _build_params(
    fn: Callable[..., Any],
    flag_overrides: dict[str, list[str]],
    help_strings: dict[str, str],
    required_names: set[str],
) -> list[Param]:
    params: list[Param] = []
    for pname, param in inspect.signature(fn).parameters.items():
        ann = param.annotation
        raw = _unwrap_optional(ann) if ann is not _EMPTY else str
        is_list = typing.get_origin(raw) is list
        inner = typing.get_args(raw)[0] if is_list and typing.get_args(raw) else str
        is_bool = raw is bool and not is_list

        explicit_flags = flag_overrides.get(pname)
        clean = pname.rstrip("_")
        no_default = param.default is _EMPTY
        positional = no_default or explicit_flags == []

        # Resolve flags and bool semantics
        if positional:
            flags: list[str] = []
            bool_value = True
        elif is_bool and param.default is True:
            flags = explicit_flags or [f"--no-{clean.replace('_', '-')}"]
            bool_value = False
        else:
            flags = explicit_flags or [f"--{clean.replace('_', '-')}"]
            bool_value = True

        # required= makes a defaulted param act required at parse time
        default = _EMPTY if (no_default or pname in required_names) else param.default

        params.append(
            Param(
                name=pname,
                clean=clean,
                type=inner if is_list else raw,
                default=default,
                is_list=is_list,
                is_bool=is_bool,
                bool_value=bool_value,
                flags=flags,
                positional=positional,
                help=help_strings.get(pname, ""),
            )
        )
    return params


# --- Parsing ---


def _parse(params: list[Param], argv: list[str]) -> dict[str, Any]:
    """Parse argv against param specs, return kwargs dict."""
    result: dict[str, Any] = {}
    positionals = [p for p in params if p.positional]
    all_flags = {f: p for p in params if not p.positional for f in p.flags}
    pos_idx = 0

    i = 0
    while i < len(argv):
        token = argv[i]

        if token.startswith("-"):
            # --key=value form
            if "=" in token:
                key, _, value = token.partition("=")
                param = all_flags.get(key)
                if param is None:
                    raise UsageError(f"unknown flag: {key}")
                if param.is_bool:
                    raise UsageError(f"{key} is a flag and does not take a value")
                if param.is_list:
                    result.setdefault(param.name, []).append(param.type(value))
                else:
                    result[param.name] = param.type(value)
                i += 1
                continue

            param = all_flags.get(token)
            if param is None:
                raise UsageError(f"unknown flag: {token}")

            if param.is_bool:
                result[param.name] = param.bool_value
                i += 1
                continue

            # Named value
            if param.is_list:
                i += 1
                values: list[Any] = []
                while i < len(argv) and not argv[i].startswith("-"):
                    values.append(param.type(argv[i]))
                    i += 1
                result.setdefault(param.name, []).extend(values)
                continue

            if i + 1 >= len(argv):
                raise UsageError(f"{token} requires a value")
            try:
                result[param.name] = param.type(argv[i + 1])
            except (ValueError, TypeError) as e:
                raise UsageError(f"{token}: {e}") from None
            i += 2
            continue

        # Positional argument
        if pos_idx >= len(positionals):
            raise UsageError(f"unexpected argument: {token}")

        p = positionals[pos_idx]
        if p.is_list:
            values = []
            while i < len(argv) and not argv[i].startswith("-"):
                try:
                    values.append(p.type(argv[i]))
                except (ValueError, TypeError) as e:
                    raise UsageError(f"{p.clean}: {e}") from None
                i += 1
            result[p.name] = result.get(p.name, []) + values
            pos_idx += 1
            continue

        try:
            result[p.name] = p.type(token)
        except (ValueError, TypeError) as e:
            raise UsageError(f"{p.clean}: {e}") from None
        pos_idx += 1
        i += 1

    # Fill defaults, check required
    for p in params:
        if p.name in result:
            continue
        if p.required:
            label = f"<{p.clean}>" if p.positional else p.flags[0]
            raise UsageError(f"{label} is required")
        result[p.name] = p.default

    return result


# --- Help formatting ---


def _format_help(key: str, description: str, params: list[Param]) -> str:
    """Generate help text for a single command."""
    lines: list[str] = []

    # Usage line
    usage_parts = [key]
    for p in params:
        if p.positional:
            if p.is_list:
                token = f"<{p.clean}> [<{p.clean}> ...]" if p.required else f"[<{p.clean}> ...]"
            elif p.required:
                token = f"<{p.clean}>"
            else:
                token = f"[{p.clean}]"
            usage_parts.append(token)
        elif p.is_bool:
            usage_parts.append(f"[{p.flags[0]}]")
        else:
            usage_parts.append(f"[{p.flags[0]} {p.clean.upper()}]")
    lines.append(f"usage: {' '.join(usage_parts)}")

    if description:
        lines.append(f"\n{description}")

    positional_params = [p for p in params if p.positional]
    option_params = [p for p in params if not p.positional]

    if positional_params:
        lines.append("\npositional arguments:")
        lines.extend(
            f"  {p.clean}  {p.help}" if p.help else f"  {p.clean}" for p in positional_params
        )

    lines.append("\noptions:")
    lines.append("  -h, --help  show this help message and exit")
    for p in option_params:
        if p.is_bool:
            flag_str = ", ".join(p.flags)
        else:
            metavar = p.clean.upper()
            flag_str = ", ".join(f"{f} {metavar}" for f in p.flags)
        suffix = f"  {p.help}" if p.help else ""
        if not suffix and not p.required and not p.is_bool:
            suffix = f"  (default: {p.default})"
        lines.append(f"  {flag_str}{suffix}")

    return "\n".join(lines) + "\n"


# --- Registry ---


@dataclasses.dataclass(slots=True)
class Entry:
    fn: Callable[..., Any]
    params: list[Param]
    description: str
    meta: dict[str, Any]


def _strict_discover() -> bool:
    value = os.environ.get("FNCLI_STRICT_DISCOVER", "")
    return value.lower() not in {"", "0", "false", "no", "off"}


def _assert_key_available(key: str, fn: Callable[..., Any]) -> None:
    existing = _REGISTRY.get(key)
    if existing and existing.fn is not fn:
        raise RegistrationError(f"command key {key!r} already registered")


def emit_error(msg: str) -> None:
    """Write error to both stderr and stdout (agents read stdout)."""
    sys.stderr.write(msg)
    sys.stdout.write(msg)


def _print_command_list(prefix: str, matches: list[tuple[str, str]]) -> None:
    lines = _collapse_commands(prefix, matches)
    col = max((len(cmd) for cmd, _ in lines), default=0)
    sys.stdout.write(f"usage: {prefix} <command> [args]\n\ncommands:\n")
    for cmd, desc in lines:
        sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
    sys.stdout.write(f"\nRun `{prefix} <command> --help` for details.\n")


def _collapse_commands(prefix: str, matches: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    lines: list[tuple[str, str]] = []
    for key, desc in matches:
        relative = key[len(prefix) :].lstrip()
        token = relative.split(" ", 1)[0]
        if token in seen:
            continue
        seen.add(token)
        subcommands = [k for k, _ in matches if k[len(prefix) :].lstrip().startswith(token + " ")]
        if len(subcommands) > 1 or (subcommands and relative == token):
            lines.append((token, f"{token} commands  (run `{prefix} {token} --help`)"))
        else:
            lines.append((token, desc))
    return lines


# --- Registration ---


def bare(namespace: str, fn: Callable[..., Any]) -> None:
    _BARE[namespace] = fn


def cli(
    parent: str | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    flags: dict[str, list[str]] | None = None,
    help: dict[str, str] | None = None,
    required: list[str] | None = None,
    aliases: list[str] | None = None,
    default: bool = False,
    readonly: bool = False,
    meta: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _name = name if name is not None else fn.__name__.replace("_", "-")
        key = f"{parent} {_name}".strip() if parent else _name
        _assert_key_available(key, fn)
        if _name in RESERVED:
            raise RegistrationError(f"command name {_name!r} is reserved")

        desc = description or fn.__doc__ or ""
        params = _build_params(fn, flags or {}, help or {}, set(required or []))

        merged = dict(meta or {})
        if readonly:
            merged["readonly"] = True

        entry = Entry(fn=fn, params=params, description=desc, meta=merged)
        _REGISTRY[key] = entry
        for a in aliases or []:
            if a in RESERVED:
                raise RegistrationError(f"alias name {a!r} is reserved")
            alias_key = f"{parent} {a}".strip() if parent else a
            _assert_key_available(alias_key, fn)
            _REGISTRY[alias_key] = entry
        if default and parent:
            _DEFAULTS[parent] = key

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args or kwargs:
                return fn(*args, **kwargs)
            argv = sys.argv[1:]
            if _HELP_FLAGS & set(argv):
                sys.stdout.write(_format_help(key, desc, params))
                sys.exit(0)
            try:
                parsed = _parse(params, argv)
                result = fn(**parsed)
                sys.exit(result if isinstance(result, int) else 0)
            except StateError as e:
                emit_error(f"{e}\n")
                sys.exit(1)
            except UsageError as e:
                emit_error(f"{e}\nRun `{key} --help` for usage.\n")
                sys.exit(1)

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return decorator


# --- Dispatch ---


def _dispatch_one(key: str, argv: list[str]) -> int:
    entry = _REGISTRY[key]

    if _HELP_FLAGS & set(argv):
        sys.stdout.write(_format_help(key, entry.description, entry.params))
        return 0

    try:
        parsed = _parse(entry.params, argv)
    except UsageError as e:
        emit_error(f"{key}: {e}\nRun `{key} --help` for usage.\n")
        return 1

    try:
        result = entry.fn(**parsed)
        return result if isinstance(result, int) else 0
    except StateError as e:
        emit_error(f"{e}\n")
        return 1
    except UsageError as e:
        emit_error(f"{e}\nRun `{key} --help` for usage.\n")
        return 1


def _subcommand_matches(prefix: str, token: str) -> bool:
    candidate = prefix + " " + token
    return any(k == candidate or k.startswith(candidate + " ") for k in _REGISTRY)


def _show_namespace(prefix: str, argv: list[str]) -> int | None:
    has_help = bool(_HELP_FLAGS & set(argv))
    matches = sorted(
        (key, entry.description)
        for key, entry in _REGISTRY.items()
        if key.startswith(prefix + " ") and key != prefix
    )
    if matches:
        _print_command_list(prefix, matches)
        return 0 if has_help else 1
    return None


def try_dispatch(argv: list[str]) -> int | None:
    # Built-in commands
    if len(argv) >= 2 and argv[1] == "selftest":
        return _selftest(argv[0], live="--live" in argv[2:], quiet="--quiet" in argv[2:])
    if len(argv) >= 2 and argv[1] == "__complete":
        return _complete(argv)
    if len(argv) >= 2 and argv[1] == "completions":
        return _completions(argv[0], argv[2] if len(argv) > 2 else "bash")

    # Longest-match dispatch
    for depth in range(len(argv), 0, -1):
        key = " ".join(argv[:depth])
        remaining = argv[depth:]

        if (
            remaining
            and not remaining[0].startswith("-")
            and _subcommand_matches(key, remaining[0])
        ):
            continue

        if key in _REGISTRY:
            return _dispatch_one(key, remaining)
        if key in _DEFAULTS:
            if remaining and bool(_HELP_FLAGS & set(remaining)):
                result = _show_namespace(key, argv)
                if result is not None:
                    return result
            return _dispatch_one(_DEFAULTS[key], remaining)

    # Bare callback
    has_help = bool(_HELP_FLAGS & set(argv))
    non_help = [a for a in argv if a not in _HELP_FLAGS]
    prefix = " ".join(non_help)

    if prefix in _BARE and not has_help:
        try:
            result = _BARE[prefix]()
            return result if isinstance(result, int) else 0
        except (StateError, UsageError) as e:
            emit_error(f"{e}\n")
            return 1

    if len(non_help) <= 1 and not has_help:
        return None

    # Namespace help or fuzzy match
    matches = sorted(
        (key, entry.description)
        for key, entry in _REGISTRY.items()
        if key.startswith(prefix + " ") or key == prefix
    )
    if matches:
        _print_command_list(prefix, matches)
        return 0 if has_help else 1

    if non_help:
        close = difflib.get_close_matches(prefix, list(_REGISTRY), n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        emit_error(f"Unknown command: {prefix}.{hint}\nRun `{argv[0]} --help` for usage.\n")
        return 1

    return None


def dispatch(argv: list[str]) -> int:
    result = try_dispatch(argv)
    if result is not None:
        return result
    prog = argv[0] if argv else "app"
    all_keys = sorted((k, e.description) for k, e in _REGISTRY.items())
    _print_command_list(prog, [(f"{prog} {k}", desc) for k, desc in all_keys])
    return 1


def run(argv: list[str] | None = None) -> None:
    try:
        code = dispatch(argv if argv is not None else sys.argv)
    except (StateError, UsageError) as e:
        emit_error(f"{e}\n")
        sys.exit(1)
    sys.exit(code)


# --- Testing ---


class Result:
    """Captured output from invoke()."""

    __slots__ = ("exit_code", "stderr", "stdout")

    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self) -> str:
        return f"Result(exit_code={self.exit_code})"


def invoke(argv: list[str]) -> Result:
    """Run a dispatch cycle, capturing stdout/stderr and trapping SystemExit."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = dispatch(argv)
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 1
    return Result(code, out.getvalue(), err.getvalue())


# --- Aliases ---


def alias(src: str, dst: str) -> None:
    """Point `dst` at the same handler as `src`."""
    if src not in _REGISTRY:
        raise KeyError(f"alias source {src!r} not registered")
    src_entry = _REGISTRY[src]
    existing = _REGISTRY.get(dst)
    if existing and existing is not src_entry:
        raise RegistrationError(f"alias destination {dst!r} already registered")
    _REGISTRY[dst] = _REGISTRY[src]


def alias_namespace(src: str, dst: str) -> None:
    """Register all commands under `src` namespace also under `dst`."""
    prefix = src + " "
    updates: dict[str, Entry] = {}
    for key, entry in list(_REGISTRY.items()):
        if not key.startswith(prefix):
            continue
        new_key = dst + key[len(src) :]
        existing = _REGISTRY.get(new_key)
        if existing and existing is not entry:
            raise RegistrationError(f"alias namespace destination {new_key!r} already registered")
        updates[new_key] = entry
    _REGISTRY.update(updates)


# --- Introspection ---


def commands() -> list[str]:
    return sorted(_REGISTRY)


def readonly(key: str) -> bool:
    entry = _REGISTRY.get(key)
    return entry.meta.get("readonly", False) if entry else False


def meta(key: str) -> dict[str, Any]:
    entry = _REGISTRY.get(key)
    return entry.meta if entry else {}


def where(**kwargs: Any) -> list[str]:
    return sorted(
        k
        for k, entry in _REGISTRY.items()
        if all(entry.meta.get(field) == value for field, value in kwargs.items())
    )


def entries() -> list[tuple[str, Callable[..., Any], list[Param]]]:
    return [(key, e.fn, e.params) for key, e in sorted(_REGISTRY.items())]


def manifest() -> dict[str, Any]:
    """Structured description of all registered commands, for agent consumption."""
    result: dict[str, Any] = {}
    for key, entry in sorted(_REGISTRY.items()):
        params: list[dict[str, Any]] = []
        for p in entry.params:
            kind = "flag" if p.is_bool else ("positional" if p.positional else "option")
            param_entry: dict[str, Any] = {
                "name": p.name,
                "type": kind,
                "required": p.required,
                "default": None if p.required else p.default,
                "help": p.help,
            }
            if p.flags:
                param_entry["flags"] = p.flags
            params.append(param_entry)
        result[key] = {
            "description": entry.description,
            "params": params,
            "meta": entry.meta,
        }
    return result


# --- Built-in commands ---


def _selftest(prog: str, live: bool = False, quiet: bool = False) -> int:
    prefix = prog + " "
    results: list[dict[str, str]] = []

    for key, fn, params in entries():
        if key != prog and not key.startswith(prefix):
            continue

        result_entry: dict[str, str] = {"command": key, "help": "skip", "live": "skip"}

        try:
            _format_help(key, _REGISTRY[key].description, params)
            result_entry["help"] = "pass"
        except Exception:
            result_entry["help"] = "FAIL"

        if live and readonly(key):
            sig = inspect.signature(fn)
            if not any(p.default is _EMPTY for p in sig.parameters.values()):
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        ret = fn()
                    result_entry["live"] = (
                        "pass" if (ret is None or ret == 0) else f"FAIL(rc={ret})"
                    )
                except SystemExit as e:
                    result_entry["live"] = "pass" if e.code == 0 else f"FAIL(exit={e.code})"
                except Exception as e:
                    result_entry["live"] = f"FAIL({type(e).__name__})"
                    traceback.print_exc(file=sys.stderr)

        results.append(result_entry)

    if not results:
        sys.stderr.write(f"{prog} selftest: no commands registered\n")
        return 1

    failed = sum(1 for r in results if "FAIL" in r["help"] or "FAIL" in r.get("live", ""))
    total = len(results)
    col = max(len(r["command"]) for r in results)

    if not quiet:
        for r in results:
            h = "✓" if r["help"] == "pass" else ("·" if r["help"] == "skip" else "✗")
            lv = "✓" if r["live"] == "pass" else ("·" if r["live"] == "skip" else "✗")
            line = f"  {r['command']:<{col}}  help={h}  live={lv}"
            if "FAIL" in r.get("live", ""):
                line += f"  ({r['live']})"
            sys.stdout.write(line + "\n")
        sys.stdout.write("\n")

    if failed:
        for r in results:
            if "FAIL" in r["help"] or "FAIL" in r.get("live", ""):
                h = "✗" if "FAIL" in r["help"] else "✓"
                lv = r.get("live", "skip")
                sys.stdout.write(f"  FAIL  {r['command']:<{col}}  help={h}  live={lv}\n")

    sys.stdout.write(
        f"  {prog}: {total - failed}/{total} passed"
        + (f"  ({failed} failed)" if failed else "")
        + "\n"
    )
    return 1 if failed else 0


def _complete(argv: list[str]) -> int:
    """Handle `prog __complete word1 word2 ...` — outputs one candidate per line."""
    words = argv[2:]
    if not words:
        return 0
    prog = words[0]
    typed = words[1:]
    cur = typed[-1] if typed else ""
    preceding = typed[:-1]

    cmd_parts = [prog] + [p for p in preceding if not p.startswith("-")]

    cmd_key: str | None = None
    for depth in range(len(cmd_parts), 0, -1):
        candidate = " ".join(cmd_parts[:depth])
        if candidate in _REGISTRY or any(k.startswith(candidate + " ") for k in _REGISTRY):
            cmd_key = candidate
            break

    seen: set[str] = set()

    def emit(s: str) -> None:
        if s not in seen and s.startswith(cur):
            sys.stdout.write(s + "\n")
            seen.add(s)

    search_prefix = (cmd_key + " ") if cmd_key else (prog + " ")
    for key in _REGISTRY:
        if key.startswith(search_prefix):
            token = key[len(search_prefix) :].split(" ")[0]
            if token:
                emit(token)

    if cmd_key and cmd_key in _REGISTRY:
        for p in _REGISTRY[cmd_key].params:
            for flag in p.flags:
                emit(flag)

    return 0


def _completions(prog: str, shell: str) -> int:
    """Output a shell completion bootstrap script for `prog`."""
    scripts: dict[str, str] = {
        "bash": (
            f"_{prog}_complete() {{\n"
            f"    local IFS=$'\\n'\n"
            f"    COMPREPLY=($('{prog}' __complete \"${{COMP_WORDS[@]}}\" 2>/dev/null))\n"
            f"}}\n"
            f"complete -F _{prog}_complete '{prog}'\n"
        ),
        "zsh": (
            f"_{prog}() {{\n"
            f"    local -a completions\n"
            f'    completions=(${{(f)"$(\'{prog}\' __complete "${{words[@]}}" 2>/dev/null)"}})\n'
            f"    compadd -a completions\n"
            f"}}\n"
            f"compdef _{prog} '{prog}'\n"
        ),
        "fish": (
            f"function __{prog}_complete\n"
            f"    set -l tokens (commandline -opc)\n"
            f"    set -a tokens (commandline -ct)\n"
            f"    '{prog}' __complete $tokens 2>/dev/null\n"
            f"end\n"
            f"complete -c '{prog}' -f -a '(__{prog}_complete)'\n"
        ),
    }
    script = scripts.get(shell)
    if script is None:
        sys.stderr.write(f"completions: unknown shell {shell!r}. Use bash, zsh, or fish.\n")
        return 1
    sys.stdout.write(script)
    return 0


# --- Discovery ---


def autodiscover(package_root: Path, package_name: str) -> None:
    depth = len(package_name.split("."))
    import_root = package_root.resolve()
    for _ in range(depth):
        import_root = import_root.parent
    for path in sorted(package_root.rglob("*.py")):
        try:
            if "@cli(" not in path.read_text():
                continue
        except OSError:
            if _strict_discover():
                raise
            continue
        rel = path.resolve().relative_to(import_root)
        mod = ".".join(rel.with_suffix("").parts)
        if not mod.startswith(package_name + "."):
            continue
        importlib.import_module(mod)
