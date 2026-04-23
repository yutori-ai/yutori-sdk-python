#!/usr/bin/env bash

set -euo pipefail

TTY="/dev/tty"

# For scripted uninstalls (CI, configuration management), set
# YUTORI_UNINSTALL_ASSUME_YES=1 to accept all defaults without a TTY.
ASSUME_YES="${YUTORI_UNINSTALL_ASSUME_YES:-0}"

# `[[ -r /dev/tty ]]` passes on non-interactive docker/CI where the device
# exists with permissive mode bits but has no controlling terminal. The
# subsequent `read <"$TTY"` then fails silently (via `|| reply=""`), the
# default_answer is honored, and destructive prompts auto-accept — removing
# the CLI and ~/.yutori without actual user confirmation. Probe by opening
# fd 3 on /dev/tty and closing it.
has_usable_tty() {
    { exec 3<"$TTY"; } 2>/dev/null || return 1
    exec 3<&-
    return 0
}

prompt_confirm() {
    local prompt="$1"
    local default_answer="$2"
    local reply

    if [[ "$ASSUME_YES" == "1" ]]; then
        # Honor the default when asked to skip interaction.
        [[ "$default_answer" == "Y" ]]
        return
    fi

    if ! has_usable_tty; then
        printf '%s\n' "No interactive terminal available. Re-run with YUTORI_UNINSTALL_ASSUME_YES=1 to accept defaults." >&2
        exit 1
    fi

    if [[ "$default_answer" == "Y" ]]; then
        printf '%s [Y/n]: ' "$prompt" >"$TTY"
    else
        printf '%s [y/N]: ' "$prompt" >"$TTY"
    fi

    read -r reply <"$TTY" || reply=""
    if [[ -z "$reply" ]]; then
        reply="$default_answer"
    fi

    [[ "$reply" =~ ^[Yy]$ ]]
}

uv_bin() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi

    if [[ -x "${HOME}/.local/bin/uv" ]]; then
        printf '%s\n' "${HOME}/.local/bin/uv"
        return 0
    fi

    if [[ -x "${HOME}/.cargo/bin/uv" ]]; then
        printf '%s\n' "${HOME}/.cargo/bin/uv"
        return 0
    fi

    return 1
}

main() {
    local exit_code=0
    local remove_cli=0
    local cli_installed=0
    local cli_state="not installed"
    local cli_summary="left in place"
    local cli_binary_path=""
    local credentials_state="not found"
    local tool_list_output=""
    local uninstall_output=""
    local uv_path=""

    printf 'Yutori uninstaller\n'
    printf '==================\n\n'

    # `uv tool list` output format is: "yutori v<version>" followed by bin
    # entries. The `^yutori v` anchor depends on that format — if uv ever
    # changes it, this branch silently reports "not installed".
    if uv_path="$(uv_bin 2>/dev/null)" && tool_list_output="$("$uv_path" tool list 2>/dev/null)"; then
        if printf '%s\n' "$tool_list_output" | grep -q '^yutori v'; then
            cli_installed=1
            cli_state="installed via uv"
        fi
    fi

    if command -v yutori >/dev/null 2>&1; then
        cli_binary_path="$(command -v yutori)"
    elif [[ -x "${HOME}/.local/bin/yutori" ]]; then
        cli_binary_path="${HOME}/.local/bin/yutori"
    fi

    if (( ! cli_installed )) && [[ -n "$cli_binary_path" ]]; then
        cli_state="binary present at ${cli_binary_path} (uv metadata unavailable)"
    fi

    if [[ -f "${HOME}/.yutori/config.json" ]]; then
        credentials_state="saved at ${HOME}/.yutori/config.json"
    fi

    printf 'CLI: %s\n' "$cli_state"
    printf 'Credentials: %s\n\n' "$credentials_state"

    if prompt_confirm "Remove the Yutori CLI?" "Y"; then
        remove_cli=1
    fi

    if (( remove_cli )); then
        if (( ! cli_installed )); then
            if [[ -n "$cli_binary_path" ]]; then
                printf 'A yutori binary is still present at %s, but uv metadata is unavailable.\n' "$cli_binary_path" >&2
                printf 'Remove that binary manually if you want to fully remove the global CLI.\n' >&2
                cli_summary="manual cleanup required"
                exit_code=1
            else
                printf 'Yutori CLI is already absent.\n'
                cli_summary="already absent"
            fi
        elif [[ -z "$uv_path" ]]; then
            printf 'uv is not available, so the CLI could not be removed automatically.\n' >&2
            cli_summary="manual cleanup required"
            exit_code=1
        elif uninstall_output="$("$uv_path" tool uninstall yutori 2>&1)"; then
            printf '%s\n' "$uninstall_output"
            cli_summary="removed"
        elif printf '%s\n' "$uninstall_output" | grep -q '`yutori` is not installed'; then
            # Fragile: depends on uv's current "not installed" message.
            # uv_tool_list should usually catch this first — this is fallback.
            printf 'Yutori CLI is already absent.\n'
            cli_summary="already absent"
        else
            printf '%s\n' "$uninstall_output" >&2
            cli_summary="manual cleanup required"
            exit_code=1
        fi
    fi

    printf '\nSummary\n'
    # Use `printf '%s\n' "$arg"` so the leading `--` can't be mistaken for a
    # printf option flag. `printf -------\n` errors with "invalid option".
    printf '%s\n' "-------"
    printf 'CLI: %s\n' "$cli_summary"
    # Never remove ~/.yutori — it may hold credentials the user wants to
    # keep across reinstalls (or other files we don't own). Point them at
    # the path; they can `rm -rf` manually if they want a clean slate.
    if [[ -d "${HOME}/.yutori" ]]; then
        printf 'Saved credentials at %s/.yutori were left in place. Remove manually with `rm -rf ~/.yutori` if you want to clear them.\n' "$HOME"
    fi
    printf 'Project SDK installs were not modified. Remove those with uv remove yutori or pip uninstall yutori inside each project.\n'
    exit "$exit_code"
}

main "$@"
