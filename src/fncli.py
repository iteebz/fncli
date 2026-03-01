"""fncli — function signature as CLI spec.

    from fncli import cli, run, UsageError

    @cli("myapp")
    def status(all: bool = False):
        \"\"\"show status\"\"\"
        ...

The function IS the interface. Signature → argparse. Docstring → help.
"""

import argparse
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

# _REGISTRY: command key → {fn, parser, meta}
# _DEFAULTS: namespace → default command key
# _BARE:     namespace → bare callback
_REGISTRY: dict[str, dict[str, Any]] = {}
_DEFAULTS: dict[str, str] = {}
_BARE: dict[str, Callable[..., Any]] = {}

RESERVED: frozenset[str] = frozenset(
    {"selftest", "completions", "__complete"}
)  # names downstream CLIs should not register
_HELP_FLAGS: frozenset[str] = frozenset(("-h", "--help"))


class UsageError(Exception):
    pass


class RegistrationError(Exception):
    pass


def _strict_discover() -> bool:
    value = os.environ.get("FNCLI_STRICT_DISCOVER", "")
    return value.lower() not in {"", "0", "false", "no", "off"}


def _assert_key_available(key: str, fn: Callable[..., Any]) -> None:
    existing = _REGISTRY.get(key)
    if existing and existing["fn"] is not fn:
        raise RegistrationError(f"command key {key!r} already registered")


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


def _required_positionals(fn: Callable[..., Any]) -> list[str]:
    """Return names of required list-type positional params — used for early error messaging."""
    return [
        pname
        for pname, param in inspect.signature(fn).parameters.items()
        if typing.get_origin(
            _unwrap_optional(
                param.annotation if param.annotation is not inspect.Parameter.empty else str
            )
        )
        is list
        and param.default is inspect.Parameter.empty
    ]


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
        _flags = flags or {}
        _help = help or {}
        _required = set(required or [])

        parser = argparse.ArgumentParser(prog=key, description=desc, add_help=True)
        sig = inspect.signature(fn)

        for pname, param in sig.parameters.items():
            ann = param.annotation
            raw = _unwrap_optional(ann) if ann is not inspect.Parameter.empty else str
            is_list = typing.get_origin(raw) is list
            inner = typing.get_args(raw)[0] if is_list and typing.get_args(raw) else str
            explicit_flags = _flags.get(pname)
            clean = pname.rstrip("_")
            flag_names = explicit_flags or [f"--{clean.replace('_', '-')}"]
            no_default = param.default is inspect.Parameter.empty
            positional_optional = explicit_flags == [] and not no_default
            metavar = clean.upper() if clean != pname else None

            param_help = _help.get(pname)

            if is_list:
                if no_default:
                    parser.add_argument(pname, type=inner, nargs="+", help=param_help)
                elif positional_optional:
                    parser.add_argument(
                        pname, type=inner, nargs="*", default=param.default, help=param_help
                    )
                elif metavar:
                    parser.add_argument(
                        *flag_names,
                        dest=pname,
                        type=inner,
                        nargs="*",
                        default=param.default,
                        metavar=metavar,
                        help=param_help,
                    )
                else:
                    parser.add_argument(
                        *flag_names,
                        dest=pname,
                        type=inner,
                        nargs="*",
                        default=param.default,
                        help=param_help,
                    )
            elif raw is bool:
                parser.add_argument(
                    *flag_names, dest=pname, action="store_true", default=False, help=param_help
                )
            elif positional_optional:
                parser.add_argument(
                    pname, type=raw, nargs="?", default=param.default, help=param_help
                )
            elif no_default:
                parser.add_argument(pname, type=raw, help=param_help)
            elif metavar:
                parser.add_argument(
                    *flag_names,
                    dest=pname,
                    type=raw,
                    default=param.default,
                    required=pname in _required,
                    metavar=metavar,
                    help=param_help,
                )
            else:
                parser.add_argument(
                    *flag_names,
                    dest=pname,
                    type=raw,
                    default=param.default,
                    required=pname in _required,
                    help=param_help,
                )

        merged = dict(meta or {})
        if readonly:
            merged["readonly"] = True

        entry: dict[str, Any] = {
            "fn": fn,
            "parser": parser,
            "meta": merged,
            "required_positionals": _required_positionals(fn),
        }
        _REGISTRY[key] = entry
        for alias in aliases or []:
            if alias in RESERVED:
                raise RegistrationError(f"alias name {alias!r} is reserved")
            alias_key = f"{parent} {alias}".strip() if parent else alias
            _assert_key_available(alias_key, fn)
            _REGISTRY[alias_key] = entry
        if default and parent:
            _DEFAULTS[parent] = key

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args or kwargs:
                return fn(*args, **kwargs)
            stderr_buf = io.StringIO()
            try:
                with redirect_stderr(stderr_buf):
                    parsed = parser.parse_args(sys.argv[1:])
            except SystemExit as e:
                stderr_out = stderr_buf.getvalue()
                if stderr_out:
                    sys.stderr.write(stderr_out)
                sys.exit(e.code)
            try:
                result = fn(**vars(parsed))
                sys.exit(result if isinstance(result, int) else 0)
            except UsageError as e:
                sys.stderr.write(f"{e}\nRun `{key} --help` for usage.\n")
                sys.exit(1)

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _dispatch_one(key: str, argv: list[str]) -> int:
    entry = _REGISTRY[key]
    fn, parser = entry["fn"], entry["parser"]
    req = entry["required_positionals"]
    if (
        req
        and not any(a for a in argv if not a.startswith("-"))
        and "--help" not in argv
        and "-h" not in argv
    ):
        names = ", ".join(f"<{n}>" for n in req)
        sys.stderr.write(f"{key}: {names} required. Run `{key} --help` for usage.\n")
        return 1
    stderr_buf = io.StringIO()
    try:
        with redirect_stderr(stderr_buf):
            args = parser.parse_args(argv)
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 1
        stderr_out = stderr_buf.getvalue()
        if stderr_out:
            sys.stderr.write(stderr_out)
        elif code != 0:
            sys.stderr.write(f"{key}: invalid arguments. Run `{key} --help`.\n")
        return code
    try:
        result = fn(**vars(args))
        return result if isinstance(result, int) else 0
    except UsageError as e:
        sys.stderr.write(f"{e}\nRun `{key} --help` for usage.\n")
        return 1


