#!/usr/bin/env python3
"""
Continuous WAF traffic generator — pure HTTP only.
Sends a realistic mix of legit, bot, and attack traffic to the WAF Gateway VS.
Traffic volume is modulated sinusoidally so the time-series chart shows peaks/troughs.
No synthetic OTel injection — all dashboard data comes from real WAF decisions.
"""

import os
import random
import time
import math
import logging
import urllib.parse
import http.client
import socket
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("waf-traffic-gen")

TARGETS = [t.strip() for t in os.environ.get(
    "WAF_TARGETS",
    "http://11.11.11.201:9080"
).split(",") if t.strip()]

ATTACK_RATIO = int(os.environ.get("ATTACK_RATIO", "55"))
# Base RPS — actual rate oscillates between 0.5x and 3x over a 10-minute period
RPS_BASE = float(os.environ.get("RPS", "8"))

LEGIT_PATHS = [
    "/", "/index.html", "/about", "/api/health", "/api/users", "/api/products",
    "/api/orders", "/search?q=shoes", "/search?q=laptop", "/login", "/dashboard",
    "/profile", "/api/v1/catalog", "/api/v1/inventory?category=electronics",
    "/api/v1/inventory?category=clothing", "/static/js/app.js", "/static/css/main.css",
    "/favicon.ico", "/robots.txt", "/sitemap.xml", "/api/metrics", "/health",
    "/?page=1", "/?page=2&sort=price", "/blog/post-1", "/blog/post-2",
    "/api/orders/123", "/checkout", "/api/v2/users?limit=10", "/api/search?q=phone",
    "/contact", "/pricing", "/docs/api", "/api/v1/categories", "/cart",
]

LEGIT_METHODS = ["GET"] * 7 + ["POST"] * 2 + ["PUT"] * 1

# Bot user agents trigger VIOL_BOT_CLIENT (alarm-only) → ALERTED events
BOT_USER_AGENTS = [
    "sqlmap/1.7.8#stable",
    "Nikto/2.1.6",
    "masscan/1.3.2",
    "ZmEu",
    "w3af.org",
    "python-requests/2.31.0",
    "Go-http-client/1.1",
    "curl/7.88.1",
    "Wget/1.21",
    "WhatWeb/0.5.5",
    "dirbuster",
    "WPScan v3.8.22",
    "Nmap Scripting Engine; https://nmap.org/book/nse.html",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0)",
    "Baiduspider+(+http://www.baidu.com/search/spider.htm)",
]

# Alarm-only paths — WAF alarmed but not blocked → ALERTED outcome
# These trigger VIOL_HTTP_PROTOCOL (sub-violation "Host header with IP") which is alarm-only
ALARM_ONLY_PAYLOADS = [
    ("Bot Scanner",    "GET",  "/", "sqlmap/1.7.8#stable"),
    ("Bot Scanner",    "GET",  "/api/users", "Nikto/2.1.6"),
    ("Bot Scanner",    "GET",  "/admin", "masscan/1.3.2"),
    ("Bot Scanner",    "GET",  "/wp-login.php", "WPScan v3.8.22"),
    ("Bot Scanner",    "GET",  "/", "ZmEu"),
    ("Bot Scanner",    "GET",  "/", "dirbuster"),
    ("Bot Scanner",    "POST", "/login", "python-requests/2.31.0"),
    ("Bot Scanner",    "GET",  "/robots.txt", "AhrefsBot"),
    ("Bot Scanner",    "GET",  "/sitemap.xml", "Baiduspider"),
    ("Bot Scanner",    "GET",  "/.well-known/security.txt", "Nmap Scripting Engine"),
    # Large payload below WAF block threshold but above alarm — ALERTED
    ("Large Payload",  "POST", "/api/upload", None),
    # Invalid content-type headers — alarm only
    ("Protocol Abuse", "GET",  "/?q=normal-search", "curl/7.88.1"),
]

