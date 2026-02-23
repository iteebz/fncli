import pytest

from fncli import (
    _REGISTRY,
    UsageError,
    cli,
    commands,
    dispatch,
    try_dispatch,
)


@pytest.fixture(autouse=True)
def clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


# --- registration ---


def test_registers_by_function_name():
    @cli()
    def status():
        pass

    assert "status" in _REGISTRY


def test_registers_with_parent():
    @cli("app")
    def start():
        pass

    assert "app start" in _REGISTRY


def test_name_override():
    @cli(name="st")
    def status():
        pass

    assert "st" in _REGISTRY
    assert "status" not in _REGISTRY


def test_underscore_to_hyphen():
    @cli()
    def list_all():
        pass

    assert "list-all" in _REGISTRY


# --- argument parsing ---


def test_positional_required():
    captured: list[str] = []

    @cli()
    def greet(name: str):
        captured.append(name)

    assert dispatch(["greet", "alice"]) == 0
    assert captured == ["alice"]


def test_flag_with_default():
    captured: list[bool] = []

    @cli()
    def run(verbose: bool = False):
        captured.append(verbose)

    dispatch(["run"])
    assert captured[-1] is False
    dispatch(["run", "--verbose"])
    assert captured[-1] is True


def test_optional_str_default_none(capsys):
    captured: list[str | None] = []

    @cli()
    def show(filter: str | None = None):
        captured.append(filter)

    assert dispatch(["show"]) == 0
    assert captured[-1] is None
    assert dispatch(["show", "--filter", "foo"]) == 0
    assert captured[-1] == "foo"


def test_required_flag_when_non_optional_none_default():
    captured: list[str | None] = []

    @cli()
    def push(target: str | None = None):
        captured.append(target)

    assert dispatch(["push"]) == 0
    assert captured[-1] is None
    assert dispatch(["push", "--target", "prod"]) == 0
    assert captured[-1] == "prod"


def test_bare_none_default_not_required():
    captured: list = []

    @cli()
    def deploy(env: str = None):  # type: ignore[assignment]  # noqa: RUF013
        captured.append(env)

    assert dispatch(["deploy"]) == 0
    assert captured[-1] is None
    assert dispatch(["deploy", "--env", "prod"]) == 0
    assert captured[-1] == "prod"


def test_list_positional():
    captured: list[list[str]] = []

    @cli()
    def tag(labels: list[str]):
        captured.append(labels)

    assert dispatch(["tag", "a", "b", "c"]) == 0
    assert captured[-1] == ["a", "b", "c"]


def test_list_flag_optional():
    captured: list[list[str]] = []

    @cli()
    def tag(labels: list[str] = []):  # noqa: B006
        captured.append(labels)

    assert dispatch(["tag"]) == 0
    assert captured[-1] == []
    assert dispatch(["tag", "--labels", "x", "y"]) == 0
    assert captured[-1] == ["x", "y"]


def test_int_positional():
    captured: list[int] = []

    @cli()
    def repeat(n: int):
        captured.append(n)

    assert dispatch(["repeat", "5"]) == 0
    assert captured[-1] == 5


# --- dispatch ---


def test_dispatch_no_match_returns_one(capsys):
    assert dispatch(["unknown"]) == 1


def test_try_dispatch_no_match_returns_none():
    assert try_dispatch(["nope"]) is None


def test_dispatch_longest_match():
    order: list[str] = []

    @cli()
    def app():
        order.append("app")

    @cli("app")
    def start():
        order.append("app start")

    assert dispatch(["app", "start"]) == 0
    assert order == ["app start"]


def test_dispatch_parent_when_no_subcommand_match():
    order: list[str] = []

    @cli()
    def app():
        order.append("app")

    @cli("app")
    def start():
        order.append("app start")

    assert dispatch(["app"]) == 0
    assert order == ["app"]


def test_dispatch_parent_called_with_flag_even_with_subcommands():
    order: list[str] = []

    @cli()
    def app(verbose: bool = False):
        order.append(f"app:{verbose}")

    @cli("app")
    def start():
        order.append("app start")

    assert dispatch(["app", "--verbose"]) == 0
    assert order == ["app:True"]


# --- return value propagation ---


def test_int_return_propagates():
    @cli()
    def fail():
        return 1

    @cli()
    def succeed():
        return 0

    @cli()
    def none_return():
        pass

    assert dispatch(["fail"]) == 1
    assert dispatch(["succeed"]) == 0
    assert dispatch(["none-return"]) == 0


# --- UsageError ---


def test_usage_error_returns_one(capsys):
    @cli()
    def fail():
        raise UsageError("bad input")

    assert dispatch(["fail"]) == 1
    assert "bad input" in capsys.readouterr().err


# --- invalid args ---


def test_invalid_args_returns_nonzero(capsys):
    @cli()
    def cmd(n: int):
        pass

    assert dispatch(["cmd", "notanint"]) != 0


# --- help ---


def test_help_lists_subcommands(capsys):
    @cli("myapp")
    def start():
        """start the app"""

    @cli("myapp")
    def stop():
        """stop the app"""

    result = try_dispatch(["myapp", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "start" in out
    assert "stop" in out


def test_help_unknown_prefix_returns_error():
    result = try_dispatch(["ghost", "--help"])
    assert result == 1


# --- commands() ---


def test_commands_returns_sorted():
    @cli()
    def z():
        pass

    @cli()
    def a():
        pass

    assert commands() == ["a", "z"]


def test_commands_empty():
    assert commands() == []