def _selftest(prog: str, live: bool = False, quiet: bool = False) -> int:
    prefix = prog + " "
    results: list[dict[str, str]] = []

    for key, fn, parser in entries():
        if key != prog and not key.startswith(prefix):
            continue

        result: dict[str, str] = {"command": key, "help": "skip", "live": "skip"}

        try:
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                parser.parse_args(["--help"])
        except SystemExit as e:
            result["help"] = "pass" if e.code == 0 else "FAIL"
        except Exception:
            result["help"] = "FAIL"

        if live and is_readonly(key):
            sig = inspect.signature(fn)
            if not any(p.default is inspect.Parameter.empty for p in sig.parameters.values()):
                try:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        ret = fn()
                    result["live"] = "pass" if (ret is None or ret == 0) else f"FAIL(rc={ret})"
                except SystemExit as e:
                    result["live"] = "pass" if e.code == 0 else f"FAIL(exit={e.code})"
                except Exception as e:
                    result["live"] = f"FAIL({type(e).__name__})"
                    traceback.print_exc(file=sys.stderr)

        results.append(result)

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
    # argv = ["prog", "__complete", "prog", "word1", ..., "cur"]
    words = argv[2:]  # COMP_WORDS passed by shell
    if not words:
        return 0
    prog = words[0]
    typed = words[1:]  # tokens after program name
    cur = typed[-1] if typed else ""  # word currently being typed
    preceding = typed[:-1]  # already-complete tokens

    # Build command key from non-flag preceding tokens
    cmd_parts = [prog] + [p for p in preceding if not p.startswith("-")]

    # Find deepest matching command key
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

    # Subcommands at current depth
    search_prefix = (cmd_key + " ") if cmd_key else (prog + " ")
    for key in _REGISTRY:
        if key.startswith(search_prefix):
            rest = key[len(search_prefix) :]
            token = rest.split(" ")[0]
            if token:
                emit(token)

    # Flags for the matched command (always offer, user may start with -)
    if cmd_key and cmd_key in _REGISTRY:
        parser = _REGISTRY[cmd_key]["parser"]
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):  # type: ignore[reportPrivateUsage]
                continue
            for flag in action.option_strings:
                emit(flag)

    return 0


