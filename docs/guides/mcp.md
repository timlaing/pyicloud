# MCP extension

The optional local Model Context Protocol (MCP) extension is under development.
Its current foundation exposes **local status only**, without Apple authentication
or account data tools. Calendar and Reminders adapters are not yet available.

Install it with `pip install "pyicloud[mcp]"`, or install both user-facing
extensions with `pip install "pyicloud[all]"`. The `all` extra does not install
development tooling. The standalone CLI extra remains `pyicloud[cli]`.

Use the interpreter/executable from your installation environment:

```console
.venv/bin/icloud-mcp --help
.venv/bin/icloud-mcp --version
.venv/bin/icloud-mcp
```

On Windows use `.venv\Scripts\icloud-mcp.exe`. With no arguments the server
communicates over stdin/stdout using MCP; it is not an interactive terminal shell.
Configure your host to launch that executable with no arguments. Do not supply
passwords or verification codes as host arguments or tool inputs.

`icloud_status` returns structured local status and performs no Apple requests.
No listener, write tools, account discovery, or implicit authentication is enabled.
Stdout is reserved for protocol traffic; diagnostics belong on stderr.

Core-only installations can use offline help/version, but serving requires the
`mcp` extra. Python 3.10–3.15 remain the supported interpreter range. See the
[compatibility guarantees](compatibility.md) for the distinction between offline
validation and live Apple-service compatibility.
