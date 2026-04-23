#!/usr/bin/env bash

set -euo pipefail

YUTORI_BRAND_MINT=$'\033[38;2;29;205;152m'
YUTORI_MINT_HIGHLIGHT=$'\033[38;2;90;232;189m'
YUTORI_SLATE_TEXT=$'\033[38;2;148;163;184m'
YUTORI_ERROR_RED=$'\033[38;2;255;92;92m'
YUTORI_RESET=$'\033[0m'

# Read the banner into a variable via `read -d ''` rather than `$(cat <<EOF)`.
# Bash 3.2 (macOS /bin/bash) has a known parser bug where a heredoc nested
# in `$(...)` mishandles literal apostrophes in the heredoc body — and the
# banner ASCII contains one (`'__| |`). `read -d ''` reads until EOF with
# no command substitution wrapper, which parses cleanly on bash 3.2+.
IFS= read -r -d '' YUTORI_BANNER <<'__YUTORI_BANNER__' || true
__   __      _             _
\ \ / /_   _| |_ ___  _ __(_)
 \ V /| | | | __/ _ \| '__| |
  | | | |_| | || (_) | |  | |
  |_|  \__,_|\__\___/|_|  |_|
__YUTORI_BANNER__
# `read -d ''` preserves the trailing newline that heredoc adds; `$(cat <<EOF)`
# would have stripped it. Strip here so banner_lines and render_banner match
# the layout the rest of main() assumes.
YUTORI_BANNER="${YUTORI_BANNER%$'\n'}"

INSTALL_LOG=""
INSTALL_STATUS_FILE=""
FRAMES_CACHE_FILE=""
FRAMES_RENDER_DIR=""
UV_INSTALL_PID=""
UV_BIN=""

is_interactive_terminal() {
    [[ -t 1 ]] && [[ "${TERM:-}" != "dumb" ]]
}

supports_truecolor() {
    if [[ "${COLORTERM:-}" == "truecolor" || "${COLORTERM:-}" == "24bit" ]]; then
        return 0
    fi

    if command -v tput >/dev/null 2>&1; then
        local colors
        colors="$(tput colors 2>/dev/null || printf '0')"
        if [[ "$colors" =~ ^[0-9]+$ ]] && (( colors >= 256 )); then
            return 0
        fi
    fi

    return 1
}

note() {
    printf '%b%s%b\n' "$YUTORI_SLATE_TEXT" "$1" "$YUTORI_RESET"
}

# `[[ -r /dev/tty ]]` / `[[ -w /dev/tty ]]` only check permission bits —
# they return true under non-interactive docker and CI runs where the device
# exists but has no controlling terminal, causing later redirects to fail.
# Probe by actually opening fd 3 on /dev/tty and closing it.
has_usable_tty() {
    { exec 3</dev/tty; } 2>/dev/null || return 1
    exec 3<&-
    return 0
}

error() {
    printf '%b%s%b\n' "$YUTORI_ERROR_RED" "$1" "$YUTORI_RESET" >&2
}

cleanup_temp_files() {
    # Each `[[ ... ]] && rm` evaluates to exit-code 1 when the guard is false
    # (e.g. in static / off animation modes where FRAMES_RENDER_DIR is never
    # set). Under `set -e` that would abort the caller — so we explicitly
    # return 0 regardless of which paths existed.
    [[ -n "$INSTALL_LOG" && -f "$INSTALL_LOG" ]] && rm -f "$INSTALL_LOG"
    [[ -n "$INSTALL_STATUS_FILE" && -f "$INSTALL_STATUS_FILE" ]] && rm -f "$INSTALL_STATUS_FILE"
    [[ -n "$FRAMES_CACHE_FILE" && -f "$FRAMES_CACHE_FILE" ]] && rm -f "$FRAMES_CACHE_FILE"
    [[ -n "$FRAMES_RENDER_DIR" && -d "$FRAMES_RENDER_DIR" ]] && rm -rf "$FRAMES_RENDER_DIR"
    return 0
}

kill_uv_process_group() {
    # Kill the whole process group of the backgrounded ensure_uv_and_install —
    # SIGTERM to just $UV_INSTALL_PID would leave curl/uv children running
    # and leaking disk I/O after we've exited.
    [[ -z "$UV_INSTALL_PID" ]] && return 0
    if kill -0 "$UV_INSTALL_PID" 2>/dev/null; then
        kill -TERM -- "-$UV_INSTALL_PID" 2>/dev/null || kill "$UV_INSTALL_PID" 2>/dev/null || true
    fi
}

cleanup() {
    # Only touch /dev/tty when it's actually usable. Under non-interactive
    # docker/CI the device can exist with permissive mode bits but no
    # controlling terminal, so `[[ -w /dev/tty ]]` passes but the redirect
    # still fails — leaking bash diagnostics to stderr.
    if has_usable_tty; then
        printf '\033[0m\033[?25h' >/dev/tty 2>/dev/null || true
    fi
    kill_uv_process_group
    cleanup_temp_files
}

on_interrupt() {
    cleanup
    printf '\n'
    error "Installation aborted."
    exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

assert_supported_platform() {
    case "$(uname -s)" in
        Darwin|Linux)
            ;;
        *)
            error "Yutori install.sh currently supports macOS and Linux."
            error "Windows bootstrap support is out of scope for v1."
            exit 1
            ;;
    esac
}

render_banner() {
    if is_interactive_terminal && supports_truecolor; then
        printf '%b%s%b\n' "$YUTORI_BRAND_MINT" "$YUTORI_BANNER" "$YUTORI_RESET"
    else
        printf '%s\n' "$YUTORI_BANNER"
    fi
    printf '\n'
}

render_bootstrap_intro() {
    local mode_line="$1"
    local use_color="${2:-0}"
    # Gate ANSI codes on use_color so dumb terminals (TERM=dumb, no truecolor,
    # redirected stdout) don't render raw escape bytes. Matches render_static_logo.
    if (( use_color )); then
        printf '%b> Yutori installer%b\n' "$YUTORI_MINT_HIGHLIGHT" "$YUTORI_RESET"
        printf '%b| %s%b\n' "$YUTORI_SLATE_TEXT" "$mode_line" "$YUTORI_RESET"
        printf '%b| Installing Yutori CLI with uv...%b\n\n' "$YUTORI_SLATE_TEXT" "$YUTORI_RESET"
    else
        printf '> Yutori installer\n'
        printf '| %s\n' "$mode_line"
        printf '| Installing Yutori CLI with uv...\n\n'
    fi
    # Flag the intro as shown so the Python UI (install_ui.py) suppresses its
    # duplicate header. Set here — at the exact site of the render — rather
    # than at handoff time, so a future code path that skips the intro can't
    # accidentally suppress the Python header too.
    export YUTORI_INSTALLER_BOOTSTRAP_SHOWN="1"
}

