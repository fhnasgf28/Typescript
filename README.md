# MCP Transfer Node

FastAPI receiver + simple Web UI + MCP tools for direct server-to-server transfer via Cloudflare Tunnel. Runtime data lives in `/home/fhnasgf/mcp-transfer/`; inbox is `/home/fhnasgf/mcp-transfer/inbox/`. All file types are accepted as binary up to 50 MB.

## Install

```bash
cd /home/fhnasgf/mcp-transfer-node
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

## Runtime config

Copy `examples/peers.json` and `examples/destinations.json` into `/home/fhnasgf/mcp-transfer/config/`. Set env vars shown in `.env.example`. Expose `http://127.0.0.1:8787` through Cloudflare Tunnel to a subdomain such as `server-a.clipperyt.online`.

## Run

```bash
mcp-transfer-serve
```

MCP command: `/home/fhnasgf/mcp-transfer-node/.venv/bin/mcp-transfer-mcp`.
