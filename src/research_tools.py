"""Small read-only clients for public scholarly sources; no ambient credentials."""
import http.client
import ipaddress
import json
import socket
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

HOSTS = frozenset({"api.crossref.org", "www.crossref.org", "pubmed.ncbi.nlm.nih.gov",
                   "pmc.ncbi.nlm.nih.gov", "arxiv.org"})
MAX_BYTES = 1_000_000
FETCH_DEADLINE_SECONDS = 25
IO_TIMEOUT_SECONDS = 10
_REQUEST = threading.Lock()
_last_search = 0.0


def _url(url):
    if len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url:
        raise ValueError("Invalid URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment
            or "@" in parsed.netloc):
        raise ValueError("Use HTTPS on an approved scholarly host")
    return parsed


def _public_ip(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError("Host did not resolve exclusively to public addresses")
    return str(ips[0])


def _fetch(url, accepted):
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    for _ in range(3):
        parsed = _url(url)
        ip = _public_ip(parsed.hostname)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Fetch deadline exceeded")
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(IO_TIMEOUT_SECONDS, remaining))
        # http.client retains hostname/SNI and normal TLS validation.
        connection._create_connection = lambda _address, timeout, source: socket.create_connection(
            (ip, 443), timeout, source
        )
        stage = "connect/request"
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request("GET", path, headers={
                "User-Agent": "ResearchChatroom/0.6", "Accept": ", ".join(accepted),
                "Accept-Encoding": "identity",
            })
            stage = "response_headers"
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect missing a destination")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Source returned HTTP {response.status}")
            content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if content_type not in accepted or response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Unsupported content type or encoding")
            length = response.getheader("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("Source exceeds download limit")
            chunks = bytearray()
            stage = "response_body"
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Fetch deadline exceeded")
                if connection.sock:
                    connection.sock.settimeout(min(IO_TIMEOUT_SECONDS, remaining))
                part = response.read1(min(16_384, MAX_BYTES + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
                if len(chunks) > MAX_BYTES:
                    raise ValueError("Source exceeds download limit")
            return url, content_type, bytes(chunks)
        except TimeoutError as exc:
            raise TimeoutError(f"network timeout at {stage}; I/O cap={IO_TIMEOUT_SECONDS}s, request deadline={FETCH_DEADLINE_SECONDS}s") from exc
        except http.client.HTTPException as exc:
            raise OSError("Invalid upstream HTTP response") from exc
        finally:
            connection.close()
    raise ValueError("Redirect limit reached")


def search_crossref(query, limit):
    global _last_search
    query = query.strip()
    if not query or len(query) > 240 or not 1 <= limit <= 5:
        raise ValueError("Supply a short scholarly query and a limit from 1 to 5")
    if not _REQUEST.acquire(blocking=False):
        raise ValueError("Research tools busy; wait before retrying")
    try:
        if time.monotonic() - _last_search < 1:
            raise ValueError("Crossref search limited to one request per second")
        _last_search = time.monotonic()
        params = urlencode({"query.bibliographic": query, "rows": limit,
                            "select": "title,author,issued,DOI,URL,type,container-title"})
        _, _, body = _fetch("https://api.crossref.org/works?" + params, {"application/json"})
        results = []
        for item in json.loads(body)["message"]["items"][:limit]:
            date = (item.get("issued", {}).get("date-parts") or [[]])[0]
            results.append({
                "title": (item.get("title") or [""])[0],
                "authors": [" ".join(filter(None, (a.get("given"), a.get("family"), a.get("name"))))
                            for a in item.get("author", [])[:12]],
                "year": date[0] if date else None,
                "doi": item.get("DOI"), "url": item.get("URL"),
                "venue": (item.get("container-title") or [""])[0],
                "publication_type": item.get("type"),
            })
        return {"source": "Crossref", "evidence": "bibliographic metadata only; no abstract or full text read",
                "results": results}
    finally:
        _REQUEST.release()


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.title = []
        self.in_title = False
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if not self.hidden and data.strip():
            (self.title if self.in_title else self.parts).append(data.strip())


def fetch_page(url):
    if not _REQUEST.acquire(blocking=False):
        raise ValueError("Research tools busy; wait before retrying")
    try:
        final_url, kind, body = _fetch(url, {"text/html", "text/plain"})
        text = body.decode("utf-8", errors="replace")
        title = ""
        if kind == "text/html":
            parser = _PageText()
            parser.feed(text)
            title = " ".join(parser.title)[:300]
            text = " ".join(parser.parts)
        return {"url": final_url, "title": title, "text": text[:6500], "truncated": len(text) > 6500,
                "evidence": "untrusted page excerpt; not proof of full-text access"}
    finally:
        _REQUEST.release()
