# fncli

One decorator. Function signature is the CLI spec.

```python
from fncli import cli

@cli("myapp")
def deploy(target: str, force: bool = False):
    """ship it"""
    print(f"deploying to {target}")
```

```
$ myapp deploy prod --force
deploying to prod
```

Signature → parser. Docstring → help. Types → validation. One file, no dependencies.

## Install

```
pip install fncli
```

## Type mapping

| annotation | CLI behavior |
|---|---|
| `name: str` | required positional |
| `n: int` | required positional, coerced to int |
| `verbose: bool = False` | `--verbose` flag |
| `count: int = 10` | `--count 10` optional |
| `tags: list[str]` | positional varargs |
| `tags: list[str] = []` | `--tags a b c` optional |
| `filter: str \| None = None` | `--filter` optional |

Naming: `dry_run` → `--dry-run`. `list_all` → `list-all`. Trailing `_` stripped: `type_` → `--type`.

## Subcommands

First argument to `@cli()` is the parent namespace.

```python
@cli()
def version(): ...                    # → "version"

@cli("myapp")
def status(): ...                     # → "myapp status"

@cli("myapp server")
def start(port: int = 8080): ...      # → "myapp server start"
```

Dispatch is longest-match. `myapp server --help` auto-lists subcommands.

## Bare namespaces

When a namespace has one obvious default action, use `bare=True`. The namespace itself becomes the command — no subcommand needed.

```python
@cli("ledger", bare=True, flags={"domain": ["-d"]}, required=["domain"])
def insight(content: str, domain: str | None = None):
    """log an insight"""
    ...

@cli("ledger insight")
def close(ref: str):
    """close an insight"""
    ...
```

```
$ ledger insight "auth is fragile" -d security    # bare — namespace IS the verb
$ ledger insight close i/abc123                    # named subcommand
$ ledger insight --help
usage: ledger insight <content> [-d DOMAIN]
       log an insight

   or: ledger insight <command> [args]

commands:
  close  close an insight
```

**How it works:** `@cli("ledger", bare=True)` on function `insight` registers a bare handler for namespace `"ledger insight"` (parent + fn name). Bare handlers:
- Accept full positional and flag arguments (same parsing as `@cli`)
- Don't appear in `commands()` or `manifest()` — they're invisible defaults
- Yield to named subcommands (dispatch checks registry first)
- Delegate `--help` to namespace help (shows subcommands + bare usage)

**When to use bare vs named:** If the action is a lifecycle verb agents should know (`propose`, `commit`, `approve`), name it. If the action is just "the thing this namespace does" (create/add), make it bare.

**Stacking bare + named** for commands that are both the default AND a lifecycle verb:

```python
@cli("ledger", name="decision", bare=True, required=["why"])
@cli("ledger decision", required=["why"])
def propose(content: str, why: str | None = None):
    """propose a decision"""
    ...
```

Both `ledger decision "X" --why "Y"` and `ledger decision propose "X" --why "Y"` work.

## `@cli()` options

```python
@cli(
    "myapp",               # parent namespace
    name="st",             # override command name (default: fn.__name__)
    description="...",     # override help text (default: fn.__doc__)
    flags={...},           # custom flag names or positional-optional
    help={...},            # per-param help: {"param": "description"}
    required=["param"],    # force a defaulted param to be required
    aliases=["s"],         # additional dispatch keys
    default=True,          # run when parent is invoked with no subcommand match
    bare=True,             # register as bare handler for parent + fn_name namespace
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
- `[]` — positional-optional: consumed by position

## Aliases

```python
@cli("myapp", aliases=["s", "stat"])
def status(): ...
# myapp s, myapp stat, myapp status all work

fncli.alias("myapp server tail", "myapp tail")
fncli.alias_namespace("myapp log", "myapp add")
```

## Error handling

```python
from fncli import UsageError, StateError

@cli("myapp")
def deploy(env: str):
    if env not in ("staging", "prod"):
        raise UsageError(f"unknown env: {env}")  # stderr + exit 1 + "Run --help"
```

`UsageError` → appends "Run --help for usage." `StateError` → does not (correct syntax, wrong state).
Return an int for exit code. `None` / no return → exit 0.

## Entrypoint

```python
fncli.run(["myapp", *sys.argv[1:]])       # dispatch + sys.exit
fncli.dispatch(argv)                       # returns exit code
fncli.try_dispatch(argv)                   # returns None on miss
```

`autodiscover` scans for `@cli(` and auto-imports — no routing table:

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

```python
result = fncli.invoke(["myapp", "deploy", "prod"])
assert result.exit_code == 0
assert "deploying to prod" in result.stdout
```

`invoke()` captures stdout, stderr, and exit code. Traps `SystemExit`.

## Shell completions

```bash
eval "$(myapp completions zsh)"   # bash, zsh, fish
```

## Selftest

```
$ myapp selftest           # smoke-test --help on all commands
$ myapp selftest --live    # also run readonly no-arg commands
```

## Introspection

```python
fncli.commands()           # sorted list of all registered keys
fncli.entries()            # [(key, fn, params), ...]
fncli.manifest()           # structured dict — for agent consumption
fncli.meta(key)            # metadata dict
fncli.where(**kwargs)      # keys matching metadata predicates
fncli.readonly(key)        # True if readonly=True
```
