# fncli

Turn any function into a CLI. One decorator. Zero ceremony.

```python
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

**File structure and command tree are independent.** `@cli` can go anywhere — one file can register commands under multiple namespaces. The CLI shape is declared by the decorator, not the file layout.

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
| `tags: list[str]` | positional varargs (`nargs="+"`) |
| `tags: list[str] = []` | `--tags a b c` optional flag (`nargs="*"`) |
| `filter: str \| None = None` | `--filter` optional flag |

Underscores in param names → hyphenated flags: `dry_run` → `--dry-run`.
Underscores in function names → hyphenated commands: `list_all` → `list-all`.
Trailing `_` stripped for reserved words: `type_: str` → `--type`.

## Subcommands

First argument to `@cli()` is the parent namespace. Omit for top-level.

```python
@cli()
def version(): ...                    # → "version"

@cli("myapp")
def status(): ...                     # → "myapp status"

@cli("myapp server")
def start(port: int = 8080): ...      # → "myapp server start"
```

Dispatch is longest-match: `myapp server start` wins over `myapp server`.
`myapp server --help` auto-lists subcommands.

## `@cli()` options

```python
@cli(
    "myapp",               # parent namespace
    name="st",             # override command name (default: fn.__name__)
    description="...",     # override help text (default: fn.__doc__)
    flags={...},           # custom flag names or positional-optional
    help={...},            # per-param help: {"param": "description"}
    required=["param"],    # force a flag to be required
    aliases=["s"],         # additional dispatch keys
    default=True,          # run when parent is invoked bare
    readonly=True,         # metadata tag; query with readonly()
    meta={...},            # arbitrary metadata; query with meta() / where()
)
```

## Flags

```python
@cli("myapp", flags={"output": ["-o", "--output"], "target": []})
def build(target: str | None = None, output: str = "dist"): ...
```

- `["-o", "--output"]` — custom short + long flags
- `[]` — positional-optional: consumed by position (`nargs="?"`)

## Aliases

```python
@cli("myapp", aliases=["s", "stat"])
def status(): ...
# myapp s, myapp stat, myapp status all work

fncli.alias("myapp server tail", "myapp tail")
fncli.alias_namespace("myapp log", "myapp add")  # snapshot at call time
```

## Default subcommand

```python
@cli("myapp server", default=True)
def start(port: int = 8080): ...
# `myapp server` → runs start
# `myapp server start` also works
```

## Bare callback

```python
fncli.bare("myapp", lambda: print("welcome"))
# `myapp` → prints welcome
# `myapp --help` → shows subcommand list (bare is skipped)
```

## Error handling

```python
from fncli import UsageError, StateError

@cli("myapp")
def deploy(env: str):
    if env not in ("staging", "prod"):
        raise UsageError(f"unknown env: {env}")  # clean stderr + exit 1
```

`UsageError` appends "Run --help for usage." `StateError` does not (correct usage, wrong state).
Return an int to set exit code. `None` / no return → exit 0.

## Entrypoint

```python
fncli.run(["myapp", *sys.argv[1:]])       # dispatch + sys.exit
fncli.dispatch(argv)                       # returns exit code; prints help on miss
fncli.try_dispatch(argv)                   # returns None on miss
```

`autodiscover` scans for `@cli(` and imports — no routing table:

```python
fncli.autodiscover(Path(__file__).parent, "myapp")
fncli.run(["myapp", *sys.argv[1:]])
```

Unknown commands get fuzzy suggestions:
```
$ myapp strat
Unknown command: strat. Did you mean: start, status?
```

## Testing

`invoke()` captures stdout, stderr, and exit code — traps `SystemExit`:

```python
result = fncli.invoke(["myapp", "deploy", "prod"])
assert result.exit_code == 0
assert "deploying to prod" in result.stdout
```

## Shell completions

```bash
eval "$(myapp completions zsh)"   # bash, zsh, fish
```

Subcommands and flags derived from the registry at runtime. `completions` and `__complete` are hidden from `--help`.

## Selftest

```
$ myapp selftest           # smoke-test --help on all commands
$ myapp selftest --live    # also run readonly no-arg commands
$ myapp selftest --quiet   # summary only
```

Hidden from `--help`. Useful in CI.

## Introspection

```python
fncli.commands()           # sorted list of all registered keys
fncli.entries()            # [(key, fn, parser), ...]
fncli.manifest()           # structured dict of all commands — for agents
fncli.meta(key)            # metadata dict for key
fncli.where(**kwargs)      # keys matching metadata predicates
fncli.readonly(key)        # True if readonly=True
```
