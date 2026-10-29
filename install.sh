#!/usr/bin/env bash

set -euo pipefail

PERSONAL_HOME="$HOME/.codex-personal"
WORK_HOME="$HOME/.codex-work"
LEGACY_HOME="$HOME/.codex"
DESKTOP_APP_ID="com.openai.codex"
LOCAL_BIN="$HOME/.local/bin"
INSTALL_PATH="$LOCAL_BIN/codex-cli"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
PATH_CHANGED=0

print_error() {
    printf 'Error: %s\n' "$*" >&2
}

require_command() {
    local command_name=$1
    local guidance=${2-}

    if ! command -v "$command_name" >/dev/null 2>&1; then
        print_error "$command_name is required but was not found in PATH."
        if [ -n "$guidance" ]; then
            printf '%s\n' "$guidance" >&2
        fi
        exit 1
    fi
}

directory_has_data() {
    [ -n "$(find "$1" -mindepth 1 -print -quit 2>/dev/null)" ]
}

migration_target_available() {
    local target=$1

    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        return 0
    fi
    if [ -d "$target" ] && [ ! -L "$target" ] && ! directory_has_data "$target"; then
        return 0
    fi
    return 1
}

read_global_account() {
    local link_target

    if [ ! -L "$LEGACY_HOME" ]; then
        return 1
    fi
    link_target=$(readlink "$LEGACY_HOME")
    case "$link_target" in
        "$PERSONAL_HOME") GLOBAL_ACCOUNT=personal ;;
        "$WORK_HOME") GLOBAL_ACCOUNT=work ;;
        *) return 2 ;;
    esac
    return 0
}

prompt_for_legacy_account() {
    local choice normalized

    while :; do
        printf 'Existing Codex home found:\n'
        printf '  %s\n\n' "$LEGACY_HOME"
        printf 'Which account does it belong to?\n\n'
        printf '1) Personal\n'
        printf '2) Work\n\n'
        printf 'Choose Codex account [1/2]: '

        if ! IFS= read -r choice; then
            printf '\n' >&2
            print_error "No account was selected; no data was moved."
            return 1
        fi

        normalized=$(printf '%s' "$choice" | tr '[:upper:]' '[:lower:]')
        case "$normalized" in
            1|personal|p)
                MIGRATION_ACCOUNT=personal
                MIGRATION_TARGET=$PERSONAL_HOME
                ;;
            2|work|w)
                MIGRATION_ACCOUNT=work
                MIGRATION_TARGET=$WORK_HOME
                ;;
            *)
                printf 'Please enter 1, 2, personal, work, p, or w.\n\n' >&2
                continue
                ;;
        esac

        if migration_target_available "$MIGRATION_TARGET"; then
            return 0
        fi

        printf '\nCannot migrate %s to %s because the target already contains data or is not a regular directory.\n' "$LEGACY_HOME" "$MIGRATION_TARGET" >&2
        printf 'Nothing has been moved or overwritten. Choose the other account, or press Control-C and resolve the directories manually.\n\n' >&2
    done
}

prompt_for_default_account() {
    local choice normalized

    while :; do
        printf '1) Personal\n'
        printf '2) Work\n\n'
        printf 'Choose Codex account [1/2]: '

        if ! IFS= read -r choice; then
            printf '\n' >&2
            print_error "No default account was selected."
            return 1
        fi

        normalized=$(printf '%s' "$choice" | tr '[:upper:]' '[:lower:]')
        case "$normalized" in
            1|personal|p)
                MIGRATION_ACCOUNT=personal
                MIGRATION_TARGET=$PERSONAL_HOME
                return 0
                ;;
            2|work|w)
                MIGRATION_ACCOUNT=work
                MIGRATION_TARGET=$WORK_HOME
                return 0
                ;;
            *)
                printf 'Please enter 1, 2, personal, work, p, or w.\n\n' >&2
                ;;
        esac
    done
}

desktop_app_running() {
    [ "$(osascript -e "application id \"$DESKTOP_APP_ID\" is running" 2>/dev/null)" = "true" ]
}

