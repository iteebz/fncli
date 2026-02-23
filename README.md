# fncli

Turn any function into a CLI. One decorator. Zero ceremony.

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

Signature → argparse. Docstring → help. Types → validation.

## Install

```
pip install fncli
```

## Type mapping

| signature | CLI behavior |
|---|---|
| `name: str` | positional arg |
| `verbose: bool = False` | `--verbose` flag |
| `count: int = 10` | `--count 10` |
| `tags: list[str]` | positional varargs |
| `filter: str \| None = None` | optional `--filter` |

## Subcommands

Parent string creates hierarchy:

```python
@cli("myapp")
def status(): ...

@cli("myapp server")
def start(port: int = 8080): ...

@cli("myapp server")
def stop(force: bool = False): ...
```

```
$ myapp server start --port 3000
$ myapp server stop --force
```

## Autodiscovery

```python
fncli.autodiscover(Path(__file__).parent, "myapp")
fncli.run()
```

Any file in the package with `@cli()` registers itself. No imports, no routing table.

## Aliases

```python
@cli("myapp", aliases=["s"])              # myapp s → myapp status
def status(): ...

fncli.alias("myapp server tail", "myapp tail")  # cross-namespace
fncli.alias_namespace("myapp log", "myapp add")  # clone namespace
```

## Flags

```python
@cli("myapp", flags={"watch": ["-w", "--watch"], "target": []})
def tail(target: str = None, watch: bool = False): ...
```

`[]` = positional-optional. List of strings = custom flag names.

## Dispatch

```python
fncli.try_dispatch(argv)  # returns exit code, or None if no match
fncli.dispatch(argv)      # returns exit code, prints help if no match
fncli.run()               # dispatch + sys.exit
```

## API

| function | |
|---|---|
| `cli()` | decorator — register function as command |
| `autodiscover(root, pkg)` | scan package, import `@cli` modules |
| `try_dispatch(argv)` | match + run, None on miss |
| `dispatch(argv)` | match + run, help on miss |
| `run(argv?)` | dispatch + sys.exit |
| `alias(src, dst)` | point one key at another |
| `alias_namespace(src, dst)` | clone all commands under prefix |
| `commands()` | sorted registered keys |
| `entries()` | (key, fn, parser) tuples |

One file. ~340 lines. No dependencies.
