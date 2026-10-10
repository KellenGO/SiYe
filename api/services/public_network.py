"""Public-page reader with bounded responses and pinned public DNS targets."""

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from parsel import Selector


async def public_target(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or len(url) > 4096 or parsed.port not in (None, 80, 443)):
        raise ValueError("只允许访问公开 HTTP/HTTPS 网页")
    host = parsed.hostname
    if host.lower() in {"localhost", "metadata.google.internal"} or host.lower().endswith((".localhost", ".local")):
        raise ValueError("不允许访问本机或内网地址")
    addresses = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    ips = {item[4][0] for item in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("不允许访问本机或内网地址")
    ip = sorted(ips)[0]
    netloc = f"[{ip}]" if ":" in ip else ip
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, "")), host


async def public_get(url: str, *, json_response=False):
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        for _ in range(6):
            pinned, host = await public_target(url)
            async with client.stream("GET", pinned, headers={"Host": urlsplit(url).netloc,
                                     "User-Agent": "Mozilla/5.0"}, extensions={"sni_hostname": host}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("网页跳转地址缺失")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError("网页超过 2 MB，未完整读取")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if json_response:
                    import json
                    return json.loads(data)
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type and not content_type.startswith("text/"):
                    raise ValueError("仅支持文字网页，不读取音视频或文件")
                return url, data.decode(response.encoding or "utf-8", errors="replace")
    raise ValueError("网页跳转次数过多")


def page_text(html: str) -> tuple[str, str]:
    selector = Selector(text=html)
    title = selector.css("title::text").get() or "网页"
    selector.xpath("//script|//style|//nav|//footer|//noscript").drop()
    text = "\n".join(part.strip() for part in selector.xpath("//body//text()").getall() if part.strip())
    return title, text


async def search_public(query: str) -> list[dict]:
    """Public HTML search; no model vendor-specific search capability or key."""
    from urllib.parse import parse_qs, urlencode

    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError("搜索问题无效")
    _, html = await public_get("https://html.duckduckgo.com/html/?" + urlencode({"q": query}))
    results = []
    for row in Selector(text=html).css(".result"):
        link = row.css(".result__a")
        url = link.attrib.get("href", "")
        if url.startswith("//"):
            url = "https:" + url
        url = parse_qs(urlsplit(url).query).get("uddg", [url])[0]
        try:
            await public_target(url)
        except (ValueError, OSError):
            continue
        title = "".join(link.xpath(".//text()").getall()).strip()
        snippet = "".join(row.css(".result__snippet").xpath(".//text()").getall()).strip()
        if title and url not in {item["url"] for item in results}:
            results.append({"url": url, "title": title[:200], "snippet": snippet[:1200]})
        if len(results) >= 5:
            break
    if not results:
        raise ValueError("公开搜索暂时不可用")
    return results
