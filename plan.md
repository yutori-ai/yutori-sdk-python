# Plan: `curl | bash` installer for Yutori SDK & CLI

## Goal

Ship a one-line installer for Yutori that feels as polished as modern developer-tool installers, matches Yutori's terminal brand, and does not leave the user with a manual "now run this other command" follow-up.

**The one-liner:**

```bash
curl -fsSL https://yutori.com/install.sh | bash
```

Common names for this pattern: **one-line installer**, **shell installer**, **bootstrap installer**.

## Context

### Current package state

- `yutori` is distributed via PyPI as a single Python package that already includes both the SDK and the CLI.
- The CLI entrypoint is exposed through `[project.scripts]` as `yutori`.
- `typer` and `rich` are regular dependencies already shipped in the package, so we can build a polished interactive installer UI in Python without introducing Node.
- Drive-by fix: `yutori/cli/main.py:10` still prints `pip install yutori[cli]` if the `typer` import fails, but there is no `cli` extra. This is dead code today (typer is a hard dep) — update the message as part of this work.

### SDK vs CLI install targets

`yutori` is two surfaces that want different destinations:


| Surface                             | Where it belongs                      | Best install action                          |
| ----------------------------------- | ------------------------------------- | -------------------------------------------- |
| **CLI** (`yutori auth login`, etc.) | Global, on `$PATH`                    | `uv tool install yutori`                     |
| **SDK** (`import yutori`)           | Inside the user's project environment | `uv add yutori` or project-aware pip install |


The installer primarily optimizes for the CLI, then offers an in-flow yes/no SDK install.

### Current web + brand surface

From the monorepo:

- `/api` uses a black + slate browser-chrome look with white copy and mint highlights.
- `Yutori Local` uses a near-black background, mint glow, ASCII/pixel-terminal language, and a strong "alive in the terminal" feel.
- `AgentLogo4.webm` / `.mp4` / `.gif` are the current rendered spinning Navigator logo assets used on the landing page.
- The landing page does **not** currently expose an `install.sh` route or asset.

### Existing terminal animation prototype

A reverse-engineered bash approximation of the spinning Navigator logo is on `main`:

- **Source:** `~/projects/yutori-monorepo/scripts/brand/agent_logo_terminal/play.sh` (378 lines of bash).
- **Role:** primary implementation reference for the installer's animation.

Because `install.sh` must be self-contained (no runtime dependency on the monorepo), the relevant parts of `play.sh` get **inlined into `install.sh` at build time** by the release pipeline. The monorepo copy stays the canonical source; the installer's copy is a generated artifact.

## Recommended architecture

Use a **two-layer installer**:

### Layer 1: shell bootstrap (`install.sh`)

`install.sh` stays small and boring, but it owns the **first-impression brand moment**. Its job:

1. Detect OS / shell / TTY capabilities.
2. Render the Yutori wordmark.
3. In parallel:
  - **Foreground:** play the inlined Navigator animation (adapted from `play.sh`).
  - **Background:** ensure `uv` is available, then run `uv tool install --upgrade yutori`, with stdout/stderr captured to a temporary log so the animation owns the terminal (see §4 Animation output isolation).
4. Reconcile the two timelines (see §4 Animation race).
5. Exec `"$(uv tool dir --bin)/yutori" __install_ui` to hand off to the Python installer UI. Using the absolute path avoids a bootstrap dead-end on machines where the `uv` tool bin dir is not yet on `$PATH`.

The point of doing branding and install in parallel: `uv tool install` takes several seconds (fetch + resolve + install). If we wait for `uv` first and render branding second, the user sees `uv`'s progress output before any Yutori pixel — the polish moment lands late. Running them concurrently keeps the Yutori surface front-and-center while the real work happens underneath.

### Layer 2: Python installer UI

The shell bootstrap invokes a hidden subcommand on the freshly-installed CLI:

```bash
"$(uv tool dir --bin)/yutori" __install_ui
```

Hidden (dunder-prefixed, excluded from `--help`) because it only exists as a handoff target from the bootstrap — users never call it directly. Naming it `self-install` would be misleading (that pattern usually means "upgrade myself", like `rustup self update`).

