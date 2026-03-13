import sys

import pytest

import fncli
from fncli import (
    RegistrationError,
    UsageError,
    cli,
    commands,
    dispatch,
    meta,
    readonly,
    try_dispatch,
    where,
)


@pytest.fixture(autouse=True)
def clean_registry():
    fncli._REGISTRY.clear()
    fncli._DEFAULTS.clear()
    fncli._BARE.clear()
    yield
    fncli._REGISTRY.clear()
    fncli._DEFAULTS.clear()
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


def test_register_duplicate_key_raises():
    @cli("app")
    def status():
        pass

    with pytest.raises(RegistrationError):

        @cli("app", name="status")
        def other():
            pass


def test_register_reserved_name_raises():
    with pytest.raises(RegistrationError):

        @cli(name="selftest")
        def reserved():
            pass


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


def test_bool_flag_default_true_generates_no_prefix():
    captured: list[bool] = []

    @cli()
    def run(verbose: bool = True):
        captured.append(verbose)

    dispatch(["run"])
    assert captured[-1] is True, "bool=True default: omitting the flag should leave it True"
    dispatch(["run", "--no-verbose"])
    assert captured[-1] is False, "--no-verbose should flip it to False"


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
    captured = capsys.readouterr()
    assert "bad input" in captured.err
    assert "bad input" in captured.out


