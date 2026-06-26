# MCP Transfer Agent Quickstart

Dokumen singkat ini untuk agent yang hanya perlu mengirim file dari server ini ke server lain lewat mcp-transfer-node. Tidak perlu membaca README penuh jika tugasnya hanya transfer file.

## Peran Server Ini

- Server ini berperan sebagai sender, dikenal sebagai server-b pada environment mcp-transfer-node.
- Project utama: /home/adminftp/farhan/mcp-transfer-node
- Runtime/config data: /home/adminftp/farhan/mcp-transfer
- Environment file service: /home/adminftp/.config/mcp-transfer-node/server-b.env
- Jangan membuka, cat, echo, atau mencetak isi environment file karena berisi secret.

## Kapan Dipakai

Pakai tool ini ketika user meminta mengirim file, paket ZIP, dokumentasi, report, atau handoff dari server ini ke alias tujuan seperti server-a. Kirim memakai alias, bukan raw destination URL.

## File Penting

- src/mcp_transfer_node/mcp_server.py: fungsi send_file_to_destination dan tool MCP send_file.
- src/mcp_transfer_node/config.py: loader settings dan destinations.
- /home/adminftp/farhan/mcp-transfer/config/destinations.json: daftar alias tujuan.
- /home/adminftp/.config/mcp-transfer-node/server-b.env: env runtime dan token destination.
- /home/adminftp/farhan/mcp-transfer-node/out: lokasi aman untuk paket output sebelum dikirim.

## Cek Alias Tujuan Tanpa Membuka Secret

Jalankan dari target server sebagai adminftp:

    cd /home/adminftp/farhan/mcp-transfer-node
    python3 - <<"PY"
    import json
    from pathlib import Path
    cfg = Path("/home/adminftp/farhan/mcp-transfer/config/destinations.json")
    data = json.loads(cfg.read_text())
    for item in data.get("destinations", []):
        print(item.get("name", ""))
    PY

Output cukup nama alias saja. Jangan print URL atau token env jika tidak diperlukan.

## Kirim File Ke Alias Tujuan

Template aman untuk mengirim file. Ganti FILE_PATH dan DESTINATION_ALIAS saja.

    set -a
    . /home/adminftp/.config/mcp-transfer-node/server-b.env
    set +a
    cd /home/adminftp/farhan/mcp-transfer-node
    PYTHONPATH=src .venv/bin/python - <<"PY"
    import asyncio
    import logging
    from pathlib import Path
    from mcp_transfer_node.config import load_settings
    from mcp_transfer_node.mcp_server import send_file_to_destination

    logging.getLogger("httpx").setLevel(logging.WARNING)

    FILE_PATH = "/home/adminftp/farhan/mcp-transfer-node/out/example.zip"
    DESTINATION_ALIAS = "server-a"
    NOTE = "handoff package"

    async def main():
        path = Path(FILE_PATH)
        if not path.exists() or not path.is_file():
            raise SystemExit("file_not_found")
        result = await send_file_to_destination(path, DESTINATION_ALIAS, NOTE, load_settings())
        print("transfer_ok=true")
        print("file_size=" + str(path.stat().st_size))
        print("stored_filename_present=" + str(bool(result.get("storedFilename"))).lower())

    asyncio.run(main())
    PY

Jika output transfer_ok=true, file sudah diterima oleh destination alias.

## Contoh Untuk Paket Code-Server Mobile Controls

Paket yang pernah dibuat:

    /home/adminftp/farhan/mcp-transfer-node/out/code-server-mobile-controls-handoff-20260626.zip

Kirim ke server-a dengan template di atas dan set:

    FILE_PATH = "/home/adminftp/farhan/mcp-transfer-node/out/code-server-mobile-controls-handoff-20260626.zip"
    DESTINATION_ALIAS = "server-a"
    NOTE = "code-server mobile controls handoff"

## Verifikasi Sebelum Transfer

Untuk ZIP atau paket handoff, minimal cek:

    test -f /path/file.zip
    python3 -m zipfile -t /path/file.zip
    ls -lh /path/file.zip

Untuk paket yang punya checksum:

    cd /path/folder-paket
    sha256sum -c checksums.sha256

## Troubleshooting Cepat

- unknown destination: alias belum ada di destinations.json.
- missing token env: environment file belum di-source atau token env tidak cocok dengan destinations.json.
- file_not_found: path file salah atau file belum dibuat.
- file exceeds limit: ukuran file melewati limit konfigurasi MCP_TRANSFER_MAX_FILE_MB.
- destination rejected upload: cek service penerima di server tujuan atau token peer.

## Aturan Keamanan Untuk Agent

- Jangan print token, isi env, credential, atau raw URL tujuan.
- Jangan mengirim file credential, .env, private key, atau dump rahasia kecuali user eksplisit meminta dan policy mengizinkan.
- Laporkan status cukup dengan alias tujuan dan transfer_ok.
- Simpan paket sementara di /home/adminftp/farhan/mcp-transfer-node/out supaya mudah dilacak.
- Jika transfer gagal, laporkan error singkat tanpa menyalin isi config atau secret.
