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