def _completions(prog: str, shell: str) -> int:
    """Output a shell completion bootstrap script for `prog`."""
    if shell == "bash":
        sys.stdout.write(
            f"_{prog}_complete() {{\n"
            f"    local IFS=$'\\n'\n"
            f"    COMPREPLY=($('{prog}' __complete \"${{COMP_WORDS[@]}}\" 2>/dev/null))\n"
            f"}}\n"
            f"complete -F _{prog}_complete '{prog}'\n"
        )
    elif shell == "zsh":
        sys.stdout.write(
            f"_{prog}() {{\n"
            f"    local -a completions\n"
            f'    completions=(${{(f)"$(\'{prog}\' __complete "${{words[@]}}" 2>/dev/null)"}})\n'
            f"    compadd -a completions\n"
            f"}}\n"
            f"compdef _{prog} '{prog}'\n"
        )
    elif shell == "fish":
        sys.stdout.write(
            f"function __{prog}_complete\n"
            f"    set -l tokens (commandline -opc)\n"
            f"    set -a tokens (commandline -ct)\n"
            f"    '{prog}' __complete $tokens 2>/dev/null\n"
            f"end\n"
            f"complete -c '{prog}' -f -a '(__{prog}_complete)'\n"
        )
    else:
        sys.stderr.write(f"completions: unknown shell {shell!r}. Use bash, zsh, or fish.\n")
        return 1
    return 0


def _subcommand_matches(prefix: str, token: str) -> bool:
    candidate = prefix + " " + token
    return any(k == candidate or k.startswith(candidate + " ") for k in _REGISTRY)


def _show_namespace(prefix: str, argv: list[str]) -> int | None:
    has_help = bool(_HELP_FLAGS & set(argv))
    matches = sorted(
        (key, entry["parser"].description or "")
        for key, entry in _REGISTRY.items()
        if key.startswith(prefix + " ") and key != prefix
    )
    if matches:
        lines = _collapse_commands(prefix, matches)
        col = max((len(cmd) for cmd, _ in lines), default=0)
        sys.stdout.write(f"usage: {prefix} <command> [args]\n\ncommands:\n")
        for cmd, desc in lines:
            sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
        sys.stdout.write(f"\nRun `{prefix} <command> --help` for details.\n")
        return 0 if has_help else 1
    return None


def try_dispatch(argv: list[str]) -> int | None:
    if len(argv) >= 2 and argv[1] == "selftest":
        return _selftest(argv[0], live="--live" in argv[2:], quiet="--quiet" in argv[2:])
    if len(argv) >= 2 and argv[1] == "__complete":
        return _complete(argv)
    if len(argv) >= 2 and argv[1] == "completions":
        shell = argv[2] if len(argv) > 2 else "bash"
        return _completions(argv[0], shell)
    for depth in range(len(argv), 0, -1):
        key = " ".join(argv[:depth])
        if key in _REGISTRY:
            remaining = argv[depth:]
            if (
                remaining
                and not remaining[0].startswith("-")
                and _subcommand_matches(key, remaining[0])
            ):
                continue
            return _dispatch_one(key, remaining)
        if key in _DEFAULTS:
            remaining = argv[depth:]
            if (
                remaining
                and not remaining[0].startswith("-")
                and _subcommand_matches(key, remaining[0])
            ):
                continue
            if remaining and bool(_HELP_FLAGS & set(remaining)):
                result = _show_namespace(key, argv)
                if result is not None:
                    return result
            return _dispatch_one(_DEFAULTS[key], remaining)

    has_help = bool(_HELP_FLAGS & set(argv))
    non_help = [a for a in argv if a not in _HELP_FLAGS]
    prefix = " ".join(non_help)

    if prefix in _BARE and not has_help:
        try:
            result = _BARE[prefix]()
            return result if isinstance(result, int) else 0
        except UsageError as e:
            sys.stderr.write(f"{e}\n")
            return 1

    if len(non_help) <= 1 and not has_help:
        return None

    matches = sorted(
        (key, entry["parser"].description or "")
        for key, entry in _REGISTRY.items()
        if key.startswith(prefix + " ") or key == prefix
    )
    if matches:
        lines = _collapse_commands(prefix, matches)
        col = max((len(cmd) for cmd, _ in lines), default=0)
        sys.stdout.write(f"usage: {prefix} <command> [args]\n\ncommands:\n")
        for cmd, desc in lines:
            sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
        sys.stdout.write(f"\nRun `{prefix} <command> --help` for details.\n")
        return 0 if has_help else 1

    if non_help:
        known = list(_REGISTRY)
        close = difflib.get_close_matches(prefix, known, n=3, cutoff=0.5)
        if close:
            hint = ", ".join(close)
            sys.stderr.write(
                f"Unknown command: {prefix}. Did you mean: {hint}?\nRun `{argv[0]} --help` for usage.\n"
            )
        else:
            sys.stderr.write(f"Unknown command: {prefix}\nRun `{argv[0]} --help` for usage.\n")
        return 1

    return None


