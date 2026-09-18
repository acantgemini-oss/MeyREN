"""
MeyREN Multi-Protocol Configuration Engine
Adapted with full protocol diversity (VLESS WS, XHTTP, VMess WS, Trojan WS,
Shadowsocks, SOCKS5, HTTP Proxy, Hysteria 2, TUIC v5, WireGuard, and presets).
"""

import base64
import json
import os
from urllib.parse import quote
from typing import List, Dict, Optional, Any

from utils import CF_CLEAN_IPS

# ============================================================
# PROTOCOL DEFINITIONS & LABELS
# ============================================================

PROTOCOLS = (
    "vless-ws",
    "xhttp-packet-up",
    "xhttp-stream-up",
    "xhttp-stream-one",
    "vmess-ws",
    "trojan-ws",
    "shadowsocks",
    "socks5",
    "http",
    "hysteria2",
    "tuic",
    "wireguard",
    "highspeed-demo",
    "gaming-lite-demo",
)

PROTOCOL_LABELS = {
    "vless-ws": "VLESS WebSocket ⭐",
    "xhttp-packet-up": "XHTTP Packet Up",
    "xhttp-stream-up": "XHTTP Stream Up",
    "xhttp-stream-one": "XHTTP Stream One",
    "vmess-ws": "VMess WebSocket",
    "trojan-ws": "Trojan WebSocket",
    "shadowsocks": "Shadowsocks",
    "socks5": "SOCKS5",
    "http": "HTTP Proxy",
    "hysteria2": "Hysteria 2",
    "tuic": "TUIC v5",
    "wireguard": "WireGuard",
    "highspeed-demo": "HighSpeed Upload/Download (دمو)",
    "gaming-lite-demo": "Gaming Lite (دمو)",
}

PROTOCOL_ALIASES = {
    "vless": "vless-ws",
    "vmess": "vmess-ws",
    "trojan": "trojan-ws",
    "ss": "shadowsocks",
    "socks": "socks5",
    "hy2": "hysteria2",
    "hysteria": "hysteria2",
    "wg": "wireguard",
    "xhttp": "xhttp-packet-up",
}

DEFAULT_PROTOCOL = "vless-ws"

FINGERPRINTS = (
    "chrome",
    "firefox",
    "safari",
    "ios",
    "android",
    "edge",
    "random",
    "randomized",
)
DEFAULT_FINGERPRINT = "chrome"

DEFAULT_ALPN_BY_PROTOCOL = {
    "vless-ws": "http/1.1",
    "xhttp-packet-up": "h2,http/1.1",
    "xhttp-stream-up": "h2,http/1.1",
    "xhttp-stream-one": "h2,http/1.1",
    "vmess-ws": "http/1.1",
    "trojan-ws": "http/1.1",
    "tuic": "h3",
    "hysteria2": "h3",
}

DEFAULT_PORT = 443
MIN_PORT = 1
MAX_PORT = 65535


def normalize_protocol(protocol: Optional[str]) -> str:
    value = str(protocol or DEFAULT_PROTOCOL).strip().lower()
    value = PROTOCOL_ALIASES.get(value, value)
    return value if value in PROTOCOLS else DEFAULT_PROTOCOL


# ============================================================
# INDIVIDUAL LINK GENERATORS
# ============================================================

def generate_vless_ws_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    sni: Optional[str] = None,
    fingerprint: Optional[str] = None,
    alpn: Optional[str] = None,
    remark: str = "MeyREN-VLESS-WS",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/ws/{uuid}"
    fp = fingerprint if fingerprint in FINGERPRINTS else DEFAULT_FINGERPRINT
    alpn_val = alpn if alpn else DEFAULT_ALPN_BY_PROTOCOL.get("vless-ws", "http/1.1")
    params = {
        "encryption": "none",
        "security": "tls",
        "type": "ws",
        "host": domain,
        "path": path_val,
        "sni": sni_val,
        "fp": fp,
        "alpn": alpn_val,
    }
    query = "&".join(f"{k}={quote(str(v), safe=',/')}" for k, v in params.items())
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