Responsibilities of this layer:

- Verify the CLI is reachable; offer to repair `$PATH` via `uv tool update-shell` if not.
- Inspect the current directory and environment.
- Prompt (per-signal default — see §4) to install the SDK into the current project.
- Prompt (default Yes in TTY) to run `yutori auth login`; in non-TTY, switch to a URL-print path instead of blocking on the callback server.
- Prompt (default Yes) to run a verification browsing task.
- Print a final outcome summary reflecting the resulting state, not user homework.

This is the layer that should visually echo `add-mcp` — vertical guide lines, structured prompts, boxed summary — rendered via `rich`.

## Runtime flow

### Shell bootstrap

1. Verify supported environment (macOS/Linux).
2. Detect whether stdout is an interactive TTY.
3. Render Yutori wordmark.
4. Start `uv` bootstrap and `uv tool install --upgrade yutori` in the background, capturing output to a temporary log.
5. Play the Navigator animation in the foreground while that install runs.
6. Reconcile the two timelines (see §4).
7. Exec `"$(uv tool dir --bin)/yutori" __install_ui` for the interactive layer. On install failure, skip the handoff and print the captured log tail.

### Python installer UI

1. Confirm CLI reachability; offer `uv tool update-shell` if needed.
2. Inspect cwd for project signals (`pyproject.toml`, active venv, `requirements.txt`, etc.).
3. Prompt to install the SDK into the current project.
4. Prompt to log in (TTY: runs full flow; non-TTY: prints auth URL only).
5. Prompt to run a verification browsing task.
6. Print the final summary.

## Detailed design

### 1. Banner: standalone `Yutori`

Both candidates land in-repo before release, so they can be compared side by side:

**Track A — crop of the Local banner:** crop `apps/desktop/yutori_local.txt` columns 1–21 to get a clean "Yutori" half of the existing two-word banner. Commit as `assets/terminal/yutori_banner_crop.txt`.

**Track B — dedicated wordmark:** commission a purpose-built terminal wordmark that says "Yutori" as a standalone mark, in the same visual family as the `Yutori Local` banner. Commit as `assets/terminal/yutori_banner_custom.txt`.

The installer temporarily reads the crop while both exist; comparison happens in-terminal with both side by side; the winner gets promoted to `assets/terminal/yutori_banner.txt` and the other is deleted.

Either way the banner renders in brand mint (`#1DCD98`) at install start.

### 2. Navigator animation: prototype-first, media-validated

**Primary path**

- Start from `play.sh` on `yutori-monorepo` main.
- Inline the essential frame-rendering logic into `install.sh` at release-build time.
- Preserve `play.sh`'s motion language as closely as possible.

**Validation source**

- `AgentLogo4.webm`
- `AgentLogo4.mp4`
- `AgentLogo4.gif`

These media assets remain the visual truth for fidelity checks, but they are **not required at installer runtime** — the installer is fully self-contained.

**Fallback plan**

Only if `play.sh`'s approximation proves insufficient:

- Add a build-time extraction/regeneration pipeline from `AgentLogo4.webm` (ffmpeg → chafa → base64-embedded frames).
- Keep the runtime dependency-free.

**Runtime rules**

- Play only on interactive TTYs.
- Skip in CI / pipes / non-interactive shells.
- Escape hatch: `YUTORI_INSTALL_SKIP_ANIM=1`.
- Degrade to a static final frame when terminal support is weak (no truecolor, no cursor-move).

### 3. Installer UX: Rich, not pure bash prompts

The target interaction style is the vertical-guide, structured prompt feel from `add-mcp`.

We imitate the **visual grammar**, not literally depend on `@clack/prompts`.

Core UI elements:

- Vertical guide lines
- Step headers
- Spinner / progress rows
- Yes/no confirms
- Boxed installation summary
- Explicit success / skipped / failed states

Example output shape (rendered by the Python layer):

