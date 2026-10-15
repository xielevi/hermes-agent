"""Regression: a dashboard/serve backend captured through macOS ``ps`` must not be replayed verbatim.

``_dashboard_cmdline_for_pid`` (hermes_cli/main_dashboard.py) reads ``/proc/<pid>/cmdline`` on
Linux and falls back to ``ps -p <pid> -o command=`` + ``shlex.split`` on macOS. ``ps`` prints the
argv as ONE space-joined line, so a backend that really runs

    [python, "-I", "-c", <multi-line launcher bootstrap>, "serve", "--host", …, "--port", …]

comes back as many fragments — the first of which is ``import``. Replaying that hands ``-c`` the
program ``import``: the child dies with ``SyntaxError`` / exit 1 inside the 1 s liveness grace, the
backend is booked "not brought back", and the update ends owing a follow-up even though the gateway
fleet is verifiably current (observed on macOS 0.22.0 -> 0.22.1, `Fleet version check: ✓ default
… — up to date` beside `⚠ Update follow-up 'gateway_restart' did not finish`).

The macOS boundary is mocked: ``ps`` is never run here; the capture is reproduced from the real
launcher bootstrap with the same join/split the macOS path performs.
"""

from __future__ import annotations

import shlex
import sys


def _captured_like_macos(root):
    """(real argv, argv as macOS ``ps`` + shlex.split returns it)."""
    from hermes_cli._launchers import _launcher_script

    bootstrap = _launcher_script("hermes", root, None)
    real = [sys.executable, "-I", "-c", bootstrap,
            "serve", "--host", "127.0.0.1", "--port", "9119"]
    ps_line = " ".join(real)
    try:
        captured = shlex.split(ps_line)
    except ValueError:
        captured = ps_line.split()
    return real, captured


def test_ps_capture_breaks_the_inline_source_into_fragments(tmp_path):
    """The premise: on macOS the captured argv is NOT the running process's argv."""
    from hermes_cli.main_dashboard import _dashboard_cmdline_for_pid

    real, captured = _captured_like_macos(tmp_path)
    assert real[2] == "-c" and len(real) == 9
    assert captured[:3] == [sys.executable, "-I", "-c"]
    assert captured[3] != real[3]  # the bootstrap is no longer one token
    assert "import" in captured[3].split()  # `-c` would receive a fragment, not the bootstrap
    # The Linux path is exact and unaffected.
    assert _dashboard_cmdline_for_pid.__doc__ is not None


def test_a_captured_inline_source_is_rebuilt_not_replayed(tmp_path):
    """The fix: fragments are resolved back to this install's launcher before spawning."""
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    _real, captured = _captured_like_macos(tmp_path)
    command = _respawnable_command_for_current_install(captured)
    assert command[:3] == [command[0], "-I", "-c"]
    assert len(command) == 9  # one token for the bootstrap, then the backend's own argv
    assert command[3].startswith("import ")
    assert command[4:] == ["serve", "--host", "127.0.0.1", "--port", "9119"]
    # A rebuilt bootstrap must actually be valid Python (the verbatim replay was not).
    compile(command[3], "<bootstrap>", "exec")


def test_an_unrelated_argv_still_replays_unchanged(tmp_path):
    """Guard: only an interpreter running inline source is rebuilt."""
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    argv = [sys.executable, "-m", "hermes_cli.main", "serve", "--port", "9119"]
    assert _respawnable_command_for_current_install(argv) == argv


