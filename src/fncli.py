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
import sys
import types
import typing
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

_REGISTRY: dict[str, tuple[Callable[..., Any], argparse.ArgumentParser]] = {}
_REQUIRED_LISTS: dict[str, list[str]] = {}
_META: dict[str, dict[str, Any]] = {}

RESERVED: frozenset[str] = frozenset({"selftest"})


class UsageError(Exception):
    pass


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


def cli(
    parent: str | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    flags: dict[str, list[str]] | None = None,
    aliases: list[str] | None = None,
    default: bool = False,
    readonly: bool = False,
    meta: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _name = name if name is not None else fn.__name__.replace("_", "-")
        key = f"{parent} {_name}".strip() if parent else _name
        desc = description or fn.__doc__ or ""
        _flags = flags or {}

        parser = argparse.ArgumentParser(prog=key, description=desc, add_help=True)
        sig = inspect.signature(fn)

        for pname, param in sig.parameters.items():
            ann = param.annotation
            raw = _unwrap_optional(ann) if ann is not inspect.Parameter.empty else str
            is_list = typing.get_origin(raw) is list
            inner = typing.get_args(raw)[0] if is_list and typing.get_args(raw) else str
            explicit_flags = _flags.get(pname)
            flag_names = explicit_flags or [f"--{pname.replace('_', '-')}"]
            no_default = param.default is inspect.Parameter.empty
            positional_optional = explicit_flags == [] and not no_default

            if is_list:
                if no_default:
                    parser.add_argument(pname, type=inner, nargs="+")
                elif positional_optional:
                    parser.add_argument(pname, type=inner, nargs="*", default=param.default)
                else:
                    parser.add_argument(
                        *flag_names, dest=pname, type=inner, nargs="*", default=param.default
                    )
            elif raw is bool:
                parser.add_argument(*flag_names, dest=pname, action="store_true", default=False)
            elif positional_optional:
                parser.add_argument(pname, type=raw, nargs="?", default=param.default)
            elif no_default:
                parser.add_argument(pname, type=raw)
            else:
                parser.add_argument(
                    *flag_names, dest=pname, type=raw, default=param.default, required=False
                )

        required_lists = [
            pname
            for pname, param in sig.parameters.items()
            if typing.get_origin(
                _unwrap_optional(
                    param.annotation if param.annotation is not inspect.Parameter.empty else str
                )
            )
            is list
            and param.default is inspect.Parameter.empty
        ]
        merged = dict(meta or {})
        if readonly:
            merged["readonly"] = True

        _REGISTRY[key] = (fn, parser)
        if merged:
            _META[key] = merged
        if required_lists:
            _REQUIRED_LISTS[key] = required_lists
        else:
            _REQUIRED_LISTS.pop(key, None)
        if default and parent:
            _REGISTRY[parent] = (fn, parser)
        for alias in aliases or []:
            alias_key = f"{parent} {alias}".strip() if parent else alias
            _REGISTRY[alias_key] = (fn, parser)
            if merged:
                _META[alias_key] = merged
            if required_lists:
                _REQUIRED_LISTS[alias_key] = required_lists
            else:
                _REQUIRED_LISTS.pop(alias_key, None)

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
    fn, parser = _REGISTRY[key]
    required_lists = _REQUIRED_LISTS.get(key, [])
    if required_lists and not any(a for a in argv if not a.startswith("-")):
        names = ", ".join(f"<{n}>" for n in required_lists)
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


_HELP_FLAGS: frozenset[str] = frozenset(("-h", "--help"))


def _selftest(prog: str, live: bool = False) -> int:
    import traceback

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

    col = max(len(r["command"]) for r in results)
    for r in results:
        h = "✓" if r["help"] == "pass" else ("·" if r["help"] == "skip" else "✗")
        lv = "✓" if r["live"] == "pass" else ("·" if r["live"] == "skip" else "✗")
        line = f"  {r['command']:<{col}}  help={h}  live={lv}"
        if "FAIL" in r.get("live", ""):
            line += f"  ({r['live']})"
        print(line)

    failed = sum(1 for r in results if "FAIL" in r["help"] or "FAIL" in r.get("live", ""))
    total = len(results)
    print(f"\n{total - failed}/{total} passed" + (f"  ({failed} failed)" if failed else ""))
    return 1 if failed else 0


def _subcommand_matches(prefix: str, token: str) -> bool:
    candidate = prefix + " " + token
    return any(k == candidate or k.startswith(candidate + " ") for k in _REGISTRY)


def try_dispatch(argv: list[str]) -> int | None:
    if len(argv) >= 2 and argv[1] == "selftest":
        return _selftest(argv[0], live="--live" in argv[2:])
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

    has_help = bool(_HELP_FLAGS & set(argv))
    non_help = [a for a in argv if a not in _HELP_FLAGS]
    prefix = " ".join(non_help)
    if len(non_help) <= 1 and not has_help:
        return None

    matches = sorted(
        (key, parser.description or "")
        for key, (_, parser) in _REGISTRY.items()
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
    all_keys = sorted((k, p.description or "") for k, (_, p) in _REGISTRY.items())
    lines = _collapse_commands(prog, [(f"{prog} {k}", desc) for k, desc in all_keys])
    col = max((len(cmd) for cmd, _ in lines), default=0)
    sys.stdout.write(f"usage: {prog} <command> [args]\n\ncommands:\n")
    for cmd, desc in lines:
        sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
    sys.stdout.write(f"\nRun `{prog} <command> --help` for details.\n")
    return 1


def run(argv: list[str] | None = None) -> None:
    code = dispatch(argv if argv is not None else sys.argv[1:])
    sys.exit(code)


def alias(src: str, dst: str) -> None:
    """Point `dst` at the same handler as `src`.

    Example: alias("space swarm tail", "space tail")
    """
    if src not in _REGISTRY:
        raise KeyError(f"alias source {src!r} not registered")
    _REGISTRY[dst] = _REGISTRY[src]
    if src in _REQUIRED_LISTS:
        _REQUIRED_LISTS[dst] = _REQUIRED_LISTS[src]


def alias_namespace(src: str, dst: str) -> None:
    """Register all commands under `src` namespace also under `dst`.

    Example: alias_namespace("life log", "life add") makes `life add a`
    dispatch the same function as `life log a`.
    """
    prefix = src + " "
    to_add = {
        dst + key[len(src) :]: val for key, val in list(_REGISTRY.items()) if key.startswith(prefix)
    }
    _REGISTRY.update(to_add)
    for alias_key in to_add:
        orig_key = src + alias_key[len(dst) :]
        if orig_key in _REQUIRED_LISTS:
            _REQUIRED_LISTS[alias_key] = _REQUIRED_LISTS[orig_key]


def commands() -> list[str]:
    return sorted(_REGISTRY)


def is_readonly(key: str) -> bool:
    return _META.get(key, {}).get("readonly", False)


def readonly_commands() -> list[str]:
    return where(readonly=True)


def meta(key: str) -> dict[str, Any]:
    return _META.get(key, {})


def where(**kwargs: Any) -> list[str]:
    return sorted(
        k
        for k in _REGISTRY
        if all(_META.get(k, {}).get(field) == value for field, value in kwargs.items())
    )


def entries() -> list[tuple[str, "Callable[..., Any]", "argparse.ArgumentParser"]]:
    return [(key, fn, parser) for key, (fn, parser) in sorted(_REGISTRY.items())]


def autodiscover(package_root: Path, package_name: str) -> None:
    for path in sorted(package_root.rglob("*.py")):
        try:
            if "@cli(" not in path.read_text():
                continue
        except OSError:
            continue
        rel = path.relative_to(package_root.parent)
        mod = ".".join(rel.with_suffix("").parts)
        if not mod.startswith(package_name + "."):
            continue
        importlib.import_module(mod)