daemon_running() {
    pgrep -f 'codex app-server' >/dev/null 2>&1
}

# The Codex app and plain codex share one app-server daemon that keeps the
# account it started with, so it must be stopped before ~/.codex is moved.
stop_global_codex() {
    local answer waited=0

    if ! desktop_app_running && ! daemon_running; then
        return 0
    fi

    printf 'Still using %s:\n' "$LEGACY_HOME"
    if desktop_app_running; then
        printf '  - Codex desktop app\n'
    fi
    if daemon_running; then
        printf '  - shared app-server daemon (also used by plain codex sessions)\n'
    fi
    printf '\n'
    printf 'Quit them now? Unsaved work in the app and plain codex sessions will stop [y/N]: '

    if ! IFS= read -r answer; then
        answer=
    fi
    case "$answer" in
        y|Y|yes|YES) ;;
        *)
            print_error "Quit them before migrating $LEGACY_HOME. Nothing was moved."
            exit 1
            ;;
    esac

    if desktop_app_running; then
        osascript -e "tell application id \"$DESKTOP_APP_ID\" to quit" >/dev/null 2>&1 || true
        while desktop_app_running && [ "$waited" -lt 15 ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if desktop_app_running; then
            print_error "The Codex app did not quit within 15 seconds. Nothing was moved."
            return 1
        fi
        printf 'Codex app quit.\n'
    fi

    if daemon_running; then
        codex app-server daemon stop >/dev/null 2>&1 || true
        sleep 1
        if daemon_running; then
            pkill -f 'codex app-server' 2>/dev/null || true
            sleep 1
        fi
        if daemon_running; then
            print_error "The app-server daemon is still running. Nothing was moved."
            pgrep -fl 'codex app-server' >&2 || true
            return 1
        fi
        printf 'App-server daemon stopped.\n'
    fi
    printf '\n'
}

migrate_legacy_home() {
    local global_status=0

    if [ -L "$LEGACY_HOME" ]; then
        read_global_account || global_status=$?
        if [ "$global_status" -eq 0 ]; then
            printf 'Global Codex account: %s\n' "$GLOBAL_ACCOUNT"
            printf '%s -> %s\n\n' "$LEGACY_HOME" "$(readlink "$LEGACY_HOME")"
            return 0
        fi
        print_error "$LEGACY_HOME is a symbolic link to an unknown target: $(readlink "$LEGACY_HOME")"
        printf 'Resolve the link manually, then run the installer again. Nothing was moved.\n' >&2
        exit 1
    fi
    if [ ! -e "$LEGACY_HOME" ] && [ ! -L "$LEGACY_HOME" ]; then
        return 0
    fi
    if [ ! -d "$LEGACY_HOME" ]; then
        print_error "$LEGACY_HOME exists but is not a directory. Nothing was moved."
        exit 1
    fi
    if ! migration_target_available "$PERSONAL_HOME" && ! migration_target_available "$WORK_HOME"; then
        print_error "Both profile targets already contain data (or are not regular directories)."
        printf 'Resolve %s, %s, and %s manually, then run the installer again. Nothing was moved.\n' "$LEGACY_HOME" "$PERSONAL_HOME" "$WORK_HOME" >&2
        exit 1
    fi

    prompt_for_legacy_account
    stop_global_codex

    if [ -d "$MIGRATION_TARGET" ]; then
        rmdir "$MIGRATION_TARGET"
    fi
    mv "$LEGACY_HOME" "$MIGRATION_TARGET"
    ln -s "$MIGRATION_TARGET" "$LEGACY_HOME"
    printf 'Migrated %s to %s (%s).\n\n' "$LEGACY_HOME" "$MIGRATION_TARGET" "$MIGRATION_ACCOUNT"
    printf 'Created compatibility link:\n  %s -> %s\n\n' "$LEGACY_HOME" "$MIGRATION_TARGET"
    GLOBAL_ACCOUNT=$MIGRATION_ACCOUNT
}

