import base64
import json
from urllib.parse import quote
from typing import List, Dict, Optional

from utils import CF_CLEAN_IPS


def generate_vless_ws_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    sni: Optional[str] = None,
    remark: str = "MeyREN-VLESS-WS",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/ws/{uuid}"
    params = {
        "encryption": "none",
        "security": "tls",
        "type": "ws",
        "host": domain,
        "path": path_val,
        "sni": sni_val,
        "fp": "chrome",
        "alpn": "http/1.1",
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


def generate_vless_xhttp_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    mode: str = "packet-up",
    sni: Optional[str] = None,
    remark: str = "MeyREN-XHTTP",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/xhttp/{uuid}"
    params = {
        "encryption": "none",
        "security": "tls",
        "type": "xhttp",
        "host": domain,
        "path": path_val,
        "mode": mode,
        "sni": sni_val,
        "fp": "chrome",
        "alpn": "h2,http/1.1",
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


def generate_trojan_ws_link(
    password: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    sni: Optional[str] = None,
    remark: str = "MeyREN-Trojan-WS",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/ws/{password}"
    params = {
        "security": "tls",
        "type": "ws",
        "host": domain,
        "path": path_val,
        "sni": sni_val,
        "fp": "chrome",
        "alpn": "http/1.1",
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"trojan://{password}@{host}:{port}?{query}#{quote(remark)}"


def generate_vmess_ws_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    sni: Optional[str] = None,
    remark: str = "MeyREN-VMess-WS",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/ws/{uuid}"
    vmess_dict = {
        "v": "2",
        "ps": remark,
        "add": host,
        "port": str(port),
        "id": uuid,
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": domain,
        "path": path_val,
        "tls": "tls",
        "sni": sni_val,
        "alpn": "http/1.1",
        "fp": "chrome",
    }
    encoded = base64.b64encode(json.dumps(vmess_dict).encode("utf-8")).decode("utf-8")
    return f"vmess://{encoded}"


def generate_shadowsocks_link(
    password: str,
    method: str = "chacha20-ietf-poly1305",
    domain: str = "localhost",
    port: int = 443,
    remark: str = "MeyREN-Shadowsocks",
) -> str:
    userpass = f"{method}:{password}"
    encoded = base64.urlsafe_b64encode(userpass.encode()).decode().rstrip("=")
    return f"ss://{encoded}@{domain}:{port}#{quote(remark)}"


def generate_all_protocols_for_link(link: dict, domain: str, port: int = 443) -> List[Dict]:
    """Generates all supported and verified configurations for a single client link."""
    uuid = link["uuid"]
    label = link.get("label") or "MeyREN"
    configs = []

    # 1. Primary VLESS WebSocket
    configs.append({
        "id": "vless-ws",
        "protocol": "VLESS",
        "transport": "WebSocket",
        "name": f"{label} · VLESS WS",
        "uri": generate_vless_ws_link(uuid, domain, port, remark=f"{label} [WS]"),
        "primary": True,
    })

    # 2. VLESS XHTTP Packet-Up (Ultra-resilient DPI bypass)
    configs.append({
        "id": "xhttp-packet",
        "protocol": "VLESS",
        "transport": "XHTTP (packet-up)",
        "name": f"{label} · XHTTP Packet-Up",
        "uri": generate_vless_xhttp_link(uuid, domain, port, mode="packet-up", remark=f"{label} [XHTTP-Packet]"),
        "primary": False,
    })

    # 3. VLESS XHTTP Stream-Up (High-bandwidth streaming)
    configs.append({
        "id": "xhttp-stream",
        "protocol": "VLESS",
        "transport": "XHTTP (stream-up)",
        "name": f"{label} · XHTTP Stream-Up",
        "uri": generate_vless_xhttp_link(uuid, domain, port, mode="stream-up", remark=f"{label} [XHTTP-Stream]"),
        "primary": False,
    })

    # 4. Clean IP Alternate (Uses top Cloudflare clean IP with TLS SNI)
    if CF_CLEAN_IPS:
        clean_ip = CF_CLEAN_IPS[0]
        configs.append({
            "id": "vless-clean-ip",
            "protocol": "VLESS",
            "transport": "WebSocket (Clean IP)",
            "name": f"{label} · Clean IP ({clean_ip})",
            "uri": generate_vless_ws_link(uuid, domain, port, connect_host=clean_ip, remark=f"{label} [Clean-IP]"),
            "primary": False,
        })

    # 5. Trojan WebSocket (Using uuid as trojan password)
    configs.append({
        "id": "trojan-ws",
        "protocol": "Trojan",
        "transport": "WebSocket",
        "name": f"{label} · Trojan WS",
        "uri": generate_trojan_ws_link(uuid, domain, port, remark=f"{label} [Trojan]"),
        "primary": False,
    })

    return configs


def generate_singbox_profile(link: dict, domain: str, port: int = 443) -> dict:
    """Returns standard Sing-box outbound configuration object."""
    uuid = link["uuid"]
    label = link.get("label") or "MeyREN"
    return {
        "version": 1,
        "outbounds": [
            {
                "type": "vless",
                "tag": f"{label}-vless-ws",
                "server": domain,
                "server_port": port,
                "uuid": uuid,
                "tls": {
                    "enabled": True,
                    "server_name": domain,
                    "utls": {"enabled": True, "fingerprint": "chrome"},
                },
                "transport": {
                    "type": "ws",
                    "path": f"/ws/{uuid}",
                    "headers": {"Host": domain},
                },
            },
            {
                "type": "vless",
                "tag": f"{label}-xhttp",
                "server": domain,
                "server_port": port,
                "uuid": uuid,
                "tls": {
                    "enabled": True,
                    "server_name": domain,
                    "utls": {"enabled": True, "fingerprint": "chrome"},
                },
                "transport": {
                    "type": "http",
                    "host": [domain],
                    "path": f"/xhttp/{uuid}",
                },
            },
        ],
    }


def generate_clash_profile(link: dict, domain: str, port: int = 443) -> str:
    """Returns standard Clash / Clash Meta proxy configuration YAML."""
    uuid = link["uuid"]
    label = link.get("label") or "MeyREN"
    return f"""proxies:
  - name: "{label} [VLESS-WS]"
    type: vless
    server: {domain}
    port: {port}
    uuid: {uuid}
    network: ws
    tls: true
    udp: true
    sni: {domain}
    client-fingerprint: chrome
    ws-opts:
      path: "/ws/{uuid}"
      headers:
        Host: {domain}
"""