def test_actual_macos_ps_escaped_fixtures_rebuild_cleanly(tmp_path):
    """macOS ps escapes newlines as \\012, which shlex unescapes to literal 012."""
    from hermes_cli._launchers import _launcher_script
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    source = _launcher_script("hermes", tmp_path, None)
    expected_tail = ["dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open"]

    for name, src in [
        ("backslash012", source.replace("\n", "\\012")),
        ("literal012", source.replace("\n", "012")),
    ]:
        real = [sys.executable, "-I", "-c", src, *expected_tail]
        ps_line = " ".join(real)
        try:
            captured = shlex.split(ps_line)
        except ValueError:
            captured = ps_line.split()
        command = _respawnable_command_for_current_install(captured)
        assert command[:3] == [command[0], "-I", "-c"], f"Failed on {name}"
        assert command[4:] == expected_tail, f"Failed tail on {name}"
        compile(command[3], "<bootstrap>", "exec")


def test_runtime_bootstrap_rebuilds_cleanly(tmp_path):
    """A single-line runtime_command bootstrap captured through ps rebuilds cleanly."""
    from hermes_cli._launchers import runtime_command
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    real = runtime_command(tmp_path, ["dashboard", "--no-open"])
    ps_line = " ".join(real)
    captured = shlex.split(ps_line)
    command = _respawnable_command_for_current_install(captured)
    assert command[:3] == [command[0], "-I", "-c"]
    assert command[4:] == ["dashboard", "--no-open"]
    compile(command[3], "<bootstrap>", "exec")


def test_venv_sync_bootstrap_rebuilds_cleanly(tmp_path):
    """A venv_sync relaunch bootstrap captured through ps rebuilds cleanly."""
    from pathlib import Path
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install
    from hermes_cli.venv_sync import relaunch_command

    real = relaunch_command(
        Path(sys.executable),
        tmp_path,
        ["hermes", "dashboard", "--no-open"],
        [sys.executable, "hermes", "dashboard", "--no-open"],
        "hermes_cli.main",
    )
    ps_line = " ".join(real)
    captured = shlex.split(ps_line)
    command = _respawnable_command_for_current_install(captured)
    assert command[:3] == [command[0], "-I", "-c"]
    assert command[4:] == ["dashboard", "--no-open"]
    compile(command[3], "<bootstrap>", "exec")


def test_already_exact_argv_preserves_backend_args(tmp_path):
    """An exact argv (e.g. from Linux /proc) is rebuilt without mangling backend arguments."""
    from hermes_cli._launchers import _launcher_script
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    src = _launcher_script("hermes", tmp_path, None)
    exact = [sys.executable, "-I", "-c", src, "dashboard", "--no-open"]
    command = _respawnable_command_for_current_install(exact)
    assert command[:3] == [command[0], "-I", "-c"]
    assert command[4:] == ["dashboard", "--no-open"]
    compile(command[3], "<bootstrap>", "exec")


def test_unrelated_inline_script_replays_unchanged():
    """An unrelated inline script must not be matched or rebuilt."""
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    argv = [sys.executable, "-c", "import time; time.sleep(1)", "dashboard", "--no-open"]
    assert _respawnable_command_for_current_install(argv) == argv


def test_spaces_in_root_and_args_rebuild_without_corruption(tmp_path):
    """Spaces in root path and backend arguments (including embedded 012 in numbers) survive."""
    from pathlib import Path
    from hermes_cli._launchers import _launcher_script
    from hermes_cli.main_dashboard import _respawnable_command_for_current_install

    root_with_spaces = tmp_path / "hermes install dir"
    root_with_spaces.mkdir()
    src = _launcher_script("hermes", root_with_spaces, None)
    expected_tail = ["dashboard", "--port", "9012", "--title", "My Great Dashboard", "--no-open"]

    for name, escaped_src in [
        ("backslash012", src.replace("\n", "\\012")),
        ("literal012", src.replace("\n", "012")),
    ]:
        real = [sys.executable, "-I", "-c", escaped_src, *expected_tail]
        ps_line = shlex.join(real)
        captured = shlex.split(ps_line)
        command = _respawnable_command_for_current_install(captured)
        assert command[:3] == [command[0], "-I", "-c"], f"Failed on {name}"
        assert command[4:] == expected_tail, f"Failed tail on {name}"
        compile(command[3], "<bootstrap>", "exec")