resolve_uv_bin() {
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

install_uv() {
    if ! command -v curl >/dev/null 2>&1; then
        error "curl is required to bootstrap uv."
        return 1
    fi

    curl -LsSf https://astral.sh/uv/install.sh | sh
}

ensure_uv() {
    # Resolve or bootstrap uv. Sets UV_BIN in the parent shell — callers rely
    # on a single consistent uv binary across install + handoff. If we
    # bundled this into the backgrounded install job (via `(...) &`), the
    # UV_BIN assignment would be lost to the subshell and handoff_to_python_ui
    # would have to re-resolve, potentially picking a different binary.
    local current_uv
    if ! current_uv="$(resolve_uv_bin)"; then
        note "Installing uv..."
        if ! install_uv; then
            error "Failed to install uv."
            return 1
        fi
        if ! current_uv="$(resolve_uv_bin)"; then
            error "uv bootstrap completed but uv binary was not found afterwards."
            return 1
        fi
    fi
    UV_BIN="$current_uv"
}

start_install_job() {
    # UV_BIN is read from the parent-shell assignment set by ensure_uv —
    # subshells inherit parent vars for reads, they just can't write back.
    (
        set +e
        "$UV_BIN" tool install --force --upgrade yutori >"$INSTALL_LOG" 2>&1
        status=$?
        printf '%s\n' "$status" >"$INSTALL_STATUS_FILE"
        exit "$status"
    ) &
    UV_INSTALL_PID="$!"
}

write_frames_cache() {
    FRAMES_CACHE_FILE="$(mktemp "${TMPDIR:-/tmp}/yutori-install-frames.XXXXXX")"
    cat >"$FRAMES_CACHE_FILE" <<'__YUTORI_AGENT_FRAMES_CACHE__'
=== FRAME 0 ===
           :::uuu:::
         :uuu  :  uuuu
       uuu   :u u   :uuu
      uu :u uuu uuu u: uu
     uu uu uuuu uuu::uu uu
    uu:uuu:uuuu uuuu:uuu::u
   u::uuu uuuuu uuuuu uuu::u
  uu uuuu:uuu:: :uuuu:uuuu uu
  u uuuu: ::::u :::::  uuuu u
 :u:uu:  uuuuuu uuuuuu  :uu:uu
 u u::uu:uuuuuu uuuuuu:uu::u:u
 u  uuu:uuuuuuu uuuuuuu:uu:  u:
:: :uuu:uuuuuuu:uuuuuuu:uuu: :u
:::uuuu uuuuuuuuuuuuuuu uuuu::u
::uuuuu uuuuuuu:uuuuuuu uuuuu u
u           uu   uu           u
u uuuuu uuuuuuu :uuuuuu uuuuu u
:::uuuu uuuuuuu uuuuuuu uuuu: u
uu uuuu uuuuuuu uuuuuuu:uuuu :u
 u  uuu:uuuuu     uuuuu:uuu  u:
 u u uuuuuu         uuuuuu u u
 :::u: uu             uu::uu:u
  u uuuu:             :uuuu u
  u::uuu               uuu::u
   u uu     u     u     uu u:
    uuu    :u     uu    uuu:
    :uu                 uu:
     :u:                u:
       uu:            uu:
         uu:      ::uu:
          ::uuuuuuu::
=== FRAME 1 ===
           :::uuu:::
         :uuu  :  uuuu
       uuu   :u u   :uuu
      uu :u uuu uuu u: uu
     uu uu uuuu uuu::uu :u
    u::uuu:uuuu uuuu:uuu::u
   u::uuu uuuuu uuuuu uuu::u
  uu uuuu:uuu:: :uuuu:uuuu:u:
  u uuuu: ::::u ::::: :uuuu u
 :::uu:  uuuuuu uuuuuu  :uu:u:
 u u::uu:uuuuuu uuuuuu:uu::u u
 u  :uu:uuuuuuu uuuuuuu:uu:  u:
:: uuuu uuuuuuu:uuuuuuu:uuuu :u
u::uuuu uuuuuuuuuuuuuuu uuuu: u
u uuuuu uuuuuuu:uuuuuuu uuuuu u
u           uu   uu           u
u uuuuu uuuuuuu :uuuuuu uuuuu u
u :uuuu uuuuuuu uuuuuuu uuuuu u
:u uuuu uuuuuuu uuuuuuu uuuu :u
::  uuu:uuuuu     uuuuu:uuu  ::
 u u uuuuuu         uuuuuu : u
 ::uu: uu             uu::uu:u
  u uuuuu             :uuuu u
  u::uuu               uuu::u
   u uu     u     u     uu u:
    uuu    :u     uu    uuuu
    :uu                 uu:
     :u:                u:
      :uu:            uu:
         uu:      ::uu:
          ::uuuuuuu::
=== FRAME 2 ===
           :::uuu:::
         :uuu  :  uuuu
       uuu   :u u   :uuu
      uu :u uuu uuu u: uu
     uu uu uuuu uuu::uu :u
    uu:uuu:uuuu uuuu:uuu::u
   u::uuu uuuuu uuuuu uuu::u
  uu uuuu:uuuu: :uuuu:uuuu:uu
  u uuuu: ::::u ::::: :uuuu u
 :u:uu:  uuuuuu uuuuuu  :uu:u:
 u u::uu:uuuuuu uuuuuu:uu::u u
 u  :uu:uuuuuuu uuuuuuu:uu:  u:
:: uuuu:uuuuuuu:uuuuuuu:uuuu :u
:::uuuu uuuuuuuuuuuuuuu uuuu: u
u uuuuu uuuuuuu:uuuuuuu uuuuu u
u           uu   uu           u
u uuuuu uuuuuuu :uuuuuu uuuuu u
u::uuuu uuuuuuu uuuuuuu uuuuu u
:u uuuu uuuuuuu uuuuuuu uuuu :u
:u  uuu:uuuuu     :uuuu:uuu  ::
 u u uuuuuu         uuuuuu : u
 ::uu: uu             uu::uu:u
  u uuuu:             :uuuu u
  u::uuu               uuu::u
   u uu     u     u     uu u:
    uuu    :u     uu    uuuu
    :uu                 uu:
     :u:                u:
      :uu:            uu:
         uu:      ::uu:
          ::uuuuuuu::
=== FRAME 3 ===
           :::uuu:::
         :uuu::  :uuu:
       :uu:  :u u:  :uuu
      uu :u uuu uu: u: uu
     u: uu uuuu uuu::uu uu
    u::uu::uuuu uuuu uuu::u
   u::uuu uuuuu uuuuu uuu uu
  uu uuuu:uuuu: uuuuu uuuu u:
  u uuuu  ::::u :::::  uuuu u
 uu:uu: :uuuuuu uuuuuu  :uu:u:
 u u: uu:uuuuuu uuuuuu uu::u:u
 u: :uu:uuuuuuu uuuuuuuuuu:: u:
uu :uuu:uuuuuuu:uuuuuuuuuuu: u:
u: uuuu uuuuuuuuuuuuuuu uuuu::u
u:uuuuu uuuuuuu:uuuuuuu uuuuu u
u:          uu   uu           u
u:uuuuu uuuuuu: :uuuuuu uuuuu u
u:uuuuu uuuuuuu uuuuuuu uuuu: u
u: uuuu:uuuuuuu uuuuuuu:uuuu :u
:u  uuu:uuuuu:  ::uuuuuuuuu  ::
 u u uuuuuu         uuuuuu u u
 u::u  uu:           :uu  uu:u
 :u:uuuu:             uuuuu u:
  u uuuu               uuu::u
   u uu    :u     u    :uu u:
   :uuu    uu    :uu    uuuu
    :u:                 uu:
     uu:                uu
      :uu:            uu
        :uu         uu:
          uuuuuuuuuu:
=== FRAME 4 ===
           :::uuu:::
         uuuu:  ::uuu:
       uuu   :u u   :uu:
      uu :: uuu uu: u: uu
    :uu:uu uuuu uuu:uuu uu
    u::uu:uuuuu uuuu uuu uu
   u: uuu uuuuu uuuuu:uuu uu
  :u:uuuuuuuuu: uuuuu uuuu u:
  u uuuu  ::::: u:::: :uuu::u
 uu:uu:  uuuuuu uuuuu:  :uu u:
 u u::uuuuuuuuu uuuuuu uu u::u
 u  :uu:uuuuuuu uuuuuu uuu:: u
:u uuuu:uuuuuuu:uuuuuu:uuuu  u:
u :uuuu:uuuuuuuuuuuuuu:uuuuu u:
u uuuuu uuuuuu::uuuuuu::uuuu:uu
u          :uu  :uu          uu
u uuuuu uuuuuu  uuuuuu::uuuu:u:
u uuuuu uuuuuuu uuuuuu:uuuuu:u:
:: uuuu:uuuuuuu uuuuuu:uuuu: u
:u  uuu:uuuu::  ::uuuu:uuuu  u
 u u:uuuuuu         uuuuuu ::u
 u:uu::uu:           :uu :u u:
  u uuuu:             uuuuu u:
  u:uuuu               uuu:u:
   u:uu:   :u     u    :uu u
   :uuu    uu    uuu    uuu
    :u:                 uu:
     :u                :u:
       u:            :uu
        :uu        :uu
          uuuuuuuuuu:
=== FRAME 5 ===
           :::uuu:::
         :uuu: :  uuuu
       :uu:   u u:  :uuu
      uu:::::uu uu: u: uu
     uu :u::uuu:uuu::uu uu
    uu:uuu:uuuu:uuuu uuu uu
   :u uuu:uuuuu:uuuuu:uuu:u:
   u:uuuu uuuuu :uuuu:uuuu u
  :::uuu: ::::u ::::: :uuuu:u
  u uu:  uuuuuu:uuuuuu  :uu u
 :u:u uu:uuuuuu:uuuuuu:uu :uuu
 u : uuu:uuuuuu:uuuuuu:uuu   u
 u :uuuuuuuuuuuuuuuuuuuuuuu: u
 u uuuu:uuuuuuuuuuuuuuuuuuuu u:
 u:uuuu:uuuuuuu:uuuuuuu:uuuu:::
 u          uu   uu          u:
 u:uuuu:uuuuuu: :uuuuuu:uuuu:::
 u uuuuuuuuuuuu:uuuuuuu:uuuu:u:
 u :uuu:uuuuuuu:uuuuuuuuuuuu u
 u  uuu::uuuu:: ::uuuu::uuu  u
 u:u :uuuuu         uuuuuu u:u
  u:u: uuu           uuu  u:u:
  u uuuuu             uuuuu u
  :u uuu               uuu:uu
   u::uu    u     u    :u::u
    uuu    :u:    u:    uuu:
     uu                 uu:
      u:               :u:
       uu:           :uu:
        :uu:        uu:
          :uuuuuuuuuu
=== FRAME 6 ===
           :::uuu:::
         :uuu:   :uuu
       :uu:  :u u   :uu:
      uu :u uuu uu::u :u:
     uu uuu:uuu uuu:uu::u:
    uu uuu:uuuu uuuu:uuu uu
    u uuu uuuuu uuuu:uuuu u:
   u uuuu uuuuu uuuuu:uuu:uu
  :u:uuu: ::::u u:::: uuuu u
  u uu:  uuuuuu uuuuu:  :uu:u
 :u:u uu uuuuuu uuuuuu uu u u
 u:  uuu:uuuuuu uuuuuu uuu  ::
 u  uuuuuuuuuuu:uuuuuu uuuu :u
 u uuuu:uuuuuuuuuuuuuu:uuuu: u
 u uuuu:uuuuuuu:uuuuuuuuuuuu u
 u          uu  :uu          u
 u:uuuu:uuuuuu: uuuuuu:uuuuu u
 u uuuu:uuuuuuu uuuuuu:uuuuu u
 u :uuu::uuuuuu uuuuuu uuuu  u
 u  uuuu:uuuu:: ::uuuu uuu: :u
 uuu uuuuuu        :uuuuu:: u:
  u u: uu:           uuu :u u
  u:uuuuu             uuuuuuu
   u:uuu               uuu u
   uu:u:    u     u    uu u:
    uuu    uu    :u    :uuu
     uu                :uu
      uu               uu
       uu            :uu
         uu         uu
          :uuuuuuuuu:
=== FRAME 7 ===
           ::uuuu:::
          uuu:   :uuu
        uu:  :u u   :uu:
      :u: u uuu uu :: :u:
     :u::uu:uuu uuu uu::u:
     u:uuu uuuu uuuu uuu:u
    u uuu:uuuuu uuuu:uuu::u
   uu:uuu uuuuu uuuuu:uuu:u:
   u uuu: ::::: u:::: uuuu u
  :uuu:  :uuuuu uuuuu: :uu:u:
  u u uu uuuuuu uuuuuuuu::u:u
 ::  uuu uuuuuu uuuuuu uu:: u
 uu uuuu:uuuuuu:uuuuuu uuuu u:
 u :uuuu:uuuuuuuuuuuuu uuuu ::
 u uuuuu:uuuuuu:uuuuuu uuuuu::
 u          uu: :uu         uu
 u uuuuu:uuuuu: uuuuuu uuuuu:u
 u uuuuuuuuuuuu uuuuuu uuuu:u:
 :  uuuu:uuuuuu uuuuuu uuuu u:
 :: :uuu:uuuu:: ::uuuu uuu  u:
  u ::uuuuu        :uuuuu u:u
  u uu uuu           uu: :u::
  :uuuuuu             uuuu u:
   u uuu              :uu::u
   :u uu    u    :u    uu u
    uuu:   :u:   :u    uuuu
     uu:               :u:
      uu               uu
       :u:           :u:
         uu        :uu
          :uuuuuuuuu:
=== FRAME 8 ===
            :uuuu::
          uuu:   :uuu
        uuu  :u u   :uu
       u: : :uu uu :: uu
      uu:uu:uuu uuu uu uu
     u:uuu:uuuu uuuu:uu:uu
    uu uu::uuuu uuuu:uuu ::
    u uuu:uuuuu uuuuuuuuu u
   u uuuu ::::u u:::  uuuu:u
   u:uu  :uuuuu uuuuu  :uu u
  u:u::u:uuuuuu uuuuu:uu uuu:
  u  :uu uuuuuu uuuuuu:uu ::u
  u :uuu uuuuuu:uuuuuu:uuu: u
  : uuuu uuuuuuuuuuuuu uuuu u
 :::uuuu:uuuuuu:uuuuuu uuuu u
 :u         uu  :uu         u
 :::uuuu:uuuuu: uuuuuu uuuu u
 :u:uuuu:uuuuuu uuuuuu uuuu u
  u uuuu uuuuuu uuuuuu uuuu u
  u  uuu uuuu:: ::uuuu:uuu :u
  u:: uuuuu:       :uuuuu uuu
  :::u :uu          :uu: u:u
   u:uuuu:           :uuuu u
   :::uu:             uuu:u
    u uu    u    ::    uu:u
    :uuu    u:   uu    uuu:
     :u:               uu:
      uu               u:
       :u:           :u:
        :uu:       :uu
           uuuuuuuuu
=== FRAME 9 ===
            ::uuu:::
          :uu: : :uuu
        :uu:  u u   uuu
       uu :u:uu:uu:u: uu
      :u uu:uuu:uuu:uu uu
     :u uu:uuuu:uuuu:uu u:
     u:uuu uuuu:uuuu uuu u
    uuuuuuuuuuu :uuuuuuuu:u
    u uuu  :::: ::::  uuu:u:
   ::uu:  uuuuu:uuuuu  :uu u
   u u uu:uuuuu:uuuuu:uu:u u
   u  uuu:uuuuu:uuuuuu:uu  u
  :u uuu:uuuuuu:uuuuuu:uuu ::
  :: uuu:uuuuuuuuuuuuu:uuuu:u
  : uuuu:uuuuuu:uuuuuu:uuuu u
  u         uuu  uu         u
  u uuuu:uuuuuu uuuuuu:uuuu u
  : uuuu:uuuuuu:uuuuuu:uuuu u
  :u uuu:uuuuuu:uuuuuuuuuu::u
   u :uuuuuuu     uuuu:uu: u:
   u ::uuuu:       :uuuu:::u:
   : uu uu           uu :u u
    u:uuu:           :uuuu:u
    u uuu             uuu:u
     u u:   u:    u   :u:u:
     :uu:   u:   :u    uuu
      uu               uu
       u:             :u
        uu:          uu
         :uu      :uu:
           :uuuuuuu:
=== FRAME 10 ===
            ::uu:::
          :uu    uuu:
        :uu   : :  :uu
       :u : :uu:u: u :u
      :: uu uuu:uuuuu:uu
     :u uu:uuuu:uuu uuu u
     u:uuu:uuuu:uuu::uu ::
    ::uuu:uuu:: uuuu uuu u
    u uuu  :::: :::: uuuuu:
    :uu: :uuuuu:uuuu: ::u:u
   u u :uuuuuuu:uuuuu:u:::u
   u  uuuuuuuuu:uuuuu uu::::
   u :uu:uuuuuuuuuuuu uuu:uu
   u uuu:uuuuuuuuuuuu uuuu u
   ::uuu:uuuuu::uuuuu:uuuu u
            uu  uu         u
    uuuu:uuuuu  uuuuu:uuuu:u
   ::uuu:uuuuuu:uuuuu:uuuu u
   u uuu:uuuuuu:uuuuu uuu: u
   :  uu:uuuu :  :uuu uuu ::
   u :uuuuu:       uuuuu :u:
   ::u: uu          uu: u:u
    u:uuu:           uuuu:u
    u uuu            :uu:u
     u:u:   u    ::   uu u
     uuu    u    u:   uuu
      uu              :u:
       u:             u:
        uu          :uu
         :u:       uu
           uuu:uuuu:
=== FRAME 11 ===
            ::uuu::
          :uu:   :uu:
         uu:  u :   u:
        u :u:uu:uu u:uu
       u :u:uuu:uu::u :u
      uu:uu:uuu:uuu:uu:::
      u:uu:uuuu:uuu:uuu u
     u uuu:u::: :::u:uuu:u
     ::uu: :::u :::: :uu:u
    u uu  :uuuu uuuu   u:::
    u u u:uuuuu uuuuu:u:u u
    u :uu uuuuu uuuuu:uu :u
    : uuu:uuuuu:uuuuu:uuu u:
   ::uuuu:uuuuuuuuuuu:uuu:u
   uuuuu::uuuu: uuuuu:uuu::
   u:       :u  uu
   u:uuu::uuuuu uuuuu:uuuu
   uuuuuu:uuuuu uuuuu:uuuu:
     :uuu uuuuu uuuuu:uuu u:
    : :uu uuu    :uuu:uu  u
    u::uu:uu       uuuu:: u
    u u :u:         :u :u :
    ::uuuu           uuu u
     u uu            :uu:u
     ::uu   ::   :    u:u
      uuu   uu   u:   uuu
       u:             :u
       :u             u
        :u:         :u
          :u       u:
           ::uu:uu::
=== FRAME 12 ===
            u:uu:::
          :uu    uu:
         uu  ::::  :u
        u: : uuuuu:: u:
       :: u uuuuuu:uu:u
      :u:uu:uuuuuuu:uu u
      u uu:uuuuuuuu:uu:::
     ::uuu uu:::::u:uuu:u
     u:uu: :::: :::  uu:u:
     u:u: uuuuu:uuuu  :u:u
    :uu u:uuuuuuuuuu:u:uuu
    : :uuuuuuuuuuuuu uu  u:
    u:uuuuuuuuuuuuuuuuuu u
    u:uuuuuuuuuuuuuuu:uuu::
    ::uuu:uuuuu:uuuuu:uuu::
    u       :u  uu       :u
    ::uuu:uuuu: uuuu::uuu:u
    u:uuu:uuuuu:uuuuu:uuu:u
    u uuuuuuuuuuuuuuuuuu::
    : :uu:uuu:   uuu:uu: u:
    uu::uuuu      :uuuu :u
     :u::u:        :u: u u
     u:uuu          :uuu:u
     :uuuu           uu:u
      : u   :    u   :u::
      uuu   uu   u:  :uu
       uu             u
        u            ::
         u:         u:
          uu      :u
           :uuuuuuu
=== FRAME 13 ===
            :uuuuu
           uu    uu:
         :u:  u::  uu
        :u u:uuuu : :u
        u uuuuuuuu:u:uu
       u:uu:uuuuuuuuu:u
      ::uuuuuuuuuuu:uu::
      u:uuuuu:::u:uuuuuu:
      uuuu :::u ::: uuu:u
     ::uu :uuuuuuuuu :u:u
     uu::uuuuuuuuuuuuu:uu:
     u :uuuuuuuuuuuuuuu u:
     u uuuuuuuuuuuuuuuu: u
     :uuu:uuuuuuuuuu:uuu u
     :uuu uuuuu:uuuu uuu:u
     u      :u  uu       u
     uuuu uuuuu uuuu uuu:u
     uuuu:uuuuuuuuuu uuu:u
     ::uuuuuuuuuuuuu:uuu u
     u :uuuuu    uuuuuu: :
     u: uuuu      uuuu:uuu
     u:u uu        uu uuu
     :uuuu          uuu:u
      uuuu          :uuu:
      ::u:  ::   u   u:u
       uuu  uu  :u   uu:
       :u            u:
        u:           u
         uu        :u
          :u:     uu
           :uuuuuu:
=== FRAME 14 ===
            :uuuuu
           uuu   uuu
          u:  u::  uu
         uu::uuuuuu:u:
        :::u:uu:uu:u:u
        u:uuuuuuuuuuu:u
       u:uu:uuu:uuuuuuuu
       uuuuuu:: ::u:uu:u
      ::uuu uuu uu: uuuu:
      uuu  uuuu:uuu: :u::
      u::uuuuuuuuuuuu::uu
      u:uuuuuuuuuuuuuuu u
      ::uuuuuuuuuuuuuuu u:
      uuuuuuuuuuuuuu:uuuu:
      uuuu:uuuu uuuu:uuu:
      u      uu uu      u
      uuuu:uuuu uuuu:uuu:
      uuuu:uuuuuuuuu:uuu::
      uuuuuuuuuuuuuuuuu:u:
      ::uuuuu:   uuuuu: u
      uu:uuu:     :uuu uu
      :uu:uu       uu u::
      ::uuu         uuuu:
       uuuu         uuuu
       ::u   u   :  :u::
       :uu   u  :u:  uu
        :u           u:
         u:         :u
          u:       :u
           u:     uu
            uuuuuu:
=== FRAME 15 ===
             u:uuu
           :uu   uu:
           u  ::  :u
          u uuuuu u:u
         u u:uuuu::uuu
        :uuuuuuuuu:uuu:
        ::uuuuuuuuuuu:u
       :uuu:u:::::uuu:u
       ::uu :::::: :uuuu
       ::u :uuuuuuu  uu:
       u::u:uuuuuuuuu uu
      :  uuuuuuuuuuuu: u
      ::uuuuuuuuuuu:uuuu:
      :uuuuuuuuuuuu:uuuu:
       uuu:uuuu:uuu:uuu:
      :u     uu u:     :
       uuu:uuuu uuu:uuu::
      :uuu:uuuuuuuu:uuuu:
      :uuuuuuuuuuuu:uuu::
      : :uuuuu   uuuuu:u
       u::uuu     uuu::u
       u::uu       u uuu
       :uuu:       :uu:u
       :uuu         uuu:
        uuu  :  :u  u:u
        uu:  u  :u  uu:
         uu         :u
         :u         u:
          u:       u:
           :u     u:
            :uu:uu:
=== FRAME 16 ===
             uuuu:
           :u:  :uu
           u  :u  uu
          uuuuuuuu:uu
         :uuuuuuuuuuu
         uuuuuuuuuuuuu
        ::uuuuuuuu:u:u
        :uuuu:: :uuuuu
        uuuu:uu:uu uu:u
        uu: uuuuuu: uuu
        uuu:uuuuuuuuu:u
       ::uuuuuuuuuuuu:u
       :uuuuuuuuuuuuu:::
       :uuuuuuuuuuuuuuuu
        uuuuuuu:uuu:uu::
        :    uu u:    u:
        uuuuuuu:uuu:uuu:
       :uuuuuuuuuuuuuu::
       :uuuuuuuuuuuuuuuu
       : uuuuu  :uuuu :
        :uuuu    :uu: u
        uu:u      uu:uu
        :uuu       uu::
        uuu:       uuu:
         :u: :: :  :uu
         uu  u: u: uuu
         :u         u:
          u        :
          :u      :u
           :u    :u
            :uuuuu
=== FRAME 17 ===
             :uuu
            uu  :u
           u: ::: u
          :uuuuuuuuu
          :uuuuuu:uu
          uu:uuuuuu:u
         ::uuuuuuuuuu
         :uu:::::uuuu:
         :uu:u::u::uuu
         u: uuuuuu ::u
         :::uuuuuuuuuu
        ::uuuuuuuuuu::
        uuuuuuuuuuuuu::
        uuuuuuuuuu:uu:u
        :uuuuuu:uu:uu:u
        :    u::u    :u
         uuuuuuuuu:uu:u
        :uuuuuuuuu:uu:u
        uuuuuuuuuuuuu:u
        : uuu:  :uuu::
         ::uu    uuu:u
        :u:u:     u::u
         :uu      uuu:
         uuu      :uuu
         :uu :: :: uu
          u: :  u: uu
          u        u
           :       u
           :      u
            u    ::
             uuuu:
=== FRAME 18 ===
              :u:
            ::  ::
            u  : :
           :u:uu:uu:
           :uuu:u:u:
           uuuuuuuuu
          :u:uu:u:u::
          uu ::::uu::
          :u::::: uuu
          uu u:uu: :
          :u:uuuuuuuu:
          ::uuuuuuuu:
         uuuuuuuuuuu:
         uuuuuuuuuuu :
         ::uu:uuu::u :
         u    uuu    u
         :uuuuuuuuuu::
         uuuuuuuuuuu :
         uuuuuuuuuuu:
         ::::u  :uuu:
           uuu   uu:u:
          u::    ::::
          :u:     uu:
          :u:     u:u
           :    : :u
          :u :u u  u:
           u      :u
           :      ::
            :    :u
             :   u
             uuuu
=== FRAME 19 ===
              ::
             :  :
            :::  :
            ::uuuuu
            u :::::
           :uuuu:u::
            ::::::u:
           :::: ::::
           :: ::: uu
           : :::: : :
           :uuuuu:::u
           :::::u:u::
          :u:uuuuuuu:
          uu:uuuu:u
          ::::::::u
          u   uu:   :
          ::::u:u:u:
          :u:uuuu:u
          :u:uuuuuuu:
           u::  ::u::
           :uu   uuuu
          ::u:   :  :
           u:    :::
           :u     uu
           u: u   :
           :u u u :u
            :     :
            :     :
            :    ::
                 :
              uu:
=== FRAME 20 ===
              u:

             ::: u
              :::::
            ::uu:::
            ::u::::
            : ::::
            :    u::
            : ::
            ::uuu :
            :::::: :
            u:uuu::u
            u:uuu:uu
           :u:uu:::u
            ::uu:  u
           :  :::  :
           :uuuuu::u
           :::::::::
            u:u:u:uu
            ::  :: :
            ::   : :
             u   u:
            :    ::
            :u   :::
             :  :
             : :::u
                 ::
             :    :
             :   :
                 :
              u:
=== FRAME 21 ===
              :::
                :
             :  ::

              :::u
              :u::
             ::u:
             :  ::
             : : :
             ::u::
             ::::
             ::u:
             :uu:::
            ::uu: :
             :u:  :
              :   :
            ::uu:::
             :::: :
             ::u:u:
             :  :
             :  u
             u   :
             :   :
             :   :
             ::
             uu :u
             u   :
             :   :
             :   :
              : :
              ::
=== FRAME 22 ===
               :
                :
                :
                :
               ::
               ::
              :
              : ::
                 :
              :: :
              :: :
              ::::
              :u::
              ::::
               :::
               : :
              ::::
              ::::
              ::::
               :::
                 :
              u  :
              :  :
              :  :
               u
               uu
                u
              : :
              u :
                :
               :
=== FRAME 23 ===
               :
               :
               :
               :
               :
               :

                :
                :
                :
                :
                :
                :
                :
                :
              :::
                :
                :
                :
                :
                :
               ::
               ::
               :

               :
               u
               u
               u
               :

=== FRAME 24 ===
               :
               :
               :
               :



               :
               :
               :
               :
               :
               :
               :
               :
               u
               u
               :
               u
               :
               :
               u
               :
               u
               :
               :
               :
               :
               :
               :
               :
=== FRAME 25 ===
               :
              :
              :
              :
              :
              :
              :
              :
              :
              ::
              ::
              ::
              ::
              ::
              :
              : :
              ::
              ::
              :u
              :
              ::
              ::
              : :
              u u
              : :
              u :
              : :
              u :
              :::
               :

=== FRAME 26 ===
              ::
              :
              :
             :
               :
             ::::
             :
             u   :
             :
             u u::
             : ::
             : u:
             : u:
               ::
             : :
             : :
             :::::
             : ::
             : u:
             u
             :  :
             u  u
             :   :
             u   :
             :  u:
             :::u:
             ::  :
                 :
              : :
              :
                :
=== FRAME 27 ===
              :::
             :
             :
               :
            :  ::
            : :u::
            :  :::
            ::   u
            :  ::
              :uu:
              :::
             :::u
            :::uu:
             ::uu::
            :  :u :
              :::
            :u:uu:u
             ::::::
            :u::u::
             :  :
                ::
            :   :
            :    u
            ::   :
            : :: :
            : :: u
            :    u
             :   :
             :
                :
              :::
=== FRAME 28 ===
              :::
             u
            :  : :
            :  :: :
            :: :u:
           ::u::u :
           : :::: :
           u::    u
              ::: :
           :: uuu:
           :  :::::
           :: u:uu:
           :u uuuu::
           :: :uu:::
           :: ::u:::
              :::
           :u uuuu:u
           :: ::::::
           uu u:uu::
           :: : :u:
           : :   ::
           ::    ::
           :     ::
           u:    ::
           :    : u
           :: : u u
            u     :
            :
             :   u

               ::
=== FRAME 29 ===
               ::
             ::  :
            :    ::
            :  :u :
           :   :: ::
           u::::u::
           :u u:u::
          : :     ::
          u:u : : ::
          :   :::: :
          u  :uuuu u
          : ::uuuu:u
           u::uu:::u:
         : :uuuuuu:uu
         : :::::u::::
         :    :u:   :
         : u:uu:u::u:
         : :::u::::uu
         : uuuu:uu:uu
            :::  :::
          u:u:   :u:
          u :    ::u
          : u     u:
          u:u     uu
          : : : : :
           uu u u u
           :      :
           ::     :
            :     :
             :
              :::
=== FRAME 30 ===
              :uu:
             u   u:
            u::   u
           u :: u:::
          ::::u::::u
          uuu u:uuu:
          u:uuu:uu::u
         u:u: :  :uu
         :uu: : :::u:
         u : :u:uu  :
         uu: uu:uuuu:
           u:uu:uuuu:
          :u:uuuuuuuuu
        :::uuuuuuuu:uu
         u:u:uuuu:: ::
        :u    u:u    :
         uuuuuu:uuu:u:
         uuuuuu:uuu:uu
        ::uuuuu:uuuuu:
           u:u:  uu::
         ::uu:   :uu:
         u  u     u ::
          :u:     :::
         u:u:     :uu
          :u :     :
          :u u: :: ::
          :u       u
           :       u
           :u     u
            ::   ::
             :uuu:
=== FRAME 31 ===
             :uuu:
            uu   :u
           :u   : u:
           u uu:uu:::
          ::u:u:uu:::
         :uuuuu:uuuuu:
         u:uuuu:uu:u::
        ::uu:u: ::uuu:
        :uuu :: ::::uu
        u u :uu:uuu u::
        uu uuuu:uuuuuuu
        ::uuuuu:uuuuu:
        u:uuuuuuuuu:uuu
        uuuuuuuuuuu:uuu
        uuuuuuuuuuu uuu
        u    :u u:    u
        uuuuuuu:uuu:uuu
        uuuuuuu:uuu:uuu
        uuuuuuu:uuu:uuu
        ::uuuu:  uu:u:
        uuuuu:    uuuu:
        u uuu     :u:u:
         :uu:      uuu
        :uuu       uu:
         u:u  : :: :uu
         uu:  u  u :u:
          u:       :u
          ::       u:
           u:      u
            u:    u
             uuuuu
=== FRAME 32 ===
             :uuuu
            uu   :u
          :u:  ::  u
         :u:u:u:uu: u
         :::uuu:uuuu :
         u uuuu:uuuu:u
        ::uu:uu:uuuuuu:
        u:uu::: ::uuuuu
       :uuuu :: ::::uuu
       :uu  uuu:uuu: u:
       uuu::uuu:uuuuu:uu
       u:uuuuuu:uuuuuu
       u uuuuuuuuuuuuu:u
       uuuuuuuuuuuuuuuuu
       ::uu:uuu:uuu::uuu
       u     :u u:     u
       uuuu:uuu:uuuu:uuu
       u:uuuuuu:uuuuuuuu
       u:uuuuuu:uuuuuuuu
       u::uuuu:  :uuuu:
       :u uuu     :uu:u:
       uuu u       uuuu:
        uuuu       uuuu
        uuu:       :uuu
        :uu  :  ::  uu:
         uu  u: :u  uu
         uu         uu
          :        ::
           u       u:
           :u:    u
             uuuuu:
=== FRAME 33 ===
             :uuuu:
            uu   :uu
          :u: : :  uu
          u uuuuuuu::u
         u::uuu:uuuu:::
        uuuu:uuuuuuuu u
        u:uu:uu:uuu:uu::
       :uuuuu:: ::::uu::
       u:uu: uu:::: uuuu
       u:u :uuu:uuuu  uu
       u::u uuuuuuuuuuu:u
      :u uuuuuu:uuuuuuuu:
      u:uuuuuuuuuuuu:uu::
      :uuuuuuuuuuuuu:uuuu
      ::uuu:uuu uuuu:uuuu
      u:     :u uu      u
      :uuuuuuuu :uuu:uuuu
      :uuuuuuuu:uuuu:uuuu
      u:uuuuuuuuuuuu:uu:u
      u::uuuuu   uuuuuuu:
      :u:uuuu     :uuuuuu
       uuu:u       :u uu:
       uuuuu        uuu:
       :uuu         uuuu
        u:u  :   :   uu:
        :uu  u:  u  :uu
         u:         :u:
         ::         ::
          :u       :u
           :u     :u
            :uuuuuu
=== FRAME 34 ===
            :uuuuu
           :u:   :uu
          uu  : u  :u
         u::::u:uu ::u
        :u:u:uu:uuuu::u
        uuuuuuu:uuuuuuu:
       uuuuuuuu:uuuuuu :
      :u:uuuu:: ::uuuuuu:
      uuuuu ::u:::u uuu:u
      u u: uuuu:uuuu  u:u
      uu:uuuuuu:uuuuuu uu
     u: uuuuuuu:uuuuuuu ::
     u::uu:uuuuuuuuuuuu:::
     ::uuuuuuuuuuuuuuuuu::
     ::uuu:uuuu:uuuu:uuu::
     :       uu uu       u
     ::uuu:uuuu uuuu:uuuuu
     :uuuuuuuuu:uuuu:uuu :
     u uuuuuuuu:uuuuuuuu::
     uu uuuuu:   :uuuuu:::
      uu uuu:     :uuu::::
      u:u u:       uu:uuu
      uuuuu         uuu::
       uuu:         :uu:
       ::u   u   u   uuu
       uuu   u   u:  uu
        uu           :u
         u           u
         :u:       :u:
           uu     :u
            :uuuuuu
=== FRAME 35 ===
            ::uuuuu
           uu:   :uu:
          u:  : u  :u:
        :u uuuu:uuuu:u:
       :u::uuuu:uuuuu u
      :u:uuuuuu:uuu:uuuu
       uuuuuuuu:uuuuuuuu:
      uuuuuuu:: :::u:uu u
     :u:uu: u:u:::u: uuuu:
     ::u: :uuuu:uuuu: uuuu
     u:::u:uuuu:uuuuuu::uu
     :::uu uuuu:uuuuuuuuu:
    uu uuuuuuuuuuuuuu:uuuu
    ::uuuuuuuuuuuuuuu:uuuu:
     :uuuuuuuuu uuuuu uuu::
     :       uu uu        :
     uuuuuuuuuu uuuuu uuu::
    ::uuuuuuuuu:uuuuu:uuuu:
    uuuuuuuuuuu:uuuuu:uu:u
     : uuuuuuu   :uuuuuu :
     u::uuuu       uuuu uu
     :uu::u:        uu uu:
     :uuuuu         :uuu::
      u uu           uu:u
      uuuu   :   u:  :u :
       uu:  uu   u:  uuu
        u             u:
        ::           :u
         uu         :u
           u:     :u:
            uuuuuu:
=== FRAME 36 ===
            :uuuu::
           uuu    uuu
         :u:  :::   uu
        uu:u::u:uu::::u
       :u uu uu::uu u: u
      :u uuuuuu:uuuuuuuuu
      u uuu:uuu:uuuu:uuuu
      u:uu:::::  : uuuuu:u
     uuuuu  ::::::::  uu:u
    :u:u   uuuu::uuuu  :u::
    u:u :uuuuuu:uuuuu u:u:u
    u: :uu:uuuu:uuuuuuuu: u
    :  uuuuuuuuuuuuuu:uuu u
   :u uuuuuuuuuuuuuuu:uuu::
    u uuu:uuuuu :uuuu::uuu:
   :u        u: uu:       u
   :u uuuuuuuuu :uuuuu:uuuu
    u:uuuuuuuuu:uuuuuu:uuu:
    u :uuuuuuuu:uuuuu:uuu::
    :  uuu:uu:    uuu:uuu:u
    uuu uuuu       :u:uu:::
     u:u :u          u: u::
     u:uuu:          :uuuu:
     uuuuu           uuu:u
      uuu:   u    :   u::
       uu   :u   uu   uuu
       :u              u
        u:            u:
         uu         :u
          :u:      :u
            :uuu:u::
=== FRAME 37 ===
            uuuuu::
          :uuu    uuu
         uu   u::   :u
        u: u:uu:uu u: u:
       u: uuuuu:uuu:uu u
      :::uu uuu:uuuu:uuuu
     :u uu::uuu:uuuu:uuuu:
     u uuu:u::: :::u:uuu:u
    :uuuuu ::::::::: :uuu :
    u:uu   uuuu:uuuuu  :u:u
    u:u:u:uuuuu:uuuuu:u::::
   ::: uu:uuuuu:uuuuu:uuu ::
   :u:uuu:uuuuuuuuuuu:uuu  :
   : uuuu uuuuuuuuuuu:uuuuuu
   u:uuuu:uuuuu :uuuu::uuu:u
   u         u: :u:        u
   u:uuuu:uuuuu :uuuu::uuu:u
   : uuuu:uuuuu:uuuuu:uuuu:u
   :: uuu:uuuuu:uuuuu:uuuu u
   u: :uu:uuu:    uuu:uuu  :
    u::uuuuu       :uuuu :u:
    u:u: u:         :uu ::u
    u::uuu           uuuu:u
     uuuu:            uu:::
     :::u    :    :   uu u
      uuu   uu   uu   :uu:
      :u:             :u:
       :u             :u
        :u:          u:
          :u       uu
           :uuuuuu::
=== FRAME 38 ===
            uuuuuu:
          :uu:    uuu
        :uu:  ::::  uu:
       :u::u uuuuu::: uu
       u :u::uu:uuu:uu ::
      u uuu:uuuuuuuu:uu u
     :::uu:uuuu:uuuu::uu:u
    :u uuu:u:u:::u:uu:uuuuu
    u:uuu: :::u:::::  uuu u
   :u u:  uuuuu:uuuuu   uu::
   u:u :u:uuuuuuuuuuuuuu ::u
   u: uuu:uuuuu:uuuuuu:uu  u
   u :uuu:uuuuuuuuuuuu uuu u
  :u:uuuu uuuuuuuuuuuu:uuuu::
  :uuuuuu uuuuu::uuuuuuuuuu :
   u        :uu  uu         u
   u:uuu: uuuuu :uuuuu:uuuu :
  :u:uuuu:uuuuu:uuuuuuuuuuu :
   u uuuu:uuuuu:uuuuuu:uuu u:
   u  uuu:uuu:   :uuuu uu: u
   u:u uuuuu       :uuuu:::u
   :::u :uu          uu :u :
    u uuu:            uuuuu:
    :uuuu:            uuu u
    :u uu   ::    u   :u u:
     :uu:   uu   uu    uuu
      :u               uu
       uu              u:
        uu:          :u:
         :uu       :u:
           :uuuuuuuu
=== FRAME 39 ===
           :uuuuuuu
          uuu:    uuu
        uuu   u ::  :uu
       uu :u uu:uu: : :u
      uu uu:uuu:uuu:uu:uu
     uu uuu:uuu:uuuu uu::u
    :u uuu uuuu:uuuu::uu u:
    u uuuu:uuu:::uuuu:uuu:u
   :u:uuu  :::u ::::  uuuu::
   u uu:  uuuuu:uuuuu: :uu:u
  :u:u uu:uuuuu:uuuuuu:u:::u:
  :u  uuu:uuuuu:uuuuuu:uu:  :
  u: uuuuuuuuuu:uuuuuu uuu  u
  u:uuuuuuuuuuuuuuuuuu uuuu u
  u uuuu:uuuuuu:uuuuuu:uuuu :
  u         uuu  uu         u
  u uuuu:uuuuuu :uuuuu:uuuu::
  u:uuuu:uuuuuu:uuuuuu:uuuu :
  u::uuuuuuuuuu:uuuuuu uuu: u
  u: uuu:uuuu:   :uuuu:uuu  u
  :u: :uuuu:       :uuuu: :u:
   u:u: uu:          uu::u:u
   u:uuuu:            uuuu:u
    u uuu             uuu u
    u::u:   :     u    uu u
     uuu    uu   :u:   uuu
      uu               :u:
      :u:              u:
        uu           :u
         :u:       :u:
           uuuuuuuuu
=== FRAME 40 ===
           :uuuuuuu:
         :uuu     uuu
        uu:   u u:  :uu
      :u: u::uu uu::: uu
      u: uu uuu uuu uu :u
     u::uu uuuu uuuu uu::u
    u::uuu:uuuu uuuu::uu:::
   :u uuu:uuu:: ::uuu uuu u:
   u uuuu  :::u u:::  uuuu u
  :u u:  :uuuuu uuuuu:  uu:u
  u:u :u:uuuuuu uuuuuuuu::u:u
  u  :uu:uuuuuu uuuuuu uu:  u
  u :uuu uuuuuu:uuuuuu:uuu: u
 :u uuuu:uuuuuuuuuuuuu:uuuu u
 :::uuuu:uuuuuu:uuuuuu:uuuu::
 u:         uu:  uu         ::
 u::uuuu:uuuuuu uuuuuu uuuu::
 ::uuuuu:uuuuuu uuuuuu:uuuu::
  u uuuu uuuuuu uuuuuu:uuuu u
  u  uuu:uuuu     uuuu uuu  u
  u:u uuuuu:       :uuuuu u u
  :u:u :uu           uu: u:::
  :u uuuu             uuuu u
   uuuuu:             :uuu::
    u uu    u     u    uu u
    :uuu   :u:   :u:   uuu:
     :u:               :u:
      uu               uu
       :uu           uu:
         uu:       :u:
           uuuuuuuuu
=== FRAME 41 ===
           :uuuuuuu:
          uuu     uuu:
        uuu   u ::  :uu:
      :u: u: uu uuu u: u:
     :u::uu uuu uuu:uuu u:
     u:uuu uuuu uuuu:uuu uu
    u uuuu:uuuu uuuuu uuu u:
   :u:uuu:uu:u: :u:uu uuuu:u
   u:uuuu ::::u ::::: :uuu:u:
  :u:uu  :uuuuu uuuuuu  :uu u
  u:u::u:uuuuuu uuuuuu uu u:u:
 :u  :uu uuuuuu uuuuuu:uuu :::
 :: uuuu uuuuuu:uuuuuu:uuuu  u
 u::uuuu uuuuuuuuuuuuu::uuuu u
 u uuuuu uuuuuu :uuuuu::uuuu u
 u          uu:  uu          u
 u uuuuu uuuuuu :uuuuuu:uuuu :
 u :uuuu uuuuuu uuuuuuu:uuuu u
 :u uuuu uuuuuu uuuuuuuuuuu: u
 ::  uuu uuuu:    :uuu:uuu:  u
  u ::uuuuu:        uuuuu:::u:
  ::uu :uu           :uu :u u
   u uuuu             uuuuu:u
   u:uuu:              uuu u
   :u uu    u:    u    :u u
    uuuu    u:    u:    uuu
     uu:                uu
      uu               :u
       :u:           :uu
         uu:       :uu
          :uuuuuuuuu:
=== FRAME 42 ===
           :uuuuuuu:
         :uuu     uuuu
       :uu:   u ::  :uu:
      :u  u::uu uuu u: uu
     uu :u::uuu uuuu:uu u:
    uu uuu:uuuu uuuu:uuu uu
   :u uuu::uuuu uuuuu uuu u:
   u::uuu uu:uu :u:uu:uuuu u
  uu uuu: ::::u :::::  uuuu:u
  u uu:  :uuuuu uuuuuu  :uu u
 :u u uu uuuuuu uuuuuu:uu u:u:
 u:: uuu uuuuuu uuuuuu:uuu:  u
 u  uuuu uuuuuu:uuuuuu::uuu  u
 u uuuuu:uuuuuuuuuuuuuu:uuuu u
 u uuuuu:uuuuuu :uuuuuu:uuuu:u
 u          uu   uu          u
 u uuuuu:uuuuuu :uuuuuu:uuuu:u
 u uuuuu:uuuuuu uuuuuuu:uuuu u
 u  uuuu uuuuuu uuuuuuu:uuu: u
 u  :uuu uuuu:    :uuu::uuu  u
 uu:::uuuuu         uuuuu: ::u
  u:u: uuu           :uu :u u:
  u:uuuuu             uuuuu:u
   u uuu               uuu:u
   uu uu    u     u    :u::u
    uuu:   :u:   :u:    uuu
     uu                 uu:
      uu               :u
       uu:           :uu
         uu        :uu
          :uuuuuuuuu:
=== FRAME 43 ===
           :uuuuuu::
         uuuu     uuu:
       :uu:   u u:  ::u:
      uu :u :uu uuu u: uu
     uu uu::uuu uuuu uu uu
    uu uuu:uuuu uuuuuuuu::u
   uu uuu:uuuuu uuuuu uuu uu
  :u uuuu uu:u: uu:uu:uuuu:u:
  u:uuuu: :::uu u::::  uuuu u
  u uu:  :uuuuu uuuuuu  :uu u:
 uuuu uu uuuuuu uuuuuuuuu::u:u
 u  :uuu uuuuuu uuuuuu::uu:: u
:u  uuuu:uuuuuu:uuuuuuu:uuu: u
:u uuuuuuuuuuuuuuuuuuuu:uuuu u:
:u:uuuu::uuuuuu :uuuuuu:uuuu:::
uu          uu   uu          :u
:u:uuuu::uuuuuu :uuuuuu:uuuu: :
:u uuuuuuuuuuuu uuuuuuu:uuuu:::
:u uuuuuuuuuuuu uuuuuuu:uuuu u:
 u  uuuu:uuu::    :uuuu:uuu  u
 u:: uuuuuu         uuuuuu u u
 :u u: uu:           :uu :u:::
  u:uuuuu             :uuuu u
  :u:uuu               uuu:u:
   u::u:    u     u    :u::u
    uuu    uu:   :u:    uuu
    :uu                 uu
      u:               :u
       uu:            uu
         uu        :uu
          :uuuuuuuuu:
=== FRAME 44 ===
           :uuuuuu::
         uuuu     uuu:
       uuu:   u u:  ::u:
      uu :u :uu uuu :: uu
     uu uu::uuu uuuu uu uu
    u::uuu:uuuu uuuuu:uu::u
   uu uuu uuuuu uuuuu uuu :u
  :u uuuu u::u: uu:uu:uuuu u:
  u:uuuu: :::uu u::::  uuuu u
 :u uu:  uuuuuu uuuuuu  :uu u:
 u:u::uu uuuuuu uuuuuuuuu::u u
 u ::uuu:uuuuuu uuuuuu::uu:  u
:u :uuuuuuuuuuu:uuuuuuu:uuu: u
u::uuuu:uuuuuuuuuuuuuuu:uuuu :u
u :uuuu uuuuuuu uuuuuuu uuuu: u
u           uu   uu          :u
u uuuuu uuuuuu: :uuuuuu uuuuu u
u :uuuu:uuuuuuu uuuuuuu:uuuu::u
uu uuuu:uuuuuuu uuuuuuu:uuuu ::
:u  uuuu:uuu::    :uuuu:uuu  u
 u u uuuuuu         uuuuuu u u
 u::u: uu:           :uu :u::u
  u uuuu:             :uuuu u
  uu:uuu               uuuuuu
   u :u:    u     u    :u::u
   :uuu    :u:   :uu    uuu:
    :uu                 :u:
     :u                :u:
       uu:            uu
        :uu        :uu:
          :uuuuuuuuu:
=== FRAME 45 ===
           :::uuu:::
         :uuu: :  uuuu
       :uu:   u uu   uuu
      uu :u::uu:uuu :: :u
     uu uu: uuu:uuuu uu :u:
    uu uuu uuuu:uuuuu uu::u
   u: uuu::uuuu:uuuuu uuu: u
  uu uuuu:uuuuu :uuuuu:uuu::u
  u uuuu: ::::u u::::  uuuu u:
 :u:uu:  uuuuuu:uuuuuu   uuu:u
 u:u: uu uuuuuu:uuuuuuu:u: u u
 u  :uuu:uuuuuu:uuuuuuu uuu  ::
:u :uuuu:uuuuuu:uuuuuuu uuuu :u
u: uuuu:uuuuuuuuuuuuuuu uuuu: u
u:uuuuu:uuuuuuu::uuuuuu uuuuu u
u:          uu:  uu:          u
u:uuuuu:uuuuuuu  uuuuuu uuuuu u
u:uuuuu:uuuuuuu:uuuuuuu uuuuu u
:: uuuuuuuuuuuu:uuuuuuu uuuu  u
:u  uuuu:uuuu::  ::uuuu uuu: ::
 u u uuuuuu         uuuuuu:: u
 :::u: uuu            uu: uu u
 :u uuuuu              uuuu:u:
  u::uuu               :uuu u
   u uu:    u     u:    uu ::
   :uuu    :u:    uu    uuuu
    :uu                  u:
     :u:                uu
       uu:            uu:
        :uu         uuu
          :uuuuuuuuu:
=== FRAME 46 ===
           :::uuu:::
         :uuu: :  uuuu
       :uu:   u uu   uuu
      uu :u::uu:uuu :: :u
     uu uu: uuu:uuuu uu :u:
    uu:uuu uuuu:uuuuu uu::u
   u: uuu::uuuu:uuuuu uuu: u
  uu uuuu:uuuuu :uuuuu:uuu::u
  u uuuu: ::::u u::::  uuuu u:
 :u:uu:  uuuuuu:uuuuuu   uuu:u
 u:u: uu uuuuuu:uuuuuuu:u: u u
 u  :uuu:uuuuuu:uuuuuuu uuu  ::
:u :uuuu:uuuuuu:uuuuuuu uuuu  u
u: uuuu:uuuuuuuuuuuuuuu uuuu: u
u:uuuuu:uuuuuuu::uuuuuu uuuuu u
u:          uu:  uu:          u
u:uuuuu:uuuuuuu  uuuuuu uuuuu u
u:uuuuu:uuuuuuu:uuuuuuu uuuuu u
:: uuuuuuuuuuuu:uuuuuuu uuuu  u
:u  uuuu:uuuu::  ::uuuu uuu: :u
 u u uuuuuu         uuuuuu:: u
 :::u: uuu            uu: uu u
 :u uuuuu              uuuu:u:
  u::uuu               :uuu u
   u uu:    u     u:    uu ::
   :uuu    :u:    uu    uuuu
    :uu                  u:
     :u:                uu
       uu:            uu:
        :uu         uuu
          :uuuuuuuuu:
__YUTORI_AGENT_FRAMES_CACHE__
}

load_frame_count() {
    awk '/^=== FRAME / { count += 1 } END { print count + 0 }' "$FRAMES_CACHE_FILE"
}

# Pre-render every frame once into its own file so the hot loop can `cat` a
# fixed path per tick instead of forking awk 12 times per second.
#
# Assigns the resulting directory to the global FRAMES_RENDER_DIR so
# cleanup_temp_files can reap it on any exit path. Does NOT return the path
# via stdout — callers would need `$(prerender_frames ...)`, and that runs in
# a subshell where the FRAMES_RENDER_DIR assignment would be lost.
prerender_frames() {
    local frame_count="$1"
    local use_color="$2"
    local idx
    FRAMES_RENDER_DIR="${FRAMES_CACHE_FILE}.rendered.d"
    mkdir -p "$FRAMES_RENDER_DIR"
    for (( idx = 0; idx < frame_count; idx++ )); do
        render_frame "$idx" "$use_color" >"$FRAMES_RENDER_DIR/$idx"
    done
}

render_frame() {
    local frame_index="$1"
    local use_color="$2"

    awk \
        -v frame_index="$frame_index" \
        -v pad_x="4" \
        -v pad_y="1" \
        -v use_color="$use_color" \
        -v frames_cache_file="$FRAMES_CACHE_FILE" \
        '
        function colorize_row(source_row,    result, char_index, ch, style, last_style) {
            if (!use_color) {
                return source_row
            }

            result = ""
            last_style = reset_style
            for (char_index = 1; char_index <= length(source_row); char_index++) {
                ch = substr(source_row, char_index, 1)
                if (ch == "u") {
                    style = bright_style
                } else if (ch == ":") {
                    style = medium_style
                } else {
                    style = reset_style
                }

                if (style != last_style) {
                    result = result style
                    last_style = style
                }
                result = result ch
            }

            if (last_style != reset_style) {
                result = result reset_style
            }

            return result
        }

        BEGIN {
            bright_style = sprintf("%c[1;38;2;110;255;190m", 27)
            medium_style = sprintf("%c[38;2;42;214;158m", 27)
            reset_style = sprintf("%c[0m", 27)
            wanted_header = "=== FRAME " frame_index " ==="
            in_frame = 0
            row_count = 0

            while ((getline line < frames_cache_file) > 0) {
                if (line ~ /^=== FRAME /) {
                    if (line == wanted_header) {
                        in_frame = 1
                        continue
                    }
                    if (in_frame) {
                        break
                    }
                }

                if (in_frame) {
                    row_count += 1
                    rows[row_count] = line
                }
            }
            close(frames_cache_file)

            if (row_count == 0) {
                exit 1
            }

            for (pad_row = 0; pad_row < pad_y; pad_row++) {
                print ""
            }

            left_pad = sprintf("%" pad_x "s", "")
            for (row = 1; row <= row_count; row++) {
                printf "%s%s\n", left_pad, colorize_row(rows[row])
            }
        }
        '
}

play_animation_until_done() {
    local install_pid="$1"
    local use_color="$2"
    local banner_lines
    local frame_count
    local frame_top
    local frame_index=0
    local displayed_frames=0
    local minimum_frames=2
    # Animation cadence ~12fps; inlined to avoid forking awk for 1/12.
    local frame_sleep="0.0833"

    write_frames_cache
    frame_count="$(load_frame_count)"
    # Call directly (not via $(...)) so FRAMES_RENDER_DIR survives in the
    # parent shell — cleanup_temp_files needs it on exit.
    prerender_frames "$frame_count" "$use_color"
    banner_lines=0
    # Use pre-increment. A post-increment expression evaluates to the old
    # value, so on the first iteration it would be 0 — which under `set -e`
    # aborts the whole installer before the animation can render.
    while IFS= read -r _; do
        (( ++banner_lines ))
    done <<<"$YUTORI_BANNER"
    # Screen rows after `\033[2J\033[H` + render_banner + bootstrap intro:
    #   1..N       banner
    #   N+1        trailing blank from render_banner's printf '\n'
    #   N+2        > Yutori installer
    #   N+3        | Interactive terminal detected.
    #   N+4        | Installing Yutori CLI with uv...
    #   N+5        trailing blank from render_bootstrap_intro's \n\n
    #   N+6        first row available for the frame
    frame_top="$((banner_lines + 6))"

    printf '\033[2J\033[H'
    render_banner
    render_bootstrap_intro "Interactive terminal detected." "$use_color"
    printf '\033[?25l'

    printf '\033[%s;1H' "$frame_top"
    cat "$FRAMES_RENDER_DIR/$frame_index"
    displayed_frames=1

    # `kill -0` checks liveness without signaling; outer `wait` in main()
    # is what actually collects exit status. Keep at least two frames so a
    # fast reinstall still visibly shows the Navigator instead of racing from
    # banner straight to the Python UI with no logo at all.
    while kill -0 "$install_pid" 2>/dev/null || (( displayed_frames < minimum_frames )); do
        sleep "$frame_sleep"
        frame_index="$(((frame_index + 1) % frame_count))"
        printf '\033[%s;1H' "$frame_top"
        cat "$FRAMES_RENDER_DIR/$frame_index"
        (( displayed_frames += 1 ))
    done

    printf '\033[%s;1H' "$frame_top"
    # Clear from the cursor to the end of the screen. Without this, the final
    # animation frame stays drawn below frame_top, and when the Python UI
    # starts printing it only overwrites the first few rows — residual frame
    # characters bleed through below the step prompts and summary table.
    printf '\033[0J'
    printf '\033[0m\033[?25h'
    # cleanup_temp_files (EXIT trap) will rm the render dir on any exit path.
}

render_static_logo() {
    local use_color="$1"
    local frame_count
    local last_frame

    write_frames_cache
    frame_count="$(load_frame_count)"
    last_frame="$((frame_count - 1))"
    printf '%s\n' "$(render_frame "$last_frame" "$use_color")"
}

print_log_tail_and_exit() {
    error "Yutori CLI install failed."
    if [[ -n "$INSTALL_LOG" && -f "$INSTALL_LOG" ]]; then
        printf '\n' >&2
        # Print before the EXIT trap can race and delete the log.
        if ! tail -n 40 "$INSTALL_LOG" >&2; then
            error "(Could not read install log at $INSTALL_LOG.)"
        fi
    fi
    exit 1
}

handoff_to_python_ui() {
    local bin_dir
    local yutori_bin

    if [[ -z "$UV_BIN" ]] && ! UV_BIN="$(resolve_uv_bin)"; then
        error "uv is not available; cannot locate the installed CLI."
        exit 1
    fi

    # Capture stdout and stderr separately — if uv emits a warning on stderr
    # but still writes the path to stdout, we must not let the warning text
    # bleed into yutori_bin. On failure, the stderr content goes into the
    # user-facing error message.
    local bin_dir_stderr
    bin_dir_stderr="$(mktemp "${TMPDIR:-/tmp}/yutori-uv-bin-dir.XXXXXX")"
    if ! bin_dir="$("$UV_BIN" tool dir --bin 2>"$bin_dir_stderr")"; then
        local stderr_content
        stderr_content="$(cat "$bin_dir_stderr")"
        rm -f "$bin_dir_stderr"
        error "uv tool dir --bin failed: $stderr_content"
        exit 1
    fi
    rm -f "$bin_dir_stderr"
    # uv may print multi-line output (rare, but possible). Use the last
    # non-empty line as the path — matches inspect_cli_install in Python.
    bin_dir="$(printf '%s\n' "$bin_dir" | awk 'NF { line = $0 } END { print line }')"
    if [[ -z "$bin_dir" ]]; then
        error "uv tool dir --bin returned empty output."
        exit 1
    fi
    yutori_bin="${bin_dir}/yutori"

    if [[ ! -x "$yutori_bin" ]]; then
        error "Installed CLI was not found at ${yutori_bin}."
        exit 1
    fi

    printf '\n'
    cleanup_temp_files
    # The Python UI reuses uv via YUTORI_UV_BIN when PATH may not yet include
    # the uv tool bin dir (e.g., first-time installs where update-shell hasn't
    # run). Reopen /dev/tty for stdin so interactive prompts work under
    # `curl | bash` — the script's stdin is the downloaded bytes by default.
    export YUTORI_UV_BIN="$UV_BIN"
    # YUTORI_INSTALLER_BOOTSTRAP_SHOWN is exported by render_bootstrap_intro
    # itself (when the intro actually prints), so no unconditional export here.
    if has_usable_tty; then
        exec "$yutori_bin" __install_ui </dev/tty
    fi
    exec "$yutori_bin" __install_ui
}

main() {
    local interactive
    local animation_mode
    local use_color=0
    local install_status=0

    assert_supported_platform
    interactive=0
    if is_interactive_terminal; then
        interactive=1
    fi

    animation_mode="off"
    if (( interactive )) && [[ "${YUTORI_INSTALL_SKIP_ANIM:-0}" != "1" ]]; then
        if supports_truecolor; then
            animation_mode="full"
            use_color=1
        else
            animation_mode="static"
        fi
    fi

    # Bootstrap uv up front, in the parent shell, so UV_BIN is set once
    # and reused by both the backgrounded install and the handoff below.
    # Any uv-bootstrap output happens before the animation starts.
    if ! ensure_uv; then
        exit 1
    fi

    # Enable job control so the backgrounded install gets its own process
    # group — needed for kill_uv_process_group() to actually reach uv and its
    # curl/cargo children on Ctrl-C. Harmless in non-animation modes.
    set -m

    INSTALL_LOG="$(mktemp "${TMPDIR:-/tmp}/yutori-install-log.XXXXXX")"
    INSTALL_STATUS_FILE="$(mktemp "${TMPDIR:-/tmp}/yutori-install-status.XXXXXX")"
    if [[ "$animation_mode" == "full" ]]; then
        start_install_job
        play_animation_until_done "$UV_INSTALL_PID" "$use_color"
    else
        render_banner
        if (( interactive )); then
            render_bootstrap_intro "Interactive terminal detected." "$use_color"
        else
            render_bootstrap_intro "Non-interactive terminal detected." "$use_color"
        fi
        if [[ "$animation_mode" == "static" ]]; then
            render_static_logo "$use_color"
        fi
        # Still background + capture even in static/off modes so failure
        # output renders consistently via print_log_tail_and_exit.
        start_install_job
    fi

    # Prefer the status file (written by the install subshell) over `wait`'s
    # return — under job-control, `wait` can return 127 ("no such job") for a
    # successfully reaped PID. Fall back to `wait`'s status when the file is
    # missing or unparseable, so SIGKILL / setup-crash paths still surface as
    # a non-zero install_status rather than defaulting to 1 with no context.
    local wait_status=0
    wait "$UV_INSTALL_PID" 2>/dev/null || wait_status=$?
    UV_INSTALL_PID=""
    if [[ -s "$INSTALL_STATUS_FILE" ]]; then
        install_status="$(tr -d '\n' < "$INSTALL_STATUS_FILE")"
    else
        install_status="$wait_status"
    fi
    if [[ ! "$install_status" =~ ^[0-9]+$ ]]; then
        install_status="${wait_status:-1}"
    fi
    if [[ ! "$install_status" =~ ^[0-9]+$ ]]; then
        install_status=1
    fi

    if (( install_status != 0 )); then
        print_log_tail_and_exit
    fi

    # Echo the install output in non-animation modes so users see uv's
    # progress; in `full` mode it was intentionally hidden by the animation.
    if [[ "$animation_mode" != "full" && -f "$INSTALL_LOG" ]]; then
        cat "$INSTALL_LOG"
    fi

    handoff_to_python_ui
}

main "$@"