def generate_vless_xhttp_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    mode: str = "packet-up",
    sni: Optional[str] = None,
    fingerprint: Optional[str] = None,
    alpn: Optional[str] = None,
    remark: str = "MeyREN-XHTTP",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/xhttp-siz10/{mode}/{uuid}"
    fp = fingerprint if fingerprint in FINGERPRINTS else DEFAULT_FINGERPRINT
    alpn_val = alpn if alpn else DEFAULT_ALPN_BY_PROTOCOL.get(f"xhttp-{mode}", "h2,http/1.1")
    params = {
        "encryption": "none",
        "security": "tls",
        "type": "xhttp",
        "mode": mode,
        "host": domain,
        "path": path_val,
        "sni": sni_val,
        "fp": fp,
        "alpn": alpn_val,
    }
    query = "&".join(f"{k}={quote(str(v), safe=',/')}" for k, v in params.items())
    return f"vless://{uuid}@{host}:{port}?{query}#{quote(remark)}"


def generate_vmess_ws_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    path: Optional[str] = None,
    sni: Optional[str] = None,
    fingerprint: Optional[str] = None,
    remark: str = "MeyREN-VMess-WS",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    path_val = path if path else f"/ws/{uuid}"
    fp = fingerprint if fingerprint in FINGERPRINTS else DEFAULT_FINGERPRINT
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
        "fp": fp,
    }
    encoded = base64.b64encode(json.dumps(vmess_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("utf-8")
    return f"vmess://{encoded}"


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
    query = "&".join(f"{k}={quote(str(v), safe=',/')}" for k, v in params.items())
    return f"trojan://{password}@{host}:{port}?{query}#{quote(remark)}"


def generate_shadowsocks_link(
    password: str,
    method: str = "aes-256-gcm",
    domain: str = "localhost",
    port: int = 443,
    connect_host: Optional[str] = None,
    remark: str = "MeyREN-Shadowsocks",
) -> str:
    host = connect_host if connect_host else domain
    userpass = f"{method}:{password}"
    userinfo = base64.urlsafe_b64encode(userpass.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"ss://{userinfo}@{host}:{port}#{quote(remark)}"


def generate_socks5_link(
    uuid: str,
    domain: str,
    port: int = 1080,
    connect_host: Optional[str] = None,
    remark: str = "MeyREN-SOCKS5",
) -> str:
    host = connect_host if connect_host else domain
    return f"socks5://{uuid}:{uuid}@{host}:{port}#{quote(remark)}"


def generate_http_proxy_link(
    uuid: str,
    domain: str,
    port: int = 8080,
    connect_host: Optional[str] = None,
    remark: str = "MeyREN-HTTP-Proxy",
) -> str:
    host = connect_host if connect_host else domain
    return f"http://{uuid}:{uuid}@{host}:{port}#{quote(remark)}"


def generate_hysteria2_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    sni: Optional[str] = None,
    obfs: Optional[str] = None,
    remark: str = "MeyREN-Hysteria2",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    q = f"sni={quote(sni_val)}&insecure=0"
    if obfs:
        q += f"&obfs={quote(obfs)}"
    return f"hysteria2://{uuid}@{host}:{port}/?{q}#{quote(remark)}"


def generate_tuic_link(
    uuid: str,
    domain: str,
    port: int = 443,
    connect_host: Optional[str] = None,
    sni: Optional[str] = None,
    alpn: str = "h3",
    remark: str = "MeyREN-TUIC",
) -> str:
    host = connect_host if connect_host else domain
    sni_val = sni if sni else domain
    return f"tuic://{uuid}:{uuid}@{host}:{port}?sni={quote(sni_val)}&alpn={quote(alpn)}#{quote(remark)}"


def generate_wireguard_link(
    uuid: str,
    domain: str,
    port: int = 51820,
    connect_host: Optional[str] = None,
    public_key: Optional[str] = None,
    remark: str = "MeyREN-WireGuard",
) -> str:
    host = connect_host if connect_host else domain
    pk = public_key if public_key else uuid
    return f"wireguard://{uuid}@{host}:{port}?publicKey={quote(pk)}#{quote(remark)}"


# ============================================================
# UNIFIED PROTOCOL ROUTER
# ============================================================

def generate_protocol_link(
    protocol: str,
    uuid: str,
    domain: str,
    port: int = 443,
    remark: str = "MeyREN",
    connect_host: Optional[str] = None,
    fingerprint: Optional[str] = None,
    alpn: Optional[str] = None,
    sni: Optional[str] = None,
    public_key: Optional[str] = None,
) -> str:
    """Dispatches generation to the correct protocol link format."""
    proto = normalize_protocol(protocol)
    label = remark or "MeyREN"

    if proto == "vless-ws":
        return generate_vless_ws_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            fingerprint=fingerprint,
            alpn=alpn,
            remark=label,
        )

    if proto.startswith("xhttp-"):
        mode = proto.replace("xhttp-", "")
        return generate_vless_xhttp_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            mode=mode,
            sni=sni,
            fingerprint=fingerprint,
            alpn=alpn,
            remark=label,
        )

    if proto == "vmess-ws":
        return generate_vmess_ws_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            fingerprint=fingerprint,
            remark=label,
        )

    if proto == "trojan-ws":
        return generate_trojan_ws_link(
            password=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            remark=label,
        )

    if proto == "shadowsocks":
        ss_method = os.getenv("SS_METHOD", "aes-256-gcm")
        return generate_shadowsocks_link(
            password=uuid,
            method=ss_method,
            domain=domain,
            port=port,
            connect_host=connect_host,
            remark=label,
        )

    if proto == "socks5":
        return generate_socks5_link(
            uuid=uuid,
            domain=domain,
            port=port if port != 443 else 1080,
            connect_host=connect_host,
            remark=label,
        )

    if proto == "http":
        return generate_http_proxy_link(
            uuid=uuid,
            domain=domain,
            port=port if port != 443 else 8080,
            connect_host=connect_host,
            remark=label,
        )

    if proto == "hysteria2":
        return generate_hysteria2_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            remark=label,
        )

    if proto == "tuic":
        return generate_tuic_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            alpn=alpn or "h3",
            remark=label,
        )

    if proto == "wireguard":
        return generate_wireguard_link(
            uuid=uuid,
            domain=domain,
            port=port if port != 443 else 51820,
            connect_host=connect_host,
            public_key=public_key,
            remark=label,
        )

    if proto == "highspeed-demo":
        return generate_vless_xhttp_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            mode="stream-up",
            sni=sni,
            fingerprint=fingerprint,
            alpn="h2,http/1.1",
            remark=f"{label} [HighSpeed]",
        )

    if proto == "gaming-lite-demo":
        return generate_hysteria2_link(
            uuid=uuid,
            domain=domain,
            port=port,
            connect_host=connect_host,
            sni=sni,
            obfs="salamander",
            remark=f"{label} [Gaming-Lite]",
        )

    # Fallback to standard VLESS WS
    return generate_vless_ws_link(uuid, domain, port, connect_host=connect_host, remark=label)


