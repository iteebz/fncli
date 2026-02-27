# fncli

Turn any function into a CLI. One decorator. Zero ceremony.

```python
# myapp/commands.py
from fncli import cli

@cli("myapp")
def deploy(target: str, force: bool = False):
    """ship it"""
    print(f"deploying to {target}")
```

```python
# myapp/__main__.py
import sys
import fncli
from myapp import commands as _commands; _ = _commands

def main():
    fncli.run(["myapp", *sys.argv[1:]])
```

```
$ myapp deploy prod --force
deploying to prod
```

Signature → argparse. Docstring → help. Types → validation. One file, no dependencies.

**File structure and command tree are independent.** `@cli` can go anywhere — one file can register commands under multiple namespaces. Where you put the code is an implementation detail; the CLI shape is declared by the decorator.

## Install

```
pip install fncli
```

## Type mapping

| annotation | CLI shape |
|---|---|
| `name: str` | required positional |
| `n: int` | required positional, coerced to int |
| `verbose: bool = False` | `--verbose` flag |
| `count: int = 10` | `--count 10` optional flag |
| `tags: list[str]` | positional varargs, one or more (`nargs="+"`) |
| `tags: list[str] = []` | `--tags a b c` optional flag (`nargs="*"`) |
| `filter: str \| None = None` | `--filter` optional flag |

Underscores in param names → hyphenated flags: `dry_run` → `--dry-run`.
Underscores in function names → hyphenated commands: `list_all` → `list-all`.
Trailing `_` stripped — avoids reserved words: `json_: bool = False` → `--json`.

## Subcommands

The first argument to `@cli()` is the parent namespace. Omit it for top-level commands.

```python
@cli()
def version(): ...                    # → "version"

@cli("myapp")
def status(): ...                     # → "myapp status"

@cli("myapp server")
def start(port: int = 8080): ...      # → "myapp server start"

@cli("myapp server")
def stop(force: bool = False): ...    # → "myapp server stop"
```

Dispatch is longest-match: `myapp server start` wins over `myapp server`.
`myapp server --help` auto-lists all subcommands under that namespace.

## `@cli()` options

```python
@cli(
    "myapp",               # parent namespace (str or None)
    name="st",             # override command name (default: fn.__name__, underscores → hyphens)
    description="...",     # override help text (default: fn.__doc__)
    flags={...},           # see Flags
    help={...},            # per-param help strings: {"param": "description"}
    required=["param"],    # force a flag to be required even if it has a default
    aliases=["s"],         # additional keys that dispatch to this handler
    default=True,          # run this when parent is invoked bare: `myapp` → runs this fn
    readonly=True,         # tag command; query with is_readonly() / readonly_commands()
    meta={...},            # arbitrary metadata; query with meta(key) / where(**kwargs)
)
```

## Flags

`flags` overrides how specific parameters are parsed:

```python
@cli("myapp", flags={"output": ["-o", "--output"], "target": []})
def build(target: str | None = None, output: str = "dist"): ...
```

- `["-o", "--output"]` — custom flag names (short + long)
- `[]` — positional-optional: consumed by position rather than flag name (`nargs="?"`)

## Aliases

```python
# inline — same handler, multiple keys
@cli("myapp", aliases=["s", "stat"])
def status(): ...
# myapp s, myapp stat, myapp status all work

# cross-namespace single command
fncli.alias("myapp server tail", "myapp tail")

# clone an entire namespace at registration time
fncli.alias_namespace("myapp log", "myapp add")
# copies every "myapp log *" entry to "myapp add *"
# e.g. "myapp log entry" → also "myapp add entry"
# note: snapshot at call time — commands registered after won't be included
```

## Default subcommand

```python
@cli("myapp server", default=True)
def start(port: int = 8080): ...
# `myapp server` (bare) → runs start
# `myapp server start` also works
```

## Bare callback

```python
fncli.bare("myapp", lambda: print("welcome"))
# `myapp` → prints welcome
# `myapp --help` → shows subcommand list (bare is skipped)
# `myapp start` → dispatches start (bare is skipped)
```

