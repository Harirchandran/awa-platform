"""Knowledge ingestion helpers for tenant-managed sources."""
import asyncio
import ipaddress
import re
import socket
import http.client
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List
from uuid import UUID
from backend.ai.rag_service import rag_service
from backend.ai.security import is_prompt_security_request, sanitize_untrusted_text
from backend.db.client import db_client
from backend.domains.knowledge.service import knowledge_service

MAX_CHUNK_CHARS = 3500
MAX_SOURCE_CHARS = 24000
PRIVATE_HOSTS = {"localhost", "localhost.localdomain"}
class KnowledgeIngestionError(ValueError):
    pass
def _validate_admin_text(label: str, text: str, *, min_length: int = 2, max_length: int = MAX_SOURCE_CHARS) -> str:
    clean = (text or "").strip()
    if len(clean) < min_length:
        raise KnowledgeIngestionError(f"{label} is too short")
    if len(clean) > max_length:
        raise KnowledgeIngestionError(f"{label} is too long")
    if is_prompt_security_request(clean):
        raise KnowledgeIngestionError(f"{label} contains unsafe instruction-like text")
    return clean
def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    raw = str(text or "")
    wrapped_lines: List[str] = []
    for line in raw.splitlines():
        if len(line) <= 600:
            wrapped_lines.append(line)
        else:
            wrapped_lines.extend(line[start:start + 600] for start in range(0, len(line), 600))
    clean = sanitize_untrusted_text("\n".join(wrapped_lines), max_chars=MAX_SOURCE_CHARS)
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", clean) if part.strip()]
    if not paragraphs:
        return []
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start:start + max_chars].strip())
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) > max_chars:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks
def _is_private_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    if not host or host in PRIVATE_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False
class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, resolved_ip, original_host, **kwargs):
        port = None
        if ":" in host:
            _, port = host.split(":", 1)
        self.original_host = original_host
        self.resolved_ip = resolved_ip

        connection_host = resolved_ip
        if port:
            connection_host = f"{resolved_ip}:{port}"
        super().__init__(connection_host, **kwargs)
        self.host = original_host
        self.port = int(port) if port else self.default_port

    def connect(self):
        original_self_host = self.host
        self.host = self.resolved_ip
        try:
            super().connect()
        finally:
            self.host = original_self_host

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, resolved_ip, original_host, **kwargs):
        port = None
        if ":" in host:
            _, port = host.split(":", 1)
        self.original_host = original_host
        self.resolved_ip = resolved_ip

        connection_host = resolved_ip
        if port:
            connection_host = f"{resolved_ip}:{port}"
        super().__init__(connection_host, **kwargs)
        self.host = original_host
        self.port = int(port) if port else self.default_port

    def connect(self):
        original_self_host = self.host
        self.host = self.resolved_ip
        try:
            http.client.HTTPConnection.connect(self)
        finally:
            self.host = original_self_host

        if self._tunnel_host:
            server_hostname = self._tunnel_host
        else:
            server_hostname = self.original_host

        self.sock = self._context.wrap_socket(self.sock,
                                              server_hostname=server_hostname)

class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolved_ip, original_host):
        super().__init__()
        self.resolved_ip = resolved_ip
        self.original_host = original_host
    def http_open(self, req):
        def connection_factory(host, **kwargs):
            return PinnedHTTPConnection(host, self.resolved_ip, self.original_host, **kwargs)
        return self.do_open(connection_factory, req)

class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolved_ip, original_host, context=None):
        super().__init__(context=context)
        self.resolved_ip = resolved_ip
        self.original_host = original_host
    def https_open(self, req):
        def connection_factory(host, **kwargs):
            return PinnedHTTPSConnection(host, self.resolved_ip, self.original_host, **kwargs)
        return self.do_open(connection_factory, req, context=self._context)

def validate_public_url(url: str) -> tuple[str, str]:
    clean = _validate_admin_text("Website URL", url, max_length=500)
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in {"http", "https"}:
        raise KnowledgeIngestionError("Website URL must start with http:// or https://")

    hostname = parsed.hostname or ""
    host = hostname.strip().lower()
    if not host or host in PRIVATE_HOSTS:
        raise KnowledgeIngestionError("Private, local, or internal URLs cannot be imported")

    try:
        ip = ipaddress.ip_address(host)
        resolved_ip = str(ip)
    except ValueError:
        try:
            addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            if not addresses:
                raise KnowledgeIngestionError("Could not resolve website hostname")
            resolved_ip = addresses[0][4][0]
        except Exception:
            raise KnowledgeIngestionError("Private, local, or internal URLs cannot be imported")

    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast:
            raise KnowledgeIngestionError("Private, local, or internal URLs cannot be imported")
    except ValueError:
        raise KnowledgeIngestionError("Private, local, or internal URLs cannot be imported")

    return clean, resolved_ip

