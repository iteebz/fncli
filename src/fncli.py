"""fncli — function signature as CLI spec.

    from fncli import cli, run, UsageError

    @cli("myapp")
    def status(all: bool = False):
        \"\"\"show status\"\"\"
        ...

The function IS the interface. Signature → argparse. Docstring → help.
"""

import argparse
import importlib
import inspect
import io
import sys
import types
import typing
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

_REGISTRY: dict[str, tuple[Callable[..., Any], argparse.ArgumentParser]] = {}
_REQUIRED_LISTS: dict[str, list[str]] = {}


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
            if typing.get_origin(_unwrap_optional(param.annotation if param.annotation is not inspect.Parameter.empty else str)) is list
            and param.default is inspect.Parameter.empty
        ]
        _REGISTRY[key] = (fn, parser)
        if required_lists:
            _REQUIRED_LISTS[key] = required_lists
        else:
            _REQUIRED_LISTS.pop(key, None)
        for alias in aliases or []:
            alias_key = f"{parent} {alias}".strip() if parent else alias
            _REGISTRY[alias_key] = (fn, parser)
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


def _has_subcommands(prefix: str) -> bool:
    return any(k.startswith(prefix + " ") for k in _REGISTRY)


def try_dispatch(argv: list[str]) -> int | None:
    for depth in range(len(argv), 0, -1):
        key = " ".join(argv[:depth])
        if key in _REGISTRY:
            remaining = argv[depth:]
            if remaining and not remaining[0].startswith("-") and _has_subcommands(key):
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
        col = max((len(k) - len(prefix) - 1 for k, _ in matches), default=0)
        sys.stdout.write(f"usage: {prefix} <command> [args]\n\ncommands:\n")
        for key, desc in matches:
            cmd = key[len(prefix) :].lstrip()
            sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
        sys.stdout.write(f"\nRun `{prefix} <command> --help` for details.\n")
        return 0 if has_help else 1

    return None


def dispatch(argv: list[str]) -> int:
    result = try_dispatch(argv)
    if result is not None:
        return result
    known = sorted(_REGISTRY)
    sys.stdout.write("known commands:\n" + "\n".join(f"  {k}" for k in known) + "\n")
    return 1


def run(argv: list[str] | None = None) -> None:
    code = dispatch(argv if argv is not None else sys.argv[1:])
    sys.exit(code)


def commands() -> list[str]:
    return sorted(_REGISTRY)


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