ensure_global_link() {
    local global_status=0 target_home

    read_global_account || global_status=$?
    if [ "$global_status" -eq 0 ]; then
        return 0
    fi
    if [ "$global_status" -eq 2 ]; then
        print_error "$LEGACY_HOME is a symbolic link to an unknown target: $(readlink "$LEGACY_HOME")"
        exit 1
    fi
    if [ -e "$LEGACY_HOME" ]; then
        print_error "$LEGACY_HOME exists but is not a symbolic link. Nothing was changed."
        exit 1
    fi

    printf 'Choose the default account for the plain codex command.\n\n'
    prompt_for_default_account
    target_home=$MIGRATION_TARGET
    ln -s "$target_home" "$LEGACY_HOME"
    GLOBAL_ACCOUNT=$MIGRATION_ACCOUNT
    printf 'Created global Codex link:\n  %s -> %s\n\n' "$LEGACY_HOME" "$target_home"
}

configure_file_credentials() {
    local profile_home=$1
    local config_path="$profile_home/config.toml"
    local temp_path

    temp_path=$(mktemp "$profile_home/.config.toml.tmp.XXXXXX")
    if [ -f "$config_path" ]; then
        awk '
            BEGIN { in_table = 0; written = 0 }
            /^[[:space:]]*\[/ {
                if (!written) {
                    print "cli_auth_credentials_store = \"file\""
                    print ""
                    written = 1
                }
                in_table = 1
            }
            !in_table && /^[[:space:]]*cli_auth_credentials_store[[:space:]]*=/ {
                if (!written) {
                    print "cli_auth_credentials_store = \"file\""
                    written = 1
                }
                next
            }
            { print }
            END {
                if (!written) {
                    print "cli_auth_credentials_store = \"file\""
                }
            }
        ' "$config_path" > "$temp_path"
    else
        printf 'cli_auth_credentials_store = "file"\n' > "$temp_path"
    fi
    mv -f "$temp_path" "$config_path"
}

ensure_path_line() {
    local shell_file=$1

    if [ -f "$shell_file" ] && grep -F -x -q "$PATH_LINE" "$shell_file"; then
        return 0
    fi

    if [ -s "$shell_file" ]; then
        printf '\n' >> "$shell_file"
    fi
    printf '%s\n' "$PATH_LINE" >> "$shell_file"
    PATH_CHANGED=1
    printf 'Updated: %s\n' "$shell_file"
}

install_wrapper() {
    local script_dir wrapper_source install_temp support_file support_dir

    script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    wrapper_source="$script_dir/codex-cli"
    if [ ! -f "$wrapper_source" ]; then
        print_error "Wrapper source not found: $wrapper_source"
        exit 1
    fi

    mkdir -p "$LOCAL_BIN"
    if [ -d "$INSTALL_PATH" ] && [ ! -L "$INSTALL_PATH" ]; then
        print_error "$INSTALL_PATH is a directory; cannot install the wrapper there."
        exit 1
    fi
    if [ -f "$INSTALL_PATH" ] && [ ! -L "$INSTALL_PATH" ]; then
        cp -p "$INSTALL_PATH" "$INSTALL_PATH.bak"
    fi
    support_dir="$LOCAL_BIN/codex-switcher-support"
    mkdir -p "$support_dir"
    for support_file in codex-session-paths.sh install.sh VERSION; do
        if [ -f "$support_dir/$support_file" ]; then
            cp -p "$support_dir/$support_file" "$support_dir/$support_file.bak"
        fi
        cp "$script_dir/$support_file" "$support_dir/.$support_file.tmp.$$"
        chmod 644 "$support_dir/.$support_file.tmp.$$"
        mv -f "$support_dir/.$support_file.tmp.$$" "$support_dir/$support_file"
    done

    install_temp="$LOCAL_BIN/.codex-cli.tmp.$$"
    trap 'rm -f "$install_temp"' EXIT HUP INT TERM
    cp "$wrapper_source" "$install_temp"
    chmod +x "$install_temp"
    mv -f "$install_temp" "$INSTALL_PATH"
    trap - EXIT HUP INT TERM
}

main() {
    local bash_path git_path codex_path codex_version setup_only=0

    case "${1-}" in
        --wrapper-only)
            if [ "$#" -ne 1 ]; then
                print_error "--wrapper-only does not accept additional arguments."
                exit 2
            fi
            install_wrapper
            printf 'Updated wrapper and session path helper. Profiles and running processes were not changed.\n'
            return 0
            ;;
        --setup-only)
            [ "$#" -eq 1 ] || { print_error "--setup-only does not accept additional arguments."; return 2; }
            setup_only=1
            ;;
        "") ;;
        *) print_error "Usage: bash install.sh [--wrapper-only|--setup-only]"; exit 2 ;;
    esac

    if [ "$(uname -s)" != "Darwin" ]; then
        print_error "This installer supports macOS only."
        exit 1
    fi

    require_command bash
    bash_path=$(command -v bash)
    printf 'Bash: %s\n' "$bash_path"
    printf 'Bash version: %s\n' "$BASH_VERSION"
    if [ "${BASH_VERSINFO[0]}" -lt 3 ]; then
        print_error "Bash 3.0 or newer is required."
        exit 1
    fi

    require_command git
    git_path=$(command -v git)
    printf 'Git: %s\n' "$git_path"
    git --version

    require_command codex "Install Codex CLI using an official OpenAI installation method, then run this installer again. Homebrew is not required."
    require_command sqlite3
    require_command lsof
    codex_path=$(command -v codex)
    printf 'Codex: %s\n' "$codex_path"
    printf '\n'

    migrate_legacy_home
    mkdir -p "$PERSONAL_HOME" "$WORK_HOME"
    ensure_global_link
    . "$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/codex-session-paths.sh"
    repair_session_paths "$PERSONAL_HOME" "$LEGACY_HOME"
    repair_session_paths "$WORK_HOME" "$LEGACY_HOME"
    configure_file_credentials "$PERSONAL_HOME"
    configure_file_credentials "$WORK_HOME"

    if [ "$setup_only" -eq 1 ]; then
        printf '\nSetup complete. Profiles use separate file-based credentials.\n'
        printf 'Run codex-cli from a project to select its account and log in.\n'
        return 0
    fi

    if codex_version=$(env CODEX_HOME="$PERSONAL_HOME" codex --version 2>/dev/null); then
        printf 'Codex version: %s\n\n' "$codex_version"
    else
        printf 'Codex version: unavailable\n\n'
    fi

    install_wrapper

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) ;;
        *)
            ensure_path_line "$HOME/.bash_profile"
            ensure_path_line "$HOME/.bashrc"
            ;;
    esac

    export PATH="$LOCAL_BIN:$PATH"
    if [ "$(command -v codex-cli)" != "$INSTALL_PATH" ]; then
        print_error "Validation failed: codex-cli does not resolve to $INSTALL_PATH"
        exit 1
    fi
    if [ ! -x "$INSTALL_PATH" ]; then
        print_error "Validation failed: $INSTALL_PATH is not executable."
        exit 1
    fi

    printf '\nInstallation complete.\n\n'
    printf 'Wrapper:\n  %s\n\n' "$INSTALL_PATH"
    printf 'Personal Codex home:\n  %s\n\n' "$PERSONAL_HOME"
    printf 'Work Codex home:\n  %s\n\n' "$WORK_HOME"
    printf 'Global Codex account:\n  %s (%s -> %s)\n\n' "$GLOBAL_ACCOUNT" "$LEGACY_HOME" "$(readlink "$LEGACY_HOME")"
    printf 'Run:\n  codex-cli\n  codex-cli --global-switch\n'
    if [ "$PATH_CHANGED" -eq 1 ]; then
        printf '\nPATH was added to ~/.bash_profile and ~/.bashrc.\n'
        printf 'Open a new terminal, or run:\n  source "$HOME/.bash_profile"\n'
    fi
}

main "$@"
