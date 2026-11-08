"""
Link analysis: read the public page behind a URL (title / description) and draft a campaign from it.

Safety: the server fetches a URL typed by the user, so this is hardened against SSRF - only http/https on
ports 80/443, every redirect hop is re-validated, hosts resolving to private / loopback / link-local /
reserved addresses are refused, response size and time are capped. Nothing is ever sent to WhatsApp here.
"""

import html
import ipaddress
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 8
UA = "Mozilla/5.0 (compatible; CampaignPreviewBot/1.0)"


class UnsafeURL(ValueError):
    pass


def _check_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL("Only http:// or https:// links are allowed.")
    if p.port not in (None, 80, 443):
        raise UnsafeURL("Only standard web ports are allowed.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURL("This address could not be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL("This address points to a private network and is not allowed.")
    return url


def fetch_page(url: str) -> Dict[str, Any]:
    current = _check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(current, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"},
                            timeout=TIMEOUT, stream=True, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            current = _check_url(urljoin(current, resp.headers["Location"]))
            resp.close()
            continue
        ctype = resp.headers.get("Content-Type", "")
        body = b""
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) >= MAX_BYTES:
                break
        resp.close()
        enc = resp.encoding or "utf-8"
        return {"status": resp.status_code, "final_url": current, "content_type": ctype,
                "text": body.decode(enc, errors="replace")}
    raise UnsafeURL("Too many redirects.")


def _meta(text: str, *names: str) -> str:
    for name in names:
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']*)["\']',
                    rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']'):
            m = re.search(pat, text, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
    return ""


def extract_metadata(page_html: str, fallback_url: str) -> Dict[str, str]:
    title = _meta(page_html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    desc = _meta(page_html, "og:description", "description", "twitter:description")
    site = _meta(page_html, "og:site_name") or (urlparse(fallback_url).hostname or "").replace("www.", "")
    title = re.sub(r"\s+", " ", title)[:120]
    return {"title": title or site or fallback_url, "description": re.sub(r"\s+", " ", desc)[:300], "site": site}


def draft_messages(meta: Dict[str, str], url: str) -> List[Dict[str, Any]]:
    """Ready-to-use message options. {{name}} etc. are filled per person at send time."""
    title = meta["title"]
    return [
        {"id": "pa_text", "label": "Punjabi (free text)", "kind": "text", "language": "pa",
         "body": ("ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {{name}} ਜੀ! 🙏\n\nਨਵਾਂ ਕੁਝ ਤੁਹਾਡੇ ਲਈ: {{video_title}}\n👉 {{video_url}}\n\n"
                  "ਪਿਆਰ ਤੇ ਸਾਥ ਲਈ ਧੰਨਵਾਦ! Reply STOP to unsubscribe.")},
        {"id": "en_text", "label": "English (free text)", "kind": "text", "language": "en",
         "body": ("Hi {{name}}! 👋\n\nSomething new for you: {{video_title}}\n👉 {{video_url}}\n\n"
                  "Thanks for being here. Reply STOP to unsubscribe.")},
        {"id": "pa_template", "label": "Punjabi template (needs Meta approval)", "kind": "template",
         "language": "pa", "variables": ["name", "video_url", "video_title"],
         "body": ("ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ {{1}} ਜੀ! 🙏\n\nਨਵਾਂ ਕੁਝ ਤੁਹਾਡੇ ਲਈ: {{3}}\nਇੱਥੇ ਦੇਖੋ: {{2}}\n\n"
                  "ਤੁਹਾਡੇ ਪਿਆਰ ਅਤੇ ਸਾਥ ਲਈ ਧੰਨਵਾਦ! — Manjot Singh"),
         "example_values": ["Manjot", url, title or "New video"]},
    ]


def analyse(url: str) -> Dict[str, Any]:
    """Return a campaign draft for this URL. Raises UnsafeURL for refused addresses."""
    page = fetch_page(url)
    warning: Optional[str] = None
    if page["status"] >= 400:
        warning = f"The page answered with status {page['status']}; check the link opens for everyone."
    meta = extract_metadata(page["text"], page["final_url"]) if "html" in page["content_type"].lower() or \
        "<html" in page["text"][:2000].lower() else {"title": urlparse(url).hostname or url, "description": "",
                                                       "site": urlparse(url).hostname or ""}
    return {"url": url, "final_url": page["final_url"], "campaign_name": meta["title"][:80],
            "title": meta["title"], "description": meta["description"], "site": meta["site"],
            "warning": warning, "messages": draft_messages(meta, url)}


def variables_for_template_body(body: str) -> List[str]:
    """Which campaign values fill {{1}}..{{n}}: name, link, title (in that order)."""
    n = len(set(re.findall(r"\{\{(\d+)\}\}", body or "")))
    order = ["name", "video_url", "video_title"]
    if n > len(order):
        raise ValueError("Templates with more than 3 variables are not supported by the campaign builder.")
    return order[:n]
