import sys

import pytest

import fncli
from fncli import (
    UsageError,
    bare,
    cli,
    commands,
    dispatch,
    is_readonly,
    meta,
    try_dispatch,
    where,
)


@pytest.fixture(autouse=True)
def clean_registry():
    fncli._REGISTRY.clear()
    fncli._DEFAULTS.clear()
    fncli._META.clear()
    fncli._REQUIRED_LISTS.clear()
    fncli._BARE.clear()
    yield
    fncli._REGISTRY.clear()
    fncli._DEFAULTS.clear()
    fncli._META.clear()
    fncli._REQUIRED_LISTS.clear()
    fncli._BARE.clear()


# --- registration ---


def test_registers_by_function_name():
    @cli()
    def status():
        pass

    assert "status" in commands()


def test_registers_with_parent():
    @cli("app")
    def start():
        pass

    assert "app start" in commands()


def test_name_override():
    @cli(name="st")
    def status():
        pass

    assert "st" in commands()
    assert "status" not in commands()


def test_underscore_to_hyphen():
    @cli()
    def list_all():
        pass

    assert "list-all" in commands()


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


def test_usage_error_caught_by_run_before_dispatch(capsys, monkeypatch):
    monkeypatch.setattr(
        fncli, "dispatch", lambda _argv: (_ for _ in ()).throw(UsageError("pre-dispatch boom"))
    )
    with pytest.raises(SystemExit) as exc_info:
        fncli.run(["anything"])
    assert exc_info.value.code == 1
    assert "pre-dispatch boom" in capsys.readouterr().err


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


