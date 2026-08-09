# garmin-mcp

Local MCP server exposing Garmin Connect data (sleep, HR, HRV, stress,
VO2max, activities) to Claude, with correlation and inferred "night out"
detection.

## Setup

1. `python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill in `GARMIN_EMAIL` / `GARMIN_PASSWORD`
4. Run tests: `pytest -v`

## Registering with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp/venv/bin/python",
      "args": ["/absolute/path/to/garmin-mcp/server.py"],
      "env": {
        "GARMIN_EMAIL": "your_email@example.com",
        "GARMIN_PASSWORD": "your_password"
      }
    }
  }
}
```

Restart Claude Desktop. It launches `server.py` as a local subprocess and
talks to it over stdio (stdin/stdout, JSON-RPC) — no network port, no
separate auth layer, since both processes run under your own user account
on the same machine.

## Usage

1. Ask Claude to run `sync_garmin_data` for a date range — this logs into
   Garmin and populates the local SQLite cache at `~/.garmin-mcp/data.db`.
2. Ask Claude to correlate metrics, e.g. "correlate my sleep duration with
   resting heart rate over the last 30 days" — Claude calls `correlate`.
3. Ask Claude to find inferred nights out, or log one manually via
   `log_night_out`.

## Beyond stdio: HTTP/remote deployment (notes)

This server uses stdio because it's simplest for a single local user: no
port, no auth to manage, Claude Desktop owns the subprocess lifecycle. If
you ever needed the server reachable from another machine or by multiple
MCP clients at once, MCP also supports an HTTP transport (`streamable-http`
in the SDK) — the server would then bind a port, need its own auth (MCP
doesn't provide this for you), and run as a long-lived process instead of
a per-session subprocess. Not needed for this project; noted here as the
next step if requirements change.
