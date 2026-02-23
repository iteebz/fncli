# fncli

Turn any function into a CLI. One decorator. Zero ceremony.

```python
from fncli import cli, run

@cli("myapp")
def deploy(target: str, force: bool = False):
    """ship it"""
    print(f"deploying to {target}")

run()
```

```
$ myapp deploy prod --force
deploying to prod
```

Signature → argparse. Docstring → help. Types → validation. One file, no dependencies.

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
    aliases=["s"],         # additional keys that dispatch to this handler
    default=True,          # run this when parent is invoked bare: `myapp` → runs this fn
    readonly=True,         # tag command; query with is_readonly() / readonly_commands()
    flags={...},           # see Flags
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

Without `flags`, params with defaults become `--param-name` flags automatically.

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

## Autodiscovery

```python
from pathlib import Path
import fncli

fncli.autodiscover(Path(__file__).parent, "myapp")
fncli.run()
```

Scans the package for any `.py` file containing `@cli(` and imports it. No routing table, no manual imports.

## Dispatch

```python
fncli.run(argv=None)      # dispatch sys.argv[1:], then sys.exit(code)
fncli.dispatch(argv)      # dispatch; prints help + returns 1 if nothing matched
fncli.try_dispatch(argv)  # dispatch; returns None if nothing matched (use when sharing argv)
```

Unknown commands get fuzzy suggestions:
```
$ myapp strat
Unknown command: strat. Did you mean: start, status?
```

## Selftest

Every CLI gets a hidden `selftest` command for free:

```
$ myapp selftest          # smoke-test --help on all commands
$ myapp selftest --live   # also run readonly no-arg commands
```

Hidden from `--help`. Useful in CI: `uv run myapp selftest`.

## Introspection

```python
fncli.commands()           # sorted list of all registered keys
fncli.entries()            # [(key, fn, parser), ...] for all registered commands
fncli.is_readonly(key)     # True if registered with readonly=True
fncli.readonly_commands()  # sorted list of readonly keys
```

## API reference

| symbol | signature | description |
|---|---|---|
| `cli` | `(parent?, *, name, description, flags, aliases, default, readonly)` | decorator — register fn as command |
| `run` | `(argv?)` | dispatch + sys.exit; defaults to sys.argv[1:] |
| `dispatch` | `(argv)` | dispatch; prints help and returns 1 on miss |
| `try_dispatch` | `(argv)` | dispatch; returns None on miss |
| `alias` | `(src, dst)` | point key `dst` at the same handler as `src` |
| `alias_namespace` | `(src, dst)` | clone all `src *` commands to `dst *` at call time |
| `autodiscover` | `(root: Path, pkg: str)` | scan package, import files containing `@cli(` |
| `commands` | `()` | sorted list of registered keys |
| `entries` | `()` | `[(key, fn, parser), ...]` |
| `is_readonly` | `(key)` | bool — was key registered with `readonly=True` |
| `readonly_commands` | `()` | sorted list of readonly keys |
| `UsageError` | | raise inside a command for clean stderr + exit 1 |