```text
 _    _                    _
/ \  / \                  / \
\  \/  /     /\_  ____  ___  <>
 \    /\  /\/ __\/ __ \/ __\/\
 /   / /_/ / /__/ /_/ / /  / /
 \__/\____/\___/\____/\/   \/

◆ Yutori CLI installed
│  Location: ~/.local/bin/yutori
│
◇ Install the Python SDK into this project?
│  Detected pyproject.toml in the current directory.
│  ● Yes   ○ No
│
◇ Log in to Yutori now?
│  ● Yes   ○ No
│
◇ Run a quick verification browsing task?
│  Will run: `yutori browse run "Give me a list of all employees (names
│  and titles) of Yutori." https://yutori.com`
│  ● Yes   ○ No
│
■ Done
```

The shell bootstrap never renders this — it only handles the wordmark and animation before handoff.

### 4. Install behavior

#### CLI install

Run in the bash layer, concurrently with the animation:

```bash
uv tool install --upgrade yutori
```

Then, in the Python layer:

- Verify `yutori --version`.
- Check whether the installed bin dir is on `$PATH`.
- If not: prompt (default Yes) to run `uv tool update-shell`. Delegate rc-file selection to uv rather than reinventing it — uv already handles the "which shell, which rc file" decision across bash/zsh/fish/nushell.

That shell-config update is itself an explicit, opt-in prompt — not a silent mutation.

#### SDK install

Never tell the user to manually run `pip install yutori` as a follow-up.

The prompt is always shown (per §Responsibilities of Layer 2). What changes by signal is the **action** and the **default** — weak signals don't auto-enroll the user into a destructive mutation:


| cwd signal                                          | Action on Yes                                                    | Default |
| --------------------------------------------------- | ---------------------------------------------------------------- | ------- |
| `pyproject.toml` present                            | `uv add yutori`                                                  | Yes     |
| Active virtualenv (`VIRTUAL_ENV` set, no pyproject) | `python -m pip install yutori` into the active venv              | Yes     |
| `requirements.txt` only                             | Append `yutori` to `requirements.txt`, then `pip install yutori` | **No**  |
| Nothing project-like                                | `uv pip install --user yutori`                                   | **No**  |


Always show the exact command before acting. Weak-signal prompts (requirements.txt, nothing project-like) intentionally pre-select **No** because they mutate tracked files or user-wide state from an arbitrary cwd — a user who actually wants that outcome can say Yes with one keystroke, but the default doesn't nudge them into it.

#### Auth login

Prompt yes/no (default Yes).

**Interactive TTY:** run `yutori auth login`. The existing flow starts a localhost callback server on port 54320, opens a browser, waits for the callback (`AUTH_TIMEOUT_SECONDS = 300`), then writes credentials.

**Non-interactive environment** (pipe, CI, detached SSH): do **not** invoke the blocking callback flow — `yutori/auth/flow.py:244` would hang for up to 5 minutes waiting for a browser round-trip that can't happen. Instead, print the auth URL and instructions to finish auth on another machine:

```text
◇ Non-interactive session detected.
│  Open this URL on a device with a browser:
│    https://accounts.yutori.com/oauth-consent?...
│  Then run `yutori auth status` on this machine to verify.
```

This requires a small CLI addition: `yutori auth login --print-url` (or `yutori auth url`) that calls the existing `build_auth_url()` from `yutori/auth/flow.py:65` and exits immediately without starting the callback server. The installer invokes this in non-TTY mode.

#### Verification browsing task

Once the user is authenticated, prompt (default Yes) to run a quick verification run using the canonical docs example:

```bash
yutori browse run "Give me a list of all employees (names and titles) of Yutori." "https://yutori.com"
```

Poll to completion, render the result inside the installer UI, and end on a success message. This doubles as: (a) proof-of-install, (b) the user's first real "it worked" moment, (c) a signal that their API key is valid end-to-end.

Skip silently if auth didn't actually land (no valid credentials) and skip automatically in non-interactive environments.

#### Animation race

Lock in these behaviors so the UX is deterministic:

- `**uv tool install` finishes before the first animation pass ends:** cut the animation short immediately — don't make the developer wait for the aesthetic.
- **Animation pass ends before `uv tool install` finishes:** keep looping until install completes. Never stall silently.
- `**uv tool install` fails:** stop the animation and print the captured log tail before exiting non-zero. Do not hand off to the Python UI.

#### Animation output isolation

While the animation is playing, raw `uv tool install` output stays out of the main terminal surface:

- Redirect stdout/stderr to a temporary log (`mktemp`) while the animation owns the screen.
- On success, discard the noisy raw log and continue with a structured installer summary.
- On failure, stop the animation and print the captured log tail before exiting.
- On `Ctrl-C`, clean up the log file and kill the background `uv` process.

### 5. Hosting and distribution

**Canonical URL:** `https://yutori.com/install.sh`

