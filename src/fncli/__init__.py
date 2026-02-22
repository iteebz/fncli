"""fncli — function signature as CLI spec.

    from fncli import cli, dispatch

    @cli("myapp")
    def status(all: bool = False):
        \"\"\"show status\"\"\"
        ...

The function IS the interface. Signature → argparse. Docstring → help.
"""

import argparse
import inspect
import io
import sys
import types
import typing
from collections.abc import Callable
from contextlib import redirect_stderr
from typing import Any

_REGISTRY: dict[str, tuple[Callable[..., Any], argparse.ArgumentParser, bool]] = {}


def _unwrap_optional(ann: Any) -> Any:
    if ann is type(None):
        return str
    if isinstance(ann, types.UnionType):
        args = [a for a in ann.__args__ if a is not type(None)]
        return args[0] if args else str
    origin = typing.get_origin(ann)
    if origin is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return args[0] if args else str
    if callable(ann):
        return ann
    return str


def cli(
    parent: str | None = None,
    description: str | None = None,
    name: str | None = None,
    internal: bool = False,
) -> Callable[..., Any]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _name = name if name is not None else fn.__name__.replace("_", "-")
        key = f"{parent} {_name}".strip() if parent else _name
        desc = description or fn.__doc__ or ""

        parser = argparse.ArgumentParser(prog=key, description=desc, add_help=True)
        sig = inspect.signature(fn)

        for pname, param in sig.parameters.items():
            ann = param.annotation
            raw = _unwrap_optional(ann) if ann is not inspect.Parameter.empty else str

            origin = typing.get_origin(raw)
            is_list = origin is list
            inner = typing.get_args(raw)[0] if is_list and typing.get_args(raw) else str

            flag = f"--{pname.replace('_', '-')}"

            if is_list:
                if param.default is inspect.Parameter.empty:
                    parser.add_argument(pname, type=inner, nargs="+")
                else:
                    parser.add_argument(flag, type=inner, nargs="*", default=param.default or [])
            elif raw is bool:
                parser.add_argument(flag, action="store_true", default=False)
            elif param.default is inspect.Parameter.empty:
                parser.add_argument(pname, type=raw)
            else:
                default = param.default
                ann = param.annotation
                is_optional = (isinstance(ann, types.UnionType) and type(None) in ann.__args__) or (
                    typing.get_origin(ann) is typing.Union and type(None) in typing.get_args(ann)
                )
                required = default is None and not is_optional
                parser.add_argument(flag, type=raw, default=default, required=required)

        _REGISTRY[key] = (fn, parser, internal)
        return fn

    return decorator


def try_dispatch(argv: list[str]) -> int | None:
    """Try to dispatch argv. Returns exit code if matched, None if no match."""
    for depth in range(len(argv), 0, -1):
        key = " ".join(argv[:depth])
        if key in _REGISTRY:
            fn, parser, _internal = _REGISTRY[key]
            stderr_buf = io.StringIO()
            try:
                with redirect_stderr(stderr_buf):
                    args = parser.parse_args(argv[depth:])
            except SystemExit as e:
                code = int(e.code) if e.code is not None else 1
                stderr_out = stderr_buf.getvalue()
                if stderr_out:
                    sys.stderr.write(stderr_out)
                elif code != 0:
                    sys.stderr.write(f"{key}: invalid arguments. Run `{key} --help`.\n")
                return code
            fn(**vars(args))
            return 0

    if set(argv) & {"-h", "--help"}:
        prefix = " ".join(a for a in argv if a not in ("-h", "--help"))
        matches = sorted(
            (key, parser.description or "")
            for key, (_, parser, _int) in _REGISTRY.items()
            if key.startswith(prefix + " ") or key == prefix
        )
        if matches:
            col = max(len(k) - len(prefix) - 1 for k, _ in matches)
            sys.stdout.write(f"usage: {prefix} <command> [args]\n\ncommands:\n")
            for key, desc in matches:
                cmd = key[len(prefix):].lstrip()
                sys.stdout.write(f"  {cmd:<{col}}  {desc}\n")
            sys.stdout.write(f"\nRun `{prefix} <command> --help` for details.\n")
            return 0

    return None


def dispatch(argv: list[str]) -> int:
    result = try_dispatch(argv)
    if result is not None:
        return result
    known = sorted(_REGISTRY)
    sys.stdout.write("known commands:\n" + "\n".join(f"  {k}" for k in known) + "\n")
    return 1


def command_map() -> dict[str, bool]:
    """Return {command_key: is_internal} for all registered commands."""
    return {key: internal for key, (_, _, internal) in _REGISTRY.items()}