## Error handling

`UsageError` prints a clean message to stderr and exits 1, no traceback:

```python
from fncli import UsageError

@cli("myapp")
def deploy(env: str):
    if env not in ("staging", "prod"):
        raise UsageError(f"unknown env: {env}")
```

Return an int to set the exit code explicitly. `None` / no return → exit 0.

## Entrypoint

`argv[0]` must match the namespace used in `@cli()`:

```python
def main():
    fncli.run(["myapp", *sys.argv[1:]])
```

`autodiscover` scans the package for files containing `@cli(` and imports them — no routing table:

```python
from pathlib import Path
import fncli

fncli.autodiscover(Path(__file__).parent, "myapp")
fncli.run(["myapp", *sys.argv[1:]])
```

## Dispatch

```python
fncli.run(argv)           # dispatch argv, then sys.exit(code). argv[0] = program name.
fncli.dispatch(argv)      # dispatch; prints help + returns 1 if nothing matched
fncli.try_dispatch(argv)  # dispatch; returns None if nothing matched (use when sharing argv)
```

Unknown commands get fuzzy suggestions:
```
$ myapp strat
Unknown command: strat. Did you mean: start, status?
```

## Shell completions

Every CLI gets tab completion for free — subcommands and flags, derived from the registry at runtime.

```bash
myapp completions zsh > ~/.zsh/completions/_myapp   # bash, zsh, fish supported
```

Or inline in shell config: `eval "$(myapp completions zsh)"`. Typically called from `install.sh` — zero work for CLI authors.

`completions` and `__complete` are hidden from `--help` and added to `RESERVED`.

## Selftest

Every CLI gets a hidden `selftest` command for free:

```
$ myapp selftest           # smoke-test --help on all commands
$ myapp selftest --live    # also run readonly no-arg commands
$ myapp selftest --quiet   # summary only; failures still surfaced
```

Hidden from `--help`. Useful in CI: `uv run myapp selftest`.

## Introspection

```python
fncli.commands()           # sorted list of all registered keys
fncli.entries()            # [(key, fn, parser), ...] for all registered commands
fncli.manifest()           # flat dict of all commands with params, types, defaults, meta
fncli.is_readonly(key)     # True if registered with readonly=True
fncli.readonly_commands()  # sorted list of readonly keys
fncli.meta(key)            # dict of metadata for key
fncli.where(**kwargs)      # sorted list of keys matching metadata predicates
```

## API reference

| symbol | signature | description |
|---|---|---|
| `cli` | `(parent?, *, name, description, flags, help, required, aliases, default, readonly, meta)` | decorator — register fn as command |
| `bare` | `(namespace, fn)` | register a callback for bare namespace invocation |
| `run` | `(argv)` | dispatch + sys.exit; argv[0] = program name |
| `dispatch` | `(argv)` | dispatch; prints help and returns 1 on miss |
| `try_dispatch` | `(argv)` | dispatch; returns None on miss |
| `alias` | `(src, dst)` | point key `dst` at the same handler as `src` |
| `alias_namespace` | `(src, dst)` | clone all `src *` commands to `dst *` at call time |
| `autodiscover` | `(root: Path, pkg: str)` | scan package, import files containing `@cli(` |
| `commands` | `()` | sorted list of registered keys |
| `entries` | `()` | `[(key, fn, parser), ...]` |
| `manifest` | `()` | flat dict — all commands with description, params, meta; for agent consumption |
| `meta` | `(key)` | metadata dict for key |
| `where` | `(**kwargs)` | sorted list of keys matching metadata predicates |
| `is_readonly` | `(key)` | bool — was key registered with `readonly=True` |
| `readonly_commands` | `()` | sorted list of readonly keys |
| `UsageError` | | raise inside a command for clean stderr + exit 1 |
| `RESERVED` | | frozenset of names downstream CLIs should not register (`{"selftest", "completions", "__complete"}`) |