def test_usage_error_caught_by_run_before_dispatch(capsys, monkeypatch):
    monkeypatch.setattr(
        fncli, "dispatch", lambda _argv: (_ for _ in ()).throw(UsageError("pre-dispatch boom"))
    )
    with pytest.raises(SystemExit) as exc_info:
        fncli.run(["anything"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "pre-dispatch boom" in captured.err
    assert "pre-dispatch boom" in captured.out


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
    assert readonly("app bk") is True


def test_alias_namespace_copies_meta():
    @cli("app log", readonly=True, meta={"audience": "us"})
    def entry():
        pass

    fncli.alias_namespace("app log", "app add")
    assert meta("app add entry") == {"audience": "us", "readonly": True}
    assert readonly("app add entry") is True


def test_alias_conflict_raises():
    @cli("app")
    def status():
        pass

    @cli("app")
    def health():
        pass

    with pytest.raises(RegistrationError):
        fncli.alias("app status", "app health")


def test_alias_namespace_conflict_raises():
    @cli("app log")
    def source_entry():
        pass

    @cli("app", name="source-entry")
    def app_entry():
        pass

    with pytest.raises(RegistrationError):
        fncli.alias_namespace("app log", "app")


def test_readonly_is_meta_sugar():
    @cli("app", readonly=True)
    def status():
        pass

    assert meta("app status") == {"readonly": True}
    assert readonly("app status") is True
    assert where(readonly=True) == ["app status"]


def test_readonly_merges_with_meta():
    @cli("app", readonly=True, meta={"audience": "them"})
    def status():
        pass

    assert meta("app status") == {"audience": "them", "readonly": True}
    assert readonly("app status") is True
    assert where(audience="them", readonly=True) == ["app status"]


# --- bare ---


def test_bare_runs_on_empty_args(capsys):
    @cli("myapp")
    def start():
        """start it"""

    called: list[bool] = []

    @cli(name="myapp", bare=True)
    def dashboard():
        called.append(True)
        sys.stdout.write("dashboard output\n")

    result = try_dispatch(["myapp"])
    assert result == 0
    assert called == [True]
    assert "dashboard" in capsys.readouterr().out


def test_bare_skipped_on_help(capsys):
    @cli("myapp")
    def start():
        """start it"""

    @cli(name="myapp", bare=True)
    def nope():
        sys.stdout.write("nope\n")

    result = try_dispatch(["myapp", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "nope" not in out
    assert "start" in out


def test_bare_return_code():
    @cli("myapp")
    def start():
        """start it"""

    @cli(name="myapp", bare=True)
    def bad_rc():
        return 1

    assert try_dispatch(["myapp"]) == 1


def test_bare_usage_error(capsys):
    @cli("myapp")
    def start():
        """start it"""

    @cli(name="myapp", bare=True)
    def bad():
        raise UsageError("nope")

    assert try_dispatch(["myapp"]) == 1
    captured = capsys.readouterr()
    assert "nope" in captured.err
    assert "nope" in captured.out


def test_bare_via_dispatch(capsys):
    @cli("myapp")
    def start():
        """start it"""

    called: list[bool] = []

    @cli(name="myapp", bare=True)
    def dashboard():
        called.append(True)

    result = dispatch(["myapp"])
    assert result == 0
    assert called == [True]


def test_bare_does_not_block_subcommands(capsys):
    order: list[str] = []

    @cli("myapp")
    def start():
        """start it"""
        order.append("start")

    @cli(name="myapp", bare=True)
    def bare_handler():
        order.append("bare")

    assert try_dispatch(["myapp", "start"]) == 0
    assert order == ["start"]


def test_bare_with_params(capsys):
    """Bare handlers accept positional and flag arguments."""

    @cli("myapp")
    def start():
        """start it"""

    captured: list[dict] = []

    @cli(name="myapp", bare=True, flags={"domain": ["-d", "--domain"]})
    def create(content: str, domain: str | None = None) -> None:
        captured.append({"content": content, "domain": domain})

    assert try_dispatch(["myapp", "hello world", "-d", "ops"]) == 0
    assert captured == [{"content": "hello world", "domain": "ops"}]


def test_bare_with_params_missing_required(capsys):
    """Bare handlers enforce required positional args."""

    @cli("myapp")
    def start():
        """start it"""

    @cli(name="myapp", bare=True)
    def create(content: str) -> None:
        pass

    assert try_dispatch(["myapp"]) == 1
    assert "required" in capsys.readouterr().out


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
    captured = capsys.readouterr()
    assert "--why" in captured.out


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


# --- manifest ---


def test_manifest_structure():
    @cli("myapp", readonly=True)
    def deploy(target: str, force: bool = False):
        """ship it"""

    @cli("myapp", help={"query": "search string"})
    def search(query: str, limit: int = 10):
        """search items"""

    m = fncli.manifest()
    assert "myapp deploy" in m
    assert "myapp search" in m

    deploy_entry = m["myapp deploy"]
    assert deploy_entry["description"] == "ship it"
    assert deploy_entry["meta"] == {"readonly": True}

    params = {p["name"]: p for p in deploy_entry["params"]}
    assert params["target"]["type"] == "positional"
    assert params["target"]["required"] is True
    assert params["force"]["type"] == "flag"
    assert params["force"]["default"] is False

    search_entry = m["myapp search"]
    search_params = {p["name"]: p for p in search_entry["params"]}
    assert search_params["query"]["help"] == "search string"
    assert search_params["limit"]["default"] == 10
    assert search_params["limit"]["type"] == "option"
    assert "--limit" in search_params["limit"]["flags"]


def test_help_dict_partial(capsys):
    @cli(help={"name": "who to greet"})
    def greet2(name: str, loud: bool = False):
        """say hello"""

    result = try_dispatch(["greet2", "--help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "who to greet" in out


# --- invoke ---


def test_invoke_captures_stdout():
    @cli("testapp")
    def hello():
        """say hi"""
        sys.stdout.write("hello world\n")

    result = fncli.invoke(["testapp", "hello"])
    assert result.exit_code == 0
    assert "hello world" in result.stdout
    assert result.stderr == ""


def test_invoke_nonzero_exit_code():
    @cli("testapp")
    def fail():
        """always fails"""
        return 1

    result = fncli.invoke(["testapp", "fail"])
    assert result.exit_code == 1


def test_invoke_captures_stderr_on_usage_error():
    @cli("testapp")
    def bad():
        """boom"""
        raise UsageError("wrong input")

    result = fncli.invoke(["testapp", "bad"])
    assert result.exit_code == 1
    assert "wrong input" in result.stderr


def test_invoke_traps_system_exit():
    @cli("testapp")
    def exits():
        """calls sys.exit"""
        sys.exit(42)

    result = fncli.invoke(["testapp", "exits"])
    assert result.exit_code == 42


def test_invoke_repr():
    @cli("testapp")
    def ok():
        """ok"""

    result = fncli.invoke(["testapp", "ok"])
    assert repr(result) == "Result(exit_code=0)"


# --- completions ---


def test_completions_bash_output_contains_compreply(capsys):
    @cli("myapp")
    def status():
        """check status"""

    result = try_dispatch(["myapp", "completions", "bash"])
    assert result == 0
    out = capsys.readouterr().out
    assert "COMPREPLY" in out
    assert "myapp" in out


def test_completions_zsh_output_contains_compdef(capsys):
    @cli("myapp")
    def status():
        """check status"""

    try_dispatch(["myapp", "completions", "zsh"])
    out = capsys.readouterr().out
    assert "compdef" in out
    assert "myapp" in out


def test_completions_fish_output_contains_complete_command(capsys):
    @cli("myapp")
    def status():
        """check status"""

    try_dispatch(["myapp", "completions", "fish"])
    out = capsys.readouterr().out
    assert "complete" in out
    assert "myapp" in out


def test_completions_unknown_shell_returns_one(capsys):
    @cli("myapp")
    def status():
        """check status"""

    result = try_dispatch(["myapp", "completions", "powershell"])
    assert result == 1
    err = capsys.readouterr().err
    assert "unknown shell" in err


def test_completions_defaults_to_bash_when_no_shell_arg(capsys):
    @cli("myapp")
    def status():
        """check status"""

    try_dispatch(["myapp", "completions"])
    out = capsys.readouterr().out
    assert "COMPREPLY" in out


# --- __complete ---


def test_complete_returns_subcommands(capsys):
    @cli("myapp")
    def status():
        """check status"""

    @cli("myapp")
    def report():
        """show report"""

    result = try_dispatch(["myapp", "__complete", "myapp", ""])
    assert result == 0
    out = capsys.readouterr().out
    assert "status" in out
    assert "report" in out


def test_complete_filters_by_prefix(capsys):
    @cli("myapp")
    def status():
        """check status"""

    @cli("myapp")
    def report():
        """show report"""

    try_dispatch(["myapp", "__complete", "myapp", "re"])
    out = capsys.readouterr().out
    assert "report" in out
    assert "status" not in out


def test_complete_empty_argv_returns_zero():
    result = try_dispatch(["myapp", "__complete"])
    assert result == 0


def test_autodiscover_strict_discover_on_read_error(tmp_path, monkeypatch):
    pkg_root = tmp_path / "pkg"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    bad_file = pkg_root / "cmd.py"
    bad_file.write_text("@cli()\ndef x():\n    pass\n")

    original_read_text = fncli.Path.read_text

    def boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == bad_file:
            raise OSError("read fail")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(fncli.Path, "read_text", boom)
    monkeypatch.setenv("FNCLI_STRICT_DISCOVER", "1")
    with pytest.raises(OSError):
        fncli.autodiscover(pkg_root, "pkg")