**Source of truth:** versioned in the SDK repo.

**Recommended serving model — redirect via `vercel.json`:**

The landing page (`webapps/landing-page/`) is a Next.js app on Vercel. Its `vercel.json` already defines a redirects block for things like `/product` → `/scouts`. The cleanest fit is to add one more:

```json
{
  "source": "/install.sh",
  "destination": "https://github.com/yutori-ai/yutori-sdk-python/releases/latest/download/install.sh"
}
```

Why redirect (vs. proxy via a Next route handler):

- **Zero function cost.** Redirects are handled at Vercel's edge layer for free. A proxy route costs a function invocation on every `curl` call.
- **GitHub's CDN does the heavy lifting.** Release asset downloads are already CDN-backed. Redirecting to `releases/latest/download/install.sh` means `yutori.com` just points the browser (or `curl`) at the real artifact.
- **Consistency with existing patterns.** `vercel.json` already contains redirects; no new primitive introduced.
- **Versioning headroom.** Swapping `/install.sh` to point at a pinned release, a canary, or a proxy later is a one-line change.

`curl -fsSL` follows redirects silently by default, so users see no behavioral difference.

**SHA256 served the same way:**

```json
{ "source": "/install.sh.sha256", "destination": "https://github.com/yutori-ai/yutori-sdk-python/releases/latest/download/install.sh.sha256" }
```

### 6. Integrity publishing

Publish alongside each release:

- `install.sh`
- `install.sh.sha256`

Verification path for security-conscious users:

```bash
curl -fsSL https://yutori.com/install.sh -o install.sh
curl -fsSL https://yutori.com/install.sh.sha256 | shasum -a 256 -c
bash install.sh
```

## Brand styling

Blend two existing Yutori terminal languages:

### From `/api`

- Black background.
- Slate browser-chrome separators.
- Crisp, product-facing UI framing.

### From `Yutori Local`

- Mint primary color.
- ASCII / terminal-native attitude.
- Subtle glow and motion.

Recommended terminal palette:


| Role           | Color     | Use                                  |
| -------------- | --------- | ------------------------------------ |
| Brand mint     | `#1DCD98` | Wordmark, success state, active step |
| Mint highlight | `#5AE8BD` | Selected option, focus state         |
| Slate dim      | `#2A2C2F` | Guide lines, dividers, quiet labels  |
| Slate text     | `#94A3B8` | Secondary descriptions               |
| White          | `#F8FAFC` | Primary body text                    |


The installer should feel like `Yutori Local` and `Yutori API` had a child, not like a generic PyPI bootstrapper.

## Scope decisions

### In v1

- `install.sh` as a thin shell bootstrap that owns the brand moment (wordmark + animation) and backgrounds `uv tool install` with captured logs.
- Absolute-path handoff to `"$(uv tool dir --bin)/yutori" __install_ui` — no reliance on `$PATH` being updated first.
- Python/Rich interactive installer command (`yutori __install_ui`) in the package.
- Two banner candidates shipped simultaneously (crop + custom wordmark); pick winner before GA.
- Navigator animation inlined from `scripts/brand/agent_logo_terminal/play.sh` at build time.
- Global CLI install via `uv tool install`.
- `$PATH` repair via `uv tool update-shell` prompt (default Yes).
- In-flow SDK install prompt — always shown; per-signal defaults per §4 (Yes for `pyproject.toml` / active venv, No for `requirements.txt`-only / nothing project-like).
- In-flow auth login prompt (default Yes) — interactive TTY runs the full flow; non-TTY prints the auth URL via `yutori auth login --print-url`.
- New CLI flag: `yutori auth login --print-url` (non-blocking URL emitter using existing `build_auth_url()`).
- In-flow verification browsing task prompt (default Yes) using the canonical docs example.
- Redirect at `yutori.com/install.sh` and `yutori.com/install.sh.sha256` via `vercel.json`.
- Published `install.sh.sha256` via release asset upload.
- Drive-by: fix the stale `yutori[cli]` message in `yutori/cli/main.py:10`.