# ============================================================
# BUNDLED CONFIGURATIONS FOR A LINK
# ============================================================

def generate_all_protocols_for_link(link: dict, domain: str, port: int = 443) -> List[Dict[str, Any]]:
    """Generates an array of all protocol endpoints configured for a given link."""
    uuid = link["uuid"]
    label = link.get("label") or "MeyREN"
    chosen_proto = link.get("protocol", DEFAULT_PROTOCOL)
    configs = []

    # 1. User's primary configured protocol
    primary_uri = generate_protocol_link(
        protocol=chosen_proto,
        uuid=uuid,
        domain=domain,
        port=link.get("port", port),
        remark=label,
        fingerprint=link.get("fingerprint"),
        alpn=link.get("alpn"),
    )
    configs.append({
        "id": chosen_proto,
        "protocol": chosen_proto,
        "protocol_label": PROTOCOL_LABELS.get(chosen_proto, chosen_proto),
        "name": f"{label} ({PROTOCOL_LABELS.get(chosen_proto, chosen_proto)})",
        "uri": primary_uri,
        "primary": True,
    })

    # 2. VLESS WebSocket (Industry standard fallback)
    if chosen_proto != "vless-ws":
        configs.append({
            "id": "vless-ws",
            "protocol": "vless-ws",
            "protocol_label": "VLESS WebSocket",
            "name": f"{label} · VLESS WS",
            "uri": generate_vless_ws_link(uuid, domain, port, remark=f"{label} [WS]"),
            "primary": False,
        })

    # 3. VLESS XHTTP Packet-Up (Anti-filter DPI bypass)
    if chosen_proto != "xhttp-packet-up":
        configs.append({
            "id": "xhttp-packet-up",
            "protocol": "xhttp-packet-up",
            "protocol_label": "XHTTP Packet Up",
            "name": f"{label} · XHTTP Packet-Up",
            "uri": generate_vless_xhttp_link(uuid, domain, port, mode="packet-up", remark=f"{label} [XHTTP-Packet]"),
            "primary": False,
        })

    # 4. Clean IP Alternate (if available)
    clean_ips = link.get("clean_ips") or CF_CLEAN_IPS
    if clean_ips:
        clean_ip = clean_ips[0]
        configs.append({
            "id": "vless-clean-ip",
            "protocol": "vless-ws",
            "protocol_label": "Clean IP Alternate",
            "name": f"{label} · Clean IP ({clean_ip})",
            "uri": generate_vless_ws_link(uuid, domain, port, connect_host=clean_ip, remark=f"{label} [Clean-IP]"),
            "primary": False,
        })

    # 5. Trojan WebSocket
    if chosen_proto != "trojan-ws":
        configs.append({
            "id": "trojan-ws",
            "protocol": "trojan-ws",
            "protocol_label": "Trojan WebSocket",
            "name": f"{label} · Trojan WS",
            "uri": generate_trojan_ws_link(uuid, domain, port, remark=f"{label} [Trojan]"),
            "primary": False,
        })

    # 6. Shadowsocks
    if chosen_proto != "shadowsocks":
        configs.append({
            "id": "shadowsocks",
            "protocol": "shadowsocks",
            "protocol_label": "Shadowsocks AEAD",
            "name": f"{label} · Shadowsocks",
            "uri": generate_shadowsocks_link(uuid, domain=domain, port=port, remark=f"{label} [SS]"),
            "primary": False,
        })

    # 7. Hysteria 2
    if chosen_proto != "hysteria2":
        configs.append({
            "id": "hysteria2",
            "protocol": "hysteria2",
            "protocol_label": "Hysteria 2",
            "name": f"{label} · Hysteria 2",
            "uri": generate_hysteria2_link(uuid, domain, port, remark=f"{label} [Hy2]"),
            "primary": False,
        })

    # 8. TUIC
    if chosen_proto != "tuic":
        configs.append({
            "id": "tuic",
            "protocol": "tuic",
            "protocol_label": "TUIC v5",
            "name": f"{label} · TUIC",
            "uri": generate_tuic_link(uuid, domain, port, remark=f"{label} [TUIC]"),
            "primary": False,
        })

    # 9. WireGuard
    if chosen_proto != "wireguard":
        configs.append({
            "id": "wireguard",
            "protocol": "wireguard",
            "protocol_label": "WireGuard",
            "name": f"{label} · WireGuard",
            "uri": generate_wireguard_link(uuid, domain, 51820, remark=f"{label} [WG]"),
            "primary": False,
        })

    return configs


