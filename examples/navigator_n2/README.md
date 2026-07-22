# Navigator n2-preview with Cua

These gated-preview cookbooks run the same Yutori n2 agent loop against either the local macOS desktop or a disposable Cua cloud Sandbox. They use Chat Completions, retain the full in-memory trajectory, send model screenshots as full-frame JPEGs no larger than 1280×800, and execute coordinates against the corresponding native capture dimensions.

The environment is intentionally separate because Cua requires Python 3.12 or 3.13 while the Yutori SDK continues to support Python 3.9. It pins `cua==0.1.6`, `cua-cli==0.1.12`, Cua Driver `0.10.0`, and the reviewed Yutori n2 adapter from the user's Cua fork by full commit SHA.

```bash
cd examples/navigator_n2
uv sync --python 3.12 --locked
```

Authenticate Yutori with `yutori auth login` or `YUTORI_API_KEY`. The remote example also needs normal Cua cloud authentication on the host. Neither example copies the Yutori key into an execution environment.

## Local macOS

The locked Python package supplies the MCP client binary, but macOS permissions belong to the signed `CuaDriver.app` bundle. Install the matching app release and grant its Accessibility and Screen Recording permissions before running the cookbook:

```bash
CUA_DRIVER_RS_VERSION=0.10.0 /bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
~/.local/bin/cua-driver permissions grant
~/.local/bin/cua-driver permissions status --json
```

The adapter starts a desktop-scope Cua Driver session, captures the complete display at native pixel resolution, and sends only pixel actions. It does not use AX element indices, window IDs, cropping, or logical-point coordinate math; Cua Driver performs the native-pixel-to-Retina-event conversion. Restart the driver after changing permissions.

```bash
uv run python local_macos.py "Open Calculator and compute 17 * 23"
```

## Disposable Sandbox

The cloud cookbook creates an ephemeral Linux desktop, connects the same n2 loop, and destroys the environment through the async context manager even if the run fails or is interrupted.

```bash
uv run python remote_sandbox.py "Open Calculator and compute 17 * 23"
```

Both examples default to `computer_use_tools-20260708`. Pass `--batch` only when explicitly testing experimental `computer_batch` behavior. Confirmable actions prompt by default; `screenshot`, `wait`, `mouse_move`, and `scroll` do not. `--auto-approve` disables prompts and should be used only with trusted tasks. `--max-steps` defaults to 30.

The local integration follows the Cua Driver [interface contract](https://cua.ai/docs/reference/cua-driver/contracts), [MCP tools](https://cua.ai/docs/reference/cua-driver/mcp-tools), and [platform support](https://cua.ai/docs/reference/cua-driver/platform-support).
