#!/usr/bin/env bash

# Sourced by the wrapper and installer. Uses macOS system SQLite and lsof.
session_database_users() {
    lsof -t "$1" "$1-wal" "$1-shm" 2>/dev/null | sort -nu || true
}

read_session_paths() {
    local db=$1 rows=$2 uri
    if sqlite3 -readonly "$db" "SELECT quote(id), rollout_path FROM threads;" -separator $'\t' > "$rows" 2>/dev/null; then
        return 0
    fi
    # Some migrated WAL databases cannot be opened read-only without their
    # sidecars. Immutable reads must never ignore a live WAL or active writer.
    command -v lsof >/dev/null 2>&1 || return 1
    [ ! -e "$db-wal" ] && [ ! -e "$db-shm" ] || return 1
    [ -z "$(session_database_users "$db")" ] || return 1
    uri=${db//%/%25}
    uri=${uri//\?/%3F}
    uri=${uri//#/%23}
    if ! sqlite3 "file:$uri?mode=ro&immutable=1" "SELECT quote(id), rollout_path FROM threads;" -separator $'\t' > "$rows" 2>/dev/null; then
        return 1
    fi
    [ ! -e "$db-wal" ] && [ ! -e "$db-shm" ] || return 1
    [ -z "$(session_database_users "$db")" ]
}

# A process hosting this invocation must be closed manually; killing it would
# also interrupt the migration. Fail closed if ancestry cannot be inspected.
session_process_is_ancestor() {
    local target=$1 current=$$ parent
    while [ "$current" -gt 1 ]; do
        [ "$current" != "$target" ] || return 0
        parent=$(ps -p "$current" -o ppid= 2>/dev/null | tr -d '[:space:]')
        case "$parent" in
            ''|*[!0-9]*) return 0 ;;
        esac
        [ "$parent" != "$current" ] || return 0
        current=$parent
    done
    return 1
}

close_session_database_users() {
    local db=$1 users=$2 answer pid identity current_identity remaining waited=0
    local identities=()
    printf 'Session paths need migration, but %s is in use:\n' "$db" >&2
    for pid in $users; do
        case "$pid" in ''|*[!0-9]*) return 1 ;; esac
        identities[$pid]=$(ps -p "$pid" -o lstart= -o comm= 2>/dev/null) || identities[$pid]=
        identity=$(ps -p "$pid" -o comm= 2>/dev/null) || identity='unknown process'
        printf '  PID %s: %s\n' "$pid" "$identity" >&2
    done
    printf '\n1) Terminate these processes (active sessions will stop)\n2) Close them manually, then retry\nChoose [1/2, default 2]: ' >&2
    if ! IFS= read -r answer; then answer=2; fi
    case "$answer" in
        1)
            # Check every target before sending any signals.
            for pid in $users; do
                if session_process_is_ancestor "$pid"; then
                    print_error "PID $pid may host this invocation. Close it manually and retry from a separate terminal."
                    return 1
                fi
            done
            for pid in $users; do
                identity=${identities[$pid]}
                [ -n "$identity" ] || continue
                remaining=$(session_database_users "$db")
                if ! printf '%s\n' "$remaining" | grep -F -x -q "$pid"; then continue; fi
                current_identity=$(ps -p "$pid" -o lstart= -o comm= 2>/dev/null) || continue
                [ "$identity" = "$current_identity" ] || continue
                kill -TERM "$pid" 2>/dev/null || true
            done
            while [ "$waited" -lt 5 ]; do
                remaining=$(session_database_users "$db")
                [ -n "$remaining" ] || return 0
                sleep 1
                waited=$((waited + 1))
            done
            print_error "The database is still in use. Close the remaining processes manually and retry."
            return 1
            ;;
        *)
            printf 'Close the listed processes and rerun the same command. No session paths were changed in this database.\n' >&2
            return 1
            ;;
    esac
}

repair_session_paths() {
    local profile_home=$1 legacy_home=$2 db rows sql id old_path suffix new_path escaped backup changed backup_arg users
    for db in "$profile_home"/state_*.sqlite; do
        [ -f "$db" ] || continue
        rows=$(mktemp "${TMPDIR:-/tmp}/codex-path-rows.XXXXXX")
        sql=$(mktemp "${TMPDIR:-/tmp}/codex-path-sql.XXXXXX")
        if ! read_session_paths "$db" "$rows"; then
            rm -f "$rows" "$sql"
            printf 'Warning: Cannot inspect session paths in %s. Skipping automatic repair for this database.\n' "$db" >&2
            continue
        fi
        changed=0
        printf 'BEGIN IMMEDIATE;\n' > "$sql"
        while IFS=$'\t' read -r id old_path; do
            case "$old_path" in
                "$legacy_home"/sessions/*|"$legacy_home"/archived_sessions/*) ;;
                *) continue ;;
            esac
            suffix=${old_path#"$legacy_home"/}
            case "/$suffix/" in */../*|*/./*) continue ;; esac
            case "$old_path$profile_home" in *$'\t'*|*$'\n'*) continue ;; esac
            new_path="$profile_home/$suffix"
            [ -f "$new_path" ] || continue
            escaped=${new_path//\'/\'\'}
            old_path=${old_path//\'/\'\'}
            printf "UPDATE threads SET rollout_path='%s' WHERE id=%s AND rollout_path='%s';\n" "$escaped" "$id" "$old_path" >> "$sql"
            changed=$((changed + 1))
        done < "$rows"
        rm -f "$rows"
        if [ "$changed" -eq 0 ]; then
            rm -f "$sql"
            continue
        fi
        if ! command -v lsof >/dev/null 2>&1; then
            rm -f "$sql"
            print_error "lsof is required to check whether $db is in use."
            return 1
        fi
        users=$(session_database_users "$db")
        if [ -n "$users" ]; then
            if ! close_session_database_users "$db" "$users"; then
                rm -f "$sql"
                return 1
            fi
            # Processes may have flushed newer paths while shutting down.
            # Rebuild the migration plan from the current database.
            rm -f "$sql"
            repair_session_paths "$profile_home" "$legacy_home"
            return $?
        fi
        backup=$(mktemp "$db.before-path-migration.XXXXXX")
        chmod 600 "$backup"
        backup_arg=${backup//\\/\\\\}
        backup_arg=${backup_arg//\"/\\\"}
        if ! sqlite3 "$db" ".backup \"$backup_arg\""; then
            rm -f "$sql"
            print_error "Cannot back up $db; session paths were not changed."
            return 1
        fi
        printf 'COMMIT;\n' >> "$sql"
        if ! sqlite3 -bail "$db" < "$sql"; then
            rm -f "$sql"
            print_error "Session path migration failed. Backup: $backup"
            return 1
        fi
        rm -f "$sql"
        printf 'Migrated %s session paths. Backup: %s\n' "$changed" "$backup" >&2
    done
}
