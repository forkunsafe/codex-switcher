# Codex CLI account switcher for macOS

`codex-cli` selects a Personal or Work Codex account per project, then runs the real `codex` command with an isolated `CODEX_HOME`. It supports the system Bash 3.2 included with macOS and does not require Homebrew or GNU utilities.

## Install with Homebrew

```bash
brew install forkunsafe/tap/codex-switcher
codex-cli setup
```

The official Codex CLI must also be available in `PATH`. If needed, install it separately with `brew install --cask codex`.

Homebrew installs the program without changing your accounts. Run `codex-cli setup` once to configure isolated profiles: it asks which account owns an existing `~/.codex`, migrates that directory without merging profiles, and configures separate file-based credentials. Setup does not install another wrapper or change shell startup files.

Update with:

```bash
brew update
brew upgrade codex-switcher
```

Updates do not move profiles, change configuration, or stop processes. Check the wrapper version with `codex-cli --switcher-version`; `codex-cli --version` still reports the underlying Codex version.

If you previously used the manual installer, run `type -a codex-cli`. An older `~/.local/bin/codex-cli` can shadow the Homebrew command. Move that old wrapper out of `PATH` after verifying the Homebrew installation; keep both account profile directories.

## Install manually

Make sure `git` and the official Codex CLI are already available, then run:

```bash
bash install.sh
```

To update an existing installation without changing profiles, configuration, or running processes, use `bash install.sh --wrapper-only`. Session paths will be checked at the next wrapper launch.

The installer places the wrapper at `~/.local/bin/codex-cli`. If needed, it adds `~/.local/bin` to both `~/.bash_profile` and `~/.bashrc`. An existing `~/.codex` directory is migrated, after confirmation, to the selected profile without merging or overwriting profile data. The installer then keeps `~/.codex` as a compatibility symlink to the default profile used by the plain `codex` command.

Credentials are deliberately stored in each profile's `auth.json`, using OpenAI's documented [`cli_auth_credentials_store = "file"`](https://developers.openai.com/codex/auth) setting, so macOS Keychain does not make the two profiles share a login:

- Personal: `~/.codex-personal`
- Work: `~/.codex-work`

## Use

Run `codex-cli` from any project. The wrapper uses the Git repository root when inside a repository, or the current directory otherwise. On first use it creates a `.codex-account` marker containing exactly `personal` or `work`. In Git repositories, `/.codex-account` is added to the repository-local `.git/info/exclude`, never to `.gitignore`.

All other arguments are passed directly to Codex:

```bash
codex-cli
codex-cli resume
codex-cli exec "fix tests"
```

Show or change the project's selection:

```bash
codex-cli --account
codex-cli --switch
```

Use another account for one launch, without reading or changing the project marker or Git exclusions:

```bash
codex-cli --use personal resume <session-id>
codex-cli --use work exec "fix tests"
```

The account option must come first. It also works in a project without a marker. Session history stays in its original profile; this does not copy chats between accounts.

The installer and wrapper repair session index paths left over from migration out of `~/.codex`. They replace only paths under the old `sessions` and `archived_sessions` directories whose files exist in the selected permanent profile. SQLite backups named `state_*.sqlite.before-path-migration.*` are saved next to the database before changes. Missing files and other paths are left alone. Repeated launches do not create backups once paths are repaired.

If ordinary read-only inspection fails, a closed database without WAL sidecars can be inspected using SQLite's immutable mode. Active databases never use this fallback. If inspection remains unavailable, the wrapper warns and skips repair for that database while allowing Codex to launch.

If a database needing repair is open in another process, the wrapper lists the PIDs and executable paths and offers to terminate them or let you close them manually and retry. The default is manual closure, including when input is unavailable. Automatic termination sends SIGTERM only to the listed processes still holding that database, then waits up to five seconds. Processes hosting the current invocation must be closed manually from a separate terminal. If the database remains open, migration and launch stop. No processes are checked or stopped when no repair is needed. The repair uses macOS system `sqlite3` and `lsof`; no additional packages are required. To restore a backup, close processes using the profile and use SQLite's `.restore` command with that backup.

Change the global/default account used by a plain `codex` invocation:

```bash
codex-cli --global-switch
```

Every `codex-cli` launch prints the account and email it runs as, e.g. `codex-cli: work (you@company.com)`.

A bare interactive launch (`codex-cli`, including `codex-cli --use personal`) also explains that `/resume` shows chats from the selected account and how to open the other account's history. It waits for Enter before opening the TUI so you can read the hint. Explicit commands such as `resume` or `exec`, and launches without a terminal, do not pause.

`codex-cli` always runs Codex with `--no-daemon`: the shared app-server daemon is one per macOS user, not per `CODEX_HOME`, so it would serve whichever account it started with. The Codex desktop app and plain `codex` still use that daemon and `~/.codex`; `--global-switch` detects the running app and daemon and offers to quit them before switching.

`--switch` only changes the current project's marker. `--global-switch` only changes the `~/.codex` symlink; it does not modify any project marker or move profile data.

You may also edit `.codex-account` manually and set it to exactly `personal` or `work`.

## Uninstall the wrapper

Remove only the wrapper and its optional backup:

```bash
rm "$HOME/.local/bin/codex-cli" "$HOME/.local/bin/codex-cli.bak" 2>/dev/null || true
rm "$HOME/.local/bin/codex-session-paths.sh" 2>/dev/null || true
```

The manual installer also stores its support files in `~/.local/bin/codex-switcher-support`; that directory can be removed after uninstalling the wrapper.

You may also remove the installer-added `export PATH="$HOME/.local/bin:$PATH"` line from `~/.bash_profile` and `~/.bashrc`. This does not delete either Codex profile or its data.

For a Homebrew installation, use `brew uninstall codex-switcher`. Account profiles remain in your home directory.

## Releases

The package contains shell scripts and works on Apple Silicon and Intel Macs without separate builds. GitHub Actions runs the macOS integration checks and creates a reproducible release archive, `SHA256SUMS`, and a Homebrew formula when a tag matches `VERSION`.

To prepare a release:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build-release.py
python3 scripts/render-formula.py dist/codex-switcher-1.0.0.tar.gz --output dist/codex-switcher.rb
```

Update `VERSION` for subsequent releases, commit, then push the matching `vX.Y.Z` tag. The `forkunsafe/homebrew-tap` update workflow checks published releases daily and can also be started manually to update immediately. It verifies the archive checksum and commits the matching formula using its own repository token; no cross-repository personal token is required.

## License

Apache License 2.0 with Commons Clause License Condition v1.0; see [LICENSE](LICENSE). This is source-available software, not OSI-approved open source. Internal use at commercial companies is allowed; sale is restricted as defined by the Commons Clause. That definition also covers certain paid services and permits larger products with substantial independent value. See the [Commons Clause FAQ](https://commonsclause.com/) for details.