ATTACK_PAYLOADS = [
    ("SQL Injection",         "GET",    "/?id=1'+OR+'1'='1"),
    ("SQL Injection",         "GET",    "/?id=1+UNION+SELECT+*+FROM+users--"),
    ("SQL Injection",         "GET",    "/?search=';+DROP+TABLE+users;--"),
    ("SQL Injection",         "POST",   "/login"),
    ("SQL Injection",         "GET",    "/?user=admin'--"),
    ("SQL Injection",         "GET",    "/?id=1+AND+1=1"),
    ("SQL Injection",         "GET",    "/api/users?id=1+OR+1=1"),
    ("SQL Injection",         "GET",    "/?name=a'+WAITFOR+DELAY+'0:0:5'--"),
    ("XSS",                   "GET",    "/?search=<script>alert(document.cookie)</script>"),
    ("XSS",                   "GET",    "/?q=<img+src=x+onerror=alert(1)>"),
    ("XSS",                   "GET",    "/?redirect=javascript:alert(1)"),
    ("XSS",                   "POST",   "/comment"),
    ("XSS",                   "GET",    "/?name=<svg+onload=alert(1)>"),
    ("Path Traversal",        "GET",    "/?file=../../../../etc/passwd"),
    ("Path Traversal",        "GET",    "/download?name=../../../etc/shadow"),
    ("Path Traversal",        "GET",    "/?path=..%2F..%2F..%2Fetc%2Fpasswd"),
    ("Command Injection",     "GET",    "/?cmd=;cat+/etc/passwd"),
    ("Command Injection",     "GET",    "/api/ping?host=localhost;id"),
    ("Command Injection",     "GET",    "/?exec=ls+%2Fvar"),
    ("Remote Code Execution", "GET",    "/?url=http://169.254.169.254/latest/meta-data/"),
    ("Remote Code Execution", "GET",    "/api/run?code=__import__%28os%29.system%28id%29"),
    ("Remote Code Execution", "POST",   "/eval"),
    ("SSRF",                  "GET",    "/?url=http://internal-api:8080/secrets"),
    ("SSRF",                  "GET",    "/?redirect=http://10.0.0.1/admin"),
    ("SSRF",                  "GET",    "/proxy?target=file:///etc/passwd"),
    ("Scanning",              "GET",    "/.env"),
    ("Scanning",              "GET",    "/wp-admin/"),
    ("Scanning",              "GET",    "/phpmyadmin/"),
    ("Scanning",              "GET",    "/.git/config"),
    ("Scanning",              "GET",    "/admin"),
    ("Scanning",              "GET",    "/config.php"),
    ("Scanning",              "GET",    "/backup.sql"),
    ("Method Abuse",          "DELETE", "/api/users/1"),
    ("Method Abuse",          "TRACE",  "/"),
    # From the provided test script — covering more attack categories
    ("LFI",                   "GET",    "/?page=../../../../etc/passwd"),
    ("LFI",                   "GET",    "/?file=../../../etc/shadow"),
    ("XSS",                   "POST",   "/comment"),        # JSON body with XSS
    ("XSS",                   "GET",    "/?param=%3Cscript%3Ealert%28%27xss%27%29%3C%2Fscript%3E"),
    ("Command Injection",     "POST",   "/api/ping"),       # cmd=ls | cat /etc/passwd
    ("Information Leakage",   "GET",    "/.git/config"),
    ("Information Leakage",   "GET",    "/api/run"),
    ("Scanning",              "GET",    "/wp-login.php"),
    ("Scanning",              "GET",    "/.well-known/security.txt"),
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "curl/7.88.1", "python-requests/2.31.0", "Go-http-client/1.1",
    "sqlmap/1.7.8#stable", "Nikto/2.1.6", "masscan/1.3",
    "Mozilla/5.0 (compatible; Googlebot/2.1)", "Mozilla/5.0 (compatible; bingbot/2.0)",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
    "Mozilla/5.0 (Android 13; Mobile; rv:109.0) Gecko/114.0 Firefox/114.0",
]

# 70% chance of picking from a fixed pool so repeat attacker IPs accumulate hits
_ATTACKER_POOL = [
    "185.234.218.42", "45.142.212.115", "91.108.56.182", "5.188.86.172",
    "31.184.196.88", "198.235.24.155", "212.102.34.87", "162.55.243.171",
    "195.123.240.29", "89.248.165.142", "103.21.76.33", "104.16.231.226",
    "172.67.104.58", "185.234.7.115", "45.142.94.121", "91.108.204.221",
    "31.184.105.44", "198.235.141.125", "212.102.6.97", "162.55.230.206",
    "45.142.48.122", "195.123.125.190", "185.234.186.17", "104.16.42.149",
    "172.67.8.200", "89.248.36.110", "91.108.110.28", "31.184.226.1",
    "5.188.32.213", "104.16.77.98",
]

def _random_ip() -> str:
    if random.randint(1, 10) <= 7:
        return random.choice(_ATTACKER_POOL)
    prefixes = ["185.234.", "45.142.", "91.108.", "5.188.", "31.184.", "198.235."]
    return random.choice(prefixes) + str(random.randint(1, 254)) + "." + str(random.randint(1, 254))

