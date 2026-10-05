"""Thin, config-isolated wrapper around the git CLI."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Never let the operator's ~/.gitconfig or /etc/gitconfig change what gets archived
# (autocrlf, excludesFile, hooks, default branch, ...).
_BASE_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    # read-only commands (status, diff) must not refresh a source repo's index
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}


class GitError(RuntimeError):
    """A git command exited with a non-zero status."""


def git(
    *args: str,
    git_dir: Path | None = None,
    env: dict[str, str] | None = None,
    input: bytes | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> str:
    """Run git and return stdout (decoded, stripped of the trailing newline)."""
    full_env = {**os.environ, **_BASE_ENV, **(env or {})}
    cmd = ["git"]
    if git_dir is not None:
        cmd.append(f"--git-dir={git_dir}")
    cmd.extend(args)
    proc = subprocess.run(
        cmd, env=full_env, input=input, capture_output=True, timeout=timeout, check=False
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"{' '.join(cmd)} failed ({proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout.decode(errors="surrogateescape").rstrip("\n")
