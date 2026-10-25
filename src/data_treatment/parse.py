"""Turn one RFC822 file into headers, participants, body text, and segments.

An obvious email is lowercased. Any other address is kept as written.
A missing or unparseable Date stays null and is never a file modification time.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from src.data_treatment.types import HeaderSource, ParsedMessage, Participant, ParticipantRole

_SUBJECT_PREFIX = re.compile(r"^(?:re|fw|fwd)\s*:\s*", re.IGNORECASE)
_MESSAGE_ID = re.compile(r"<[^>]+>")
_ORIGINAL_MESSAGE = re.compile(
    r"(?im)^[ \t>]*-{2,}\s*original\s+message\s*-{2,}\s*$"
)
_EMAIL = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._'+-]*[A-Za-z0-9])?"
    r"@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}"
)
_X500_CHUNK = re.compile(r".*?</O=[^>]*>", re.IGNORECASE | re.DOTALL)

_PARSER = BytesParser(policy=policy.default)

_ROLE_HEADERS = (
    ("from", "From", "X-From"),
    ("to", "To", "X-To"),
    ("cc", "Cc", "X-Cc"),
    ("bcc", "Bcc", "X-Bcc"),
)


def parse_file(relative_path: str, data: bytes) -> ParsedMessage:
    """Parse raw RFC822 bytes addressed by a corpus-relative path.

    Parser defects and undecodable bodies are kept as failed results.
    """
    file_sha256 = sha256_bytes(data)
    mailbox, folder = mailbox_and_folder(relative_path)
    try:
        message = _PARSER.parsebytes(data)
    except Exception as exc:  # noqa: BLE001 - keep the file as a parse-error row
        return _failed(relative_path, data, file_sha256, mailbox, folder, exc)

    raw_headers = raw_header_values(data)
    defects = defect_names(message)
    try:
        body = message_body(message)
        decode_error = None
    except Exception as exc:  # noqa: BLE001 - decoding failure is a kept parse error
        body = data.decode("latin-1")
        decode_error = f"{type(exc).__name__}: {exc}"

    date_raw = raw_headers.get("date") or _header(message, "Date") or None
    if date_raw == "":
        date_raw = None
    subject_raw = _header(message, "Subject")
    if subject_raw is None:
        subject_raw = ""
    origin_raw = _header(message, "X-Origin")
    if origin_raw is None:
        origin_raw = raw_headers.get("x-origin", "")
    cte = _header(message, "Content-Transfer-Encoding") or None
    message_id = _header(message, "Message-ID") or raw_headers.get("message-id")
    if not message_id:
        message_id = f"<unparsed-{file_sha256}>"

    participants = participants_for(message)
    from_raw, from_addr = sender(message, participants)
    parse_ok = not defects and decode_error is None
    error = None
    if defects:
        error = ", ".join(defects)
    elif decode_error:
        error = decode_error

    return ParsedMessage(
        path=relative_path,
        file_sha256=file_sha256,
        size_bytes=len(data),
        parse_ok=parse_ok,
        message_id=message_id,
        date_raw=date_raw,
        sent_at=parse_sent_at(date_raw),
        from_raw=from_raw,
        from_addr=from_addr,
        subject_raw=subject_raw,
        subject_norm=normalize_subject(subject_raw),
        mailbox=mailbox,
        folder=folder,
        origin_raw=origin_raw,
        cte=cte,
        body=body,
        body_norm_sha256=sha256_text(normalize_body(body)),
        participants=participants,
        segments=split_original_message(body),
        refs=message_ref_ids(message),
        error=error,
    )


def mailbox_and_folder(relative_path: str) -> tuple[str, str]:
    """Mailbox and folder names from a corpus-relative path.

    The mailbox is the first directory. The folder is every directory after it.
    """
    parts = relative_path.split("/")
    if len(parts) >= 3:
        return parts[0], "/".join(parts[1:-1])
    if len(parts) == 2:
        return parts[0], ""
    return "", ""


def normalize_subject(subject: str) -> str:
    """Lowercase a subject, strip leading Re/Fw/Fwd prefixes, and collapse whitespace.

    An empty subject stays empty.
    """
    text = subject.strip()
    while True:
        updated = _SUBJECT_PREFIX.sub("", text, count=1)
        if updated == text:
            break
        text = updated.strip()
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_body(body: str) -> str:
    """Collapse every whitespace run in a body to a single space."""
    return re.sub(r"\s+", " ", body).strip()


def parse_sent_at(date_raw: str | None) -> str | None:
    """UTC timestamp from a Date header value.

    Returns null when the value is missing or not a real date. A date with no
    timezone stays naive. The file modification time is not consulted.
    """
    if not date_raw or not date_raw.strip():
        return None
    try:
        parsed = parsedate_to_datetime(date_raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is None:
        return parsed.strftime("%Y-%m-%dT%H:%M:%S")
    utc = parsed.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def split_original_message(body: str) -> list[str]:
    """Split a body on Original Message separator lines.

    Position 0 is the text before the first separator, including when that line
    has a leading space. Other separator styles are left intact.
    """
    parts = _ORIGINAL_MESSAGE.split(body)
    if len(parts) == 1:
        stripped = body.strip()
        return [stripped] if stripped else [""]
    segments = [parts[0].strip()]
    for part in parts[1:]:
        text = part.strip()
        if text:
            segments.append(text)
    return segments


def parse_address_list(value: str | None) -> list[tuple[str, str]]:
    """Split an address header into `(raw token, identity)` pairs.

    Identity is the lowercased email when one is obvious. X.500 and Notes
    strings that are not emails stay as written.
    """
    if value is None:
        return []
    text = value.strip()
    if not text:
        return []
    if "/O=" in text.upper():
        chunks = _X500_CHUNK.findall(text)
        if chunks:
            return _identities_from_chunks(chunks)
    return _identities_from_chunks(split_address_commas(text))


def participants_for(message: EmailMessage) -> list[Participant]:
    """Address rows for From, To, Cc, and Bcc.

    The plain header is used when it is present, even if empty. The matching
    X- header is used only when the plain header is absent.
    """
    rows: list[Participant] = []
    for role, plain_name, x_name in _ROLE_HEADERS:
        plain = _header(message, plain_name)
        if plain is not None:
            source = HeaderSource.HEADER
            value = plain
        else:
            fallback = _header(message, x_name)
            if fallback is None:
                continue
            source = HeaderSource.X_HEADER
            value = fallback
        for position, (raw, addr) in enumerate(parse_address_list(value)):
            rows.append(
                Participant(
                    role=ParticipantRole(role),
                    source=source,
                    position=position,
                    raw=raw,
                    addr=addr,
                )
            )
    return rows


def sender(
    message: EmailMessage, participants: list[Participant]
) -> tuple[str, str | None]:
    """Sender header text and address identity.

    The plain From value is preferred. X-From is used only when From is absent.
    """
    plain = _header(message, "From")
    if plain is not None:
        from_raw = plain
    else:
        from_raw = _header(message, "X-From") or ""
    from_addr = next(
        (row.addr for row in participants if row.role == "from" and row.addr),
        None,
    )
    return from_raw, from_addr


def raw_header_values(data: bytes) -> dict[str, str]:
    """Unfolded header map from raw bytes, with lowercased names.

    Values stay as written, including Date comments such as `(PST)`.
    """
    block = _header_block(data)
    unfolded: list[str] = []
    for line in block.splitlines():
        if unfolded and line[:1] in (" ", "\t"):
            unfolded[-1] = unfolded[-1] + " " + line.strip()
        else:
            unfolded.append(line)
    headers: dict[str, str] = {}
    for line in unfolded:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        key = name.strip().lower()
        if key and key not in headers:
            headers[key] = value.strip()
    return headers


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a Unicode string encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _failed(
    relative_path: str,
    data: bytes,
    file_sha256: str,
    mailbox: str,
    folder: str,
    exc: Exception,
) -> ParsedMessage:
    """A failed parse that still keeps the file bytes as Latin-1 text."""
    body = data.decode("latin-1")
    return ParsedMessage(
        path=relative_path,
        file_sha256=file_sha256,
        size_bytes=len(data),
        parse_ok=False,
        message_id=f"<unparsed-{file_sha256}>",
        date_raw=None,
        sent_at=None,
        from_raw="",
        from_addr=None,
        subject_raw="",
        subject_norm="",
        mailbox=mailbox,
        folder=folder,
        origin_raw="",
        cte=None,
        body=body,
        body_norm_sha256=sha256_text(normalize_body(body)),
        participants=[],
        segments=split_original_message(body),
        error=f"{type(exc).__name__}: {exc}",
    )


def message_ref_ids(message: EmailMessage) -> list[str]:
    """Message-ids from In-Reply-To and References, in header order.

    Identifiers are kept as written. Repeated ids are dropped.
    """
    found: list[str] = []
    seen: set[str] = set()
    for name in ("In-Reply-To", "References"):
        value = _header(message, name)
        if not value:
            continue
        ids = _MESSAGE_ID.findall(value) or [value.strip()]
        for item in ids:
            token = item.strip()
            key = token.lower()
            if token and key not in seen:
                seen.add(key)
                found.append(token)
    return found


def _header(message: EmailMessage, name: str) -> str | None:
    """Stripped header value, or null when the header is absent.

    An empty header is an empty string, which is distinct from absence.
    """
    if name not in message:
        return None
    value = message.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _header_block(data: bytes) -> str:
    """Header section of an RFC822 byte string, before the first blank line."""
    indexes = []
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = data.find(separator)
        if index != -1:
            indexes.append(index)
    if not indexes:
        return ""
    return data[: min(indexes)].decode("latin-1")


def message_body(message: EmailMessage) -> str:
    """Decoded text body. Non-text content is decoded as UTF-8 with replacement."""
    content = message.get_content()
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return ""


def defect_names(message: EmailMessage) -> list[str]:
    """Parser defect class names on a message and its parts."""
    names = [type(item).__name__ for item in message.defects]
    if message.is_multipart():
        for part in message.iter_parts():
            names.extend(type(item).__name__ for item in part.defects)
    return names


def split_address_commas(value: str) -> list[str]:
    """Split an address list on commas outside quotes and angle brackets."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quotes = False
    for char in value:
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes and char == "<":
            depth += 1
        elif not in_quotes and char == ">" and depth:
            depth -= 1
        if char == "," and not in_quotes and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _identities_from_chunks(chunks: list[str]) -> list[tuple[str, str]]:
    """Raw token and identity for each address chunk.

    One obvious email becomes a lowercased identity. Several emails in one
    chunk become one pair each. A chunk with no email keeps its raw text.
    """
    identities: list[tuple[str, str]] = []
    for chunk in chunks:
        raw = re.sub(r"^[\s,]+", "", chunk).strip()
        if not raw:
            continue
        emails = _obvious_emails(raw)
        if len(emails) == 1:
            identities.append((raw, emails[0].lower()))
        elif len(emails) > 1:
            for email in emails:
                identities.append((email, email.lower()))
        else:
            identities.append((raw, raw))
    return identities


def _obvious_emails(value: str) -> list[str]:
    """Email addresses in a string, in order, without repeated lowercased forms."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _EMAIL.finditer(value):
        text = match.group(0)
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found