def _collapse_commands(prefix: str, matches: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    lines: list[tuple[str, str]] = []
    for key, desc in matches:
        relative = key[len(prefix) :].lstrip()
        parts = relative.split(" ", 1)
        token = parts[0]
        if token in seen:
            continue
        seen.add(token)
        if len(parts) == 1:
            subcommands = [
                k for k, _ in matches if k[len(prefix) :].lstrip().startswith(token + " ")
            ]
            if subcommands:
                group_desc = f"{token} commands  (run `{prefix} {token} --help`)"
                lines.append((token, group_desc))
            else:
                lines.append((token, desc))
        else:
            subcommands = [
                k for k, _ in matches if k[len(prefix) :].lstrip().startswith(token + " ")
            ]
            group_desc = (
                f"{token} commands  (run `{prefix} {token} --help`)"
                if len(subcommands) > 1
                else desc
            )
            lines.append((token, group_desc))
    return lines


def dispatch(argv: list[str]) -> int:
    result = try_dispatch(argv)
    if result is not None:
        return result
    prog = argv[0] if argv else "app"
    all_keys = sorted((k, e["parser"].description or "") for k, e in _REGISTRY.items())
    lines = _collapse_commands(prog, [(f"{prog} {k}", desc) for k, desc in all_keys])
    col = max((len(cmd) for cmd, _ in lines), default=0)
    sys.stdout.write(f"usage: {prog} <command> [args]\n\ncommands:\n")
    for cmd, desc in lines:
        sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
    sys.stdout.write(f"\nRun `{prog} <command> --help` for details.\n")
    return 1


def run(argv: list[str] | None = None) -> None:
    try:
        code = dispatch(argv if argv is not None else sys.argv)
    except UsageError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)
    sys.exit(code)


def alias(src: str, dst: str) -> None:
    """Point `dst` at the same handler as `src`.

    Example: alias("space swarm tail", "space tail")
    """
    if src not in _REGISTRY:
        raise KeyError(f"alias source {src!r} not registered")
    src_entry = _REGISTRY[src]
    existing = _REGISTRY.get(dst)
    if existing and existing is not src_entry:
        raise RegistrationError(f"alias destination {dst!r} already registered")
    _REGISTRY[dst] = _REGISTRY[src]


def alias_namespace(src: str, dst: str) -> None:
    """Register all commands under `src` namespace also under `dst`.

    Example: alias_namespace("life log", "life add") makes `life add a`
    dispatch the same function as `life log a`.
    """
    prefix = src + " "
    updates: dict[str, dict[str, Any]] = {}
    for key, entry in list(_REGISTRY.items()):
        if not key.startswith(prefix):
            continue
        new_key = dst + key[len(src) :]
        existing = _REGISTRY.get(new_key)
        if existing and existing is not entry:
            raise RegistrationError(f"alias namespace destination {new_key!r} already registered")
        updates[new_key] = entry
    _REGISTRY.update(updates)


def commands() -> list[str]:
    return sorted(_REGISTRY)


def is_readonly(key: str) -> bool:
    return _REGISTRY.get(key, {}).get("meta", {}).get("readonly", False)


def readonly_commands() -> list[str]:
    return where(readonly=True)


def meta(key: str) -> dict[str, Any]:
    return _REGISTRY.get(key, {}).get("meta", {})


def where(**kwargs: Any) -> list[str]:
    return sorted(
        k
        for k in _REGISTRY
        if all(_REGISTRY[k]["meta"].get(field) == value for field, value in kwargs.items())
    )


def entries() -> list[tuple[str, Callable[..., Any], argparse.ArgumentParser]]:
    return [(key, e["fn"], e["parser"]) for key, e in sorted(_REGISTRY.items())]


def manifest() -> dict[str, Any]:
    """Structured description of all registered commands, suitable for agent consumption.

    Returns a flat dict keyed by command string. Each entry contains:
      description, params (name, flags, type, required, default, help), meta.
    """
    result: dict[str, Any] = {}
    for key, _, parser in entries():
        params: list[dict[str, Any]] = []
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):  # type: ignore[reportPrivateUsage]
                continue
            if action.option_strings:
                kind = "flag" if isinstance(action, argparse._StoreTrueAction) else "option"  # type: ignore[reportPrivateUsage]
            else:
                kind = "positional"
            entry: dict[str, Any] = {
                "name": action.dest,
                "type": kind,
                "required": action.required
                if hasattr(action, "required")
                else not action.option_strings,
                "default": None if action.default is argparse.SUPPRESS else action.default,
                "help": action.help or "",
            }
            if action.option_strings:
                entry["flags"] = action.option_strings
            params.append(entry)
        result[key] = {
            "description": parser.description or "",
            "params": params,
            "meta": _REGISTRY[key]["meta"],
        }
    return result


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