# ============================================================
# SING-BOX & CLASH EXPORTERS
# ============================================================

def generate_singbox_profile(link: dict, domain: str, port: int = 443) -> dict:
    """Returns Sing-box JSON outbound configuration."""
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
                    "path": f"/xhttp-siz10/packet-up/{uuid}",
                },
            },
            {
                "type": "trojan",
                "tag": f"{label}-trojan-ws",
                "server": domain,
                "server_port": port,
                "password": uuid,
                "tls": {
                    "enabled": True,
                    "server_name": domain,
                },
                "transport": {
                    "type": "ws",
                    "path": f"/ws/{uuid}",
                },
            },
            {
                "type": "shadowsocks",
                "tag": f"{label}-ss",
                "server": domain,
                "server_port": port,
                "method": "aes-256-gcm",
                "password": uuid,
            },
            {
                "type": "hysteria2",
                "tag": f"{label}-hy2",
                "server": domain,
                "server_port": port,
                "password": uuid,
                "tls": {
                    "enabled": True,
                    "server_name": domain,
                    "insecure": False,
                },
            },
        ],
    }


def generate_clash_profile(link: dict, domain: str, port: int = 443) -> str:
    """Returns Clash / Clash Meta proxy configuration in YAML format."""
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

  - name: "{label} [Trojan-WS]"
    type: trojan
    server: {domain}
    port: {port}
    password: {uuid}
    network: ws
    sni: {domain}
    ws-opts:
      path: "/ws/{uuid}"

  - name: "{label} [Shadowsocks]"
    type: ss
    server: {domain}
    port: {port}
    cipher: aes-256-gcm
    password: {uuid}

  - name: "{label} [Hysteria2]"
    type: hysteria2
    server: {domain}
    port: {port}
    password: {uuid}
    sni: {domain}
    skip-cert-verify: false
"""