def _extract_page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|li|h[1-6]|section|article)>", "\n", html)
    text = re.sub(r"(?is)<[^>]+>", " ", html)
    text = urllib.parse.unquote(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()

def _fetch_url_text_sync(url: str, resolved_ip: str | None = None) -> str:
    if not resolved_ip:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname or ""
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        resolved_ip = addresses[0][4][0]

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname or ""
    opener = urllib.request.build_opener(
        PinnedHTTPHandler(resolved_ip, hostname),
        PinnedHTTPSHandler(resolved_ip, hostname)
    )
    request = urllib.request.Request(url, headers={"User-Agent": "AWA-KnowledgeBot/1.0"})
    with opener.open(request, timeout=8) as response:
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            raise KnowledgeIngestionError("Website did not return readable text")
        raw = response.read(MAX_SOURCE_CHARS * 2)
    return _extract_page_text(raw.decode("utf-8", errors="ignore"))
class KnowledgeIngestionService:
    async def create_entries(
        self,
        tenant_id: UUID,
        title: str,
        text: str,
        source: str,
        metadata: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        clean_title = _validate_admin_text("Title", title, max_length=160)
        clean_text = _validate_admin_text("Knowledge text", text, min_length=10)
        chunks = chunk_text(clean_text)
        if not chunks:
            raise KnowledgeIngestionError("Knowledge text could not be processed")
        created = []
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            embedding = await rag_service.embed_query(chunk)
            entry_title = clean_title if total_chunks == 1 else f"{clean_title} ({index}/{total_chunks})"
            created.append(await knowledge_service.create_entry(
                tenant_id=tenant_id,
                document_title=entry_title,
                chunk_text=chunk,
                embedding=embedding,
                metadata={
                    "source": source,
                    "enabled": True,
                    "status": "ready",
                    "processing_mode": "vector_search" if rag_service.is_usable_embedding(embedding) else "lexical_search",
                    "chunk_index": index,
                    "chunk_count": total_chunks,
                    **dict(metadata or {}),
                }
            ))
        return created
    async def create_manual_entry(self, tenant_id: UUID, title: str, text: str) -> List[Dict[str, Any]]:
        return await self.create_entries(tenant_id, title, text, "manual")
    async def create_document_entry(self, tenant_id: UUID, title: str, text: str, file_name: str | None = None) -> List[Dict[str, Any]]:
        metadata = {"file_name": file_name} if file_name else {}
        return await self.create_entries(tenant_id, title, text, "document", metadata)
    async def import_website(self, tenant_id: UUID, url: str, title: str | None = None) -> List[Dict[str, Any]]:
        safe_url, resolved_ip = validate_public_url(url)
        text = await asyncio.to_thread(_fetch_url_text_sync, safe_url, resolved_ip)
        if len(text) < 80:
            raise KnowledgeIngestionError("Website did not contain enough readable text")
        parsed = urllib.parse.urlparse(safe_url)
        source_title = title or f"Website - {parsed.netloc}"
        await knowledge_service.delete_entries_by_url(tenant_id, safe_url)
        return await self.create_entries(
            tenant_id,
            source_title,
            text,
            "website",
            {"url": safe_url},
        )
    async def sync_faqs(self, tenant_id: UUID, faqs: Iterable[Dict[str, Any]]) -> int:
        await knowledge_service.delete_entries_by_source(tenant_id, "faq")
        count = 0
        for faq in faqs or []:
            question = (faq.get("question") or "").strip()
            answer = (faq.get("answer") or "").strip()
            if not question or not answer:
                continue
            chunk = f"FAQ question: {question}\nFAQ answer: {answer}"
            await self.create_entries(
                tenant_id,
                question[:120],
                chunk,
                "faq",
                {"faq_id": faq.get("id"), "processing_mode": "direct_context"},
            )
            count += 1
        return count
knowledge_ingestion_service = KnowledgeIngestionService()