def _legit_ip() -> str:
    prefixes = ["98.234.", "76.120.", "71.198.", "108.14.", "67.189.", "50.77."]
    return random.choice(prefixes) + str(random.randint(1, 254)) + "." + str(random.randint(1, 254))


def _parse_target(target: str):
    parsed = urllib.parse.urlparse(target)
    return parsed.hostname, parsed.port or 80


def _current_rps() -> float:
    """Modulate RPS sinusoidally over a 10-min period between 0.5x and 3x base."""
    period = 600  # 10-minute oscillation
    phase = (time.time() % period) / period * 2 * math.pi
    # Add a second faster 3-min oscillation for more realistic spikiness
    fast_phase = (time.time() % 180) / 180 * 2 * math.pi
    factor = 1.75 + 1.25 * math.sin(phase) + 0.5 * math.sin(fast_phase)
    return max(0.5, RPS_BASE * factor)


def send_request(target: str, method: str, path: str, is_attack: bool,
                 attack_type: str = "", ua_override=None) -> int:
    """Send raw HTTP request so attack chars are NOT percent-encoded by Python."""
    ua = ua_override or random.choice(USER_AGENTS)
    src_ip = _random_ip() if is_attack else _legit_ip()

    body_bytes = None
    content_type = "application/x-www-form-urlencoded"
    if method in ("POST", "PUT"):
        if "login" in path:
            raw = "username=admin&password=' OR '1'='1" if is_attack else "username=testuser&password=securepass"
        elif "comment" in path:
            raw = '{"username": "admin", "data": "<script>alert(1)</script>"}'
            content_type = "application/json"
        elif "eval" in path:
            raw = "<script>alert(1)</script>" if is_attack else "Great product!"
        elif "upload" in path:
            # Large payload — triggers alarm-only if under block threshold
            raw = "A" * 8000
        elif "ping" in path:
            raw = "cmd=ls | cat /etc/passwd"
        else:
            raw = "{}"
        body_bytes = raw.encode()

    label = f"ATTACK({attack_type})" if is_attack else "LEGIT"
    host, port = _parse_target(target)

    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        headers = {
            "User-Agent": ua,
            "X-Forwarded-For": src_ip,
            "Accept": "text/html,application/json,*/*",
            "Host": f"{host}:{port}",
        }
        if body_bytes:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body_bytes))
        conn.request(method, path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        code = resp.status
        resp.read()
        conn.close()
    except (http.client.HTTPException, socket.timeout, OSError, ConnectionResetError) as e:
        log.warning("request failed: %s %s%s: %s", method, target, path, e)
        return 0

    log.info("%s %s %s%s → HTTP %d  [src=%s]", label, method, target, path, code, src_ip)
    return code


def main():
    log.info("WAF traffic generator starting — modulated RPS, ALERTED traffic enabled")
    log.info("Targets: %s", TARGETS)
    log.info("Base RPS: %.1f  Attack ratio: %d%%", RPS_BASE, ATTACK_RATIO)

    stats = {"legit": 0, "attack": 0, "alarm": 0}
    last_report = time.time()

    while True:
        rps = _current_rps()
        sleep_between = 1.0 / rps

        target = random.choice(TARGETS)

        # Decide traffic category:
        # 55% attack (REJECTED), 10% alarm-only bot (ALERTED), 35% legit (PASSED)
        roll = random.randint(1, 100)
        if roll <= ATTACK_RATIO:
            # Blocked attack
            attack_type, method, path = random.choice(ATTACK_PAYLOADS)
            send_request(target, method, path, is_attack=True, attack_type=attack_type)
            stats["attack"] += 1
        elif roll <= ATTACK_RATIO + 10:
            # Alarm-only bot traffic → produces ALERTED events
            attack_type, method, path, bot_ua = random.choice(ALARM_ONLY_PAYLOADS)
            ua = bot_ua or random.choice(BOT_USER_AGENTS)
            send_request(target, method, path, is_attack=False, attack_type=attack_type, ua_override=ua)
            stats["alarm"] += 1
        else:
            path = random.choice(LEGIT_PATHS)
            method = random.choice(LEGIT_METHODS)
            send_request(target, method, path, is_attack=False)
            stats["legit"] += 1

        if time.time() - last_report >= 60:
            total = sum(stats.values())
            log.info(
                "=== 1-min: total=%d legit=%d attack=%d alarm-bot=%d  rps=%.1f ===",
                total, stats["legit"], stats["attack"], stats["alarm"], rps,
            )
            stats = {"legit": 0, "attack": 0, "alarm": 0}
            last_report = time.time()

        time.sleep(sleep_between * random.uniform(0.5, 1.5))


if __name__ == "__main__":
    main()