def test_default_command_help_shows_namespace(capsys):
    @cli("app", default=True)
    def ls():
        """list items"""

    @cli("app")
    def add(name: str):
        """add an item"""

    @cli("app")
    def rm(name: str):
        """remove an item"""

    result = try_dispatch(["app", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "ls" in out
    assert "add" in out
    assert "rm" in out


def test_default_command_dispatches_without_args(capsys):
    captured: list[str] = []

    @cli("app", default=True)
    def ls():
        """list items"""
        captured.append("ls")

    @cli("app")
    def add(name: str):
        """add an item"""

    result = dispatch(["app"])
    assert result == 0
    assert captured == ["ls"]


def test_default_command_passes_args(capsys):
    captured: list[bool] = []

    @cli("app", default=True)
    def ls(verbose: bool = False):
        """list items"""
        captured.append(verbose)

    @cli("app")
    def add(name: str):
        """add an item"""

    assert dispatch(["app", "--verbose"]) == 0
    assert captured == [True]


def test_trailing_underscore_flag(capsys):
    captured: list[str] = []

    @cli()
    def query(type_: str = "all"):
        captured.append(type_)

    assert dispatch(["query", "--type", "foo"]) == 0
    assert captured == ["foo"]
    assert try_dispatch(["query", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--type TYPE" in out
    assert "TYPE_" not in out


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


# --- selftest ---


def test_selftest_passes_for_valid_commands(capsys):
    @cli("myapp")
    def status():
        """show status"""

    @cli("myapp")
    def deploy(target: str):
        """deploy to target"""

    result = try_dispatch(["myapp", "selftest"])
    assert result == 0
    out = capsys.readouterr().out
    assert "2/2 passed" in out


def test_selftest_hidden_from_help(capsys):
    @cli("myapp")
    def status():
        """show status"""

    try_dispatch(["myapp", "--help"])
    out = capsys.readouterr().out
    assert "selftest" not in out


def test_selftest_no_commands_returns_one(capsys):
    result = try_dispatch(["ghost", "selftest"])
    assert result == 1


def test_selftest_live_flag_runs_readonly(capsys):
    ran: list[bool] = []

    @cli("myapp", readonly=True)
    def ping():
        """check connectivity"""
        ran.append(True)

    result = try_dispatch(["myapp", "selftest", "--live"])
    assert result == 0
    assert ran == [True]


# --- meta ---


def test_meta_stored():
    @cli(meta={"audience": "us"})
    def backup():
        pass

    assert meta("backup") == {"audience": "us"}


def test_meta_default_empty():
    @cli()
    def status():
        pass

    assert meta("status") == {}


def test_where():
    @cli("app", meta={"audience": "us"})
    def backup():
        pass

    @cli("app", meta={"audience": "them"})
    def status():
        pass

    @cli("app", meta={"audience": "us"})
    def db():
        pass

    assert where(audience="us") == ["app backup", "app db"]
    assert where(audience="them") == ["app status"]
    assert where(audience="nope") == []


def test_meta_on_alias():
    @cli("app", aliases=["bk"], meta={"audience": "us"})
    def backup():
        pass

    assert meta("app bk") == {"audience": "us"}
    assert where(audience="us") == ["app backup", "app bk"]


def test_alias_copies_meta():
    @cli("app", readonly=True, meta={"audience": "us"})
    def backup():
        pass

    fncli.alias("app backup", "app bk")
    assert meta("app bk") == {"audience": "us", "readonly": True}
    assert is_readonly("app bk") is True


def test_alias_namespace_copies_meta():
    @cli("app log", readonly=True, meta={"audience": "us"})
    def entry():
        pass

    fncli.alias_namespace("app log", "app add")
    assert meta("app add entry") == {"audience": "us", "readonly": True}
    assert is_readonly("app add entry") is True


def test_readonly_is_meta_sugar():
    @cli("app", readonly=True)
    def status():
        pass

    assert meta("app status") == {"readonly": True}
    assert is_readonly("app status") is True
    assert where(readonly=True) == ["app status"]


def test_readonly_merges_with_meta():
    @cli("app", readonly=True, meta={"audience": "them"})
    def status():
        pass

    assert meta("app status") == {"audience": "them", "readonly": True}
    assert is_readonly("app status") is True
    assert where(audience="them", readonly=True) == ["app status"]


# --- bare ---


def test_bare_runs_on_empty_args(capsys):
    @cli("myapp")
    def start():
        """start it"""

    called: list[bool] = []

    def dashboard():
        called.append(True)
        sys.stdout.write("dashboard output\n")

    bare("myapp", dashboard)
    result = try_dispatch(["myapp"])
    assert result == 0
    assert called == [True]
    assert "dashboard" in capsys.readouterr().out


def test_bare_skipped_on_help(capsys):
    @cli("myapp")
    def start():
        """start it"""

    bare("myapp", lambda: sys.stdout.write("nope\n"))
    result = try_dispatch(["myapp", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "nope" not in out
    assert "start" in out


def test_bare_return_code():
    @cli("myapp")
    def start():
        """start it"""

    bare("myapp", lambda: 1)
    assert try_dispatch(["myapp"]) == 1


def test_bare_usage_error(capsys):
    @cli("myapp")
    def start():
        """start it"""

    def bad():
        raise UsageError("nope")

    bare("myapp", bad)
    assert try_dispatch(["myapp"]) == 1
    assert "nope" in capsys.readouterr().err


def test_bare_via_dispatch(capsys):
    @cli("myapp")
    def start():
        """start it"""

    called: list[bool] = []

    def dashboard():
        called.append(True)

    bare("myapp", dashboard)
    result = dispatch(["myapp"])
    assert result == 0
    assert called == [True]


def test_bare_does_not_block_subcommands(capsys):
    order: list[str] = []

    @cli("myapp")
    def start():
        """start it"""
        order.append("start")

    bare("myapp", lambda: order.append("bare"))
    assert try_dispatch(["myapp", "start"]) == 0
    assert order == ["start"]


# --- help= per-param descriptions ---


def test_help_dict_appears_in_argparse(capsys):
    @cli(help={"name": "who to greet", "loud": "shout it"})
    def greet(name: str, loud: bool = False):
        """say hello"""

    result = try_dispatch(["greet", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "who to greet" in out
    assert "shout it" in out


# --- required= kwarg ---


def test_required_kwarg_rejects_missing(capsys):
    @cli(required=["why"])
    def propose(content: str, why: str | None = None):
        pass

    assert dispatch(["propose", "hello"]) != 0
    err = capsys.readouterr().err
    assert "--why" in err


def test_required_kwarg_accepts_when_provided():
    captured: list[str] = []

    @cli(required=["why"])
    def propose(content: str, why: str | None = None):
        captured.append(why)

    assert dispatch(["propose", "hello", "--why", "because"]) == 0
    assert captured == ["because"]


def test_required_kwarg_shows_in_help(capsys):
    @cli(required=["why"])
    def propose(content: str, why: str | None = None):
        """make a proposal"""

    try_dispatch(["propose", "--help"])
    out = capsys.readouterr().out
    assert "required" in out.lower() or "--why WHY" in out


def test_required_kwarg_with_metavar(capsys):
    captured: list[str] = []

    @cli(required=["type_"])
    def query(type_: str | None = None):
        captured.append(type_)

    assert dispatch(["query"]) != 0
    assert dispatch(["query", "--type", "foo"]) == 0
    assert captured == ["foo"]


def test_help_dict_partial(capsys):
    @cli(help={"name": "who to greet"})
    def greet2(name: str, loud: bool = False):
        """say hello"""

    result = try_dispatch(["greet2", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "who to greet" in out