### Out of scope for v1

- Desktop app installation.
- Full Windows bootstrap parity. Installer fails clearly and politely on Windows rather than silently degrading to `pip install`.
- Perfect `@clack/prompts` parity — imitate the visual grammar, don't obsess over pixel matching.
- Runtime video/frame conversion in the installer itself.

## Open questions

1. **Banner winner mechanism.** Both candidates land in-repo before release; who makes the call between crop vs. custom wordmark, and on what criteria (legibility at 80 cols? brand-team sign-off?)?
2. **Verification task on-failure behavior.** If the browsing task fails (network hiccup, `yutori.com` down, model rejection), do we:
  a. Show the error inside the installer summary and exit 0, or
   b. Exit non-zero so scripted installers can notice?
   Leaning (a) — a failed verification isn't a failed install.
3. **Uninstall story.** The installer installs into `~/.local/bin` and possibly user/venv site-packages. Do we ship `yutori uninstall` / `curl | bash` uninstaller in v1 or defer? Leaning defer — `uv tool uninstall yutori` already handles the CLI side.

## Implementation order

1. Fix the stale `yutori[cli]` message in `yutori/cli/main.py:10` (drive-by, unblocks nothing but cleans up context).
2. Add `yutori auth login --print-url` flag (calls `build_auth_url()` and exits, no callback server).
3. Commit v1 banner crop (`assets/terminal/yutori_banner_crop.txt`) from `yutori_local.txt`.
4. Kick off the custom-wordmark design in parallel; land as `assets/terminal/yutori_banner_custom.txt` when ready.
5. Add the hidden `yutori __install_ui` subcommand skeleton rendered with `rich` (no install logic yet — just the visual flow).
6. Implement CLI install verification + `uv tool update-shell` prompt inside `__install_ui`.
7. Implement cwd-aware SDK install prompts with per-signal defaults.
8. Implement auth-login prompt (TTY full flow + non-TTY URL-print).
9. Implement verification browsing task prompt + execution + result rendering.
10. Write `install.sh` bootstrap — wordmark, animation, background `uv tool install` with log capture, race reconciliation, absolute-path handoff.
11. Wire up the build-time inline of `play.sh` logic into `install.sh`.
12. Add installer publishing + SHA256 to release automation.
13. Add `/install.sh` and `/install.sh.sha256` redirects to `webapps/landing-page/vercel.json`.
14. Pick banner winner; promote to `yutori_banner.txt`; delete the loser.
15. Update README and landing-page `/api` install section to lead with the one-liner.

## File layout

```text
yutori-sdk-python/
├── assets/
│   └── terminal/
│       ├── yutori_banner_crop.txt    # v1 candidate A
│       ├── yutori_banner_custom.txt  # v1 candidate B
│       └── yutori_banner.txt         # winner symlink/copy after cutover
├── scripts/
│   └── build_install.sh              # regenerates install.sh (inlines play.sh logic)
├── yutori/
│   ├── auth/
│   │   └── flow.py                   # add non-blocking URL-print path
│   └── cli/
│       └── commands/
│           ├── auth.py               # add --print-url flag
│           └── install_ui.py         # implements `yutori __install_ui`
├── install.sh                        # generated artifact (committed)
├── install.sh.template               # human-authored source
└── .github/workflows/
    └── release.yml                   # publishes to PyPI + uploads install.sh + SHA256
```

