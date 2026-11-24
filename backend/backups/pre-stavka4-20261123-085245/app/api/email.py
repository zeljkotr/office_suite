import base64
import binascii
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape
from html.parser import HTMLParser
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.email_account import EmailAccount
from app.models.email_folder_item import EmailFolderItem
from app.models.user import User
from app.services.email_crypto import encrypt_email_password, decrypt_email_password

router = APIRouter(prefix="/email", tags=["Email Manager"])


def require_admin(user: User = Depends(get_current_user)):
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


def owned_account(db: Session, user: User, account_id: int) -> EmailAccount:
    account = db.query(EmailAccount).filter(
        EmailAccount.id == account_id,
        EmailAccount.user_id == user.id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Email account not found.")
    return account


def active_account(db: Session, user: User) -> EmailAccount:
    account = db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id,
        EmailAccount.is_active.is_(True),
    ).order_by(EmailAccount.id.desc()).first()
    if not account:
        raise HTTPException(
            status_code=503,
            detail="No email account configured. Add an account in Email Manager.",
        )
    return account


def account_data(account: EmailAccount) -> dict:
    return {
        "id": account.id,
        "provider": account.provider,
        "email_address": account.email_address,
        "display_name": account.display_name,
        "imap": {
            "host": account.imap_host, "port": account.imap_port,
            "ssl": account.imap_ssl, "configured": True,
        },
        "smtp": {
            "host": account.smtp_host, "port": account.smtp_port,
            "ssl": account.smtp_ssl, "starttls": account.smtp_starttls,
            "configured": True,
        },
        "mailbox": account.mailbox,
        "is_active": account.is_active,
        "connection_status": account.connection_status,
        "last_tested_at": account.last_tested_at,
        "password_saved": bool(account.encrypted_password),
    }


def decode_text(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def open_imap(account: EmailAccount):
    password = decrypt_email_password(account.encrypted_password)
    try:
        if account.imap_ssl:
            client = imaplib.IMAP4_SSL(
                account.imap_host, account.imap_port,
                ssl_context=ssl.create_default_context(), timeout=15
            )
        else:
            client = imaplib.IMAP4(account.imap_host, account.imap_port, timeout=15)
        client.login(account.email_address, password)
        return client
    except (OSError, imaplib.IMAP4.error) as exc:
        raise HTTPException(
            status_code=502,
            detail="IMAP connection or authentication failed. Check the server settings and app password.",
        ) from exc


def open_smtp(account: EmailAccount):
    password = decrypt_email_password(account.encrypted_password)
    try:
        context = ssl.create_default_context()
        if account.smtp_ssl:
            client = smtplib.SMTP_SSL(
                account.smtp_host, account.smtp_port, timeout=15, context=context
            )
        else:
            client = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=15)
            client.ehlo()
            if account.smtp_starttls:
                client.starttls(context=context)
                client.ehlo()
        client.login(account.email_address, password)
        return client
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=502,
            detail="SMTP connection or authentication failed. Check the server settings and app password.",
        ) from exc


class EmailHTMLSanitizer(HTMLParser):
    """Remove executable content and externally loaded resources from email HTML."""

    BLOCKED = {
        "script", "style", "iframe", "frame", "frameset",
        "object", "embed", "applet", "form", "input",
        "button", "textarea", "select", "meta", "base",
        "link", "svg", "math",
    }
    VOID = {
        "area", "br", "col", "hr", "img", "source",
        "track", "wbr", "input", "meta", "link", "base",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.blocked_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if self.blocked_stack:
            if tag not in self.VOID:
                self.blocked_stack.append(tag)
            return

        if tag in self.BLOCKED:
            self.blocked_stack.append(tag)
            return

        safe_attrs = []
        for name, value in attrs:
            name = name.lower()
            if (
                name.startswith("on")
                or name in {"src", "srcset", "poster", "background",
                            "style", "action", "formaction", "xlink:href"}
            ):
                continue

            if name == "href":
                if not value or value.strip().lower().startswith(
                    ("javascript:", "data:", "vbscript:", "file:")
                ):
                    continue

            if value is not None:
                safe_attrs.append(f'{name}="{escape(value, quote=True)}"')

        attributes = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.output.append(f"<{tag}{attributes}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if not self.blocked_stack and tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self.blocked_stack:
            if tag in self.blocked_stack:
                index = len(self.blocked_stack) - 1 - self.blocked_stack[::-1].index(tag)
                self.blocked_stack = self.blocked_stack[:index]
            return

        if tag not in self.VOID and tag not in self.BLOCKED:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked_stack:
            self.output.append(escape(data))

    def handle_entityref(self, name):
        if not self.blocked_stack:
            self.output.append(f"&{name};")

    def handle_charref(self, name):
        if not self.blocked_stack:
            self.output.append(f"&#{name};")

    def get_html(self):
        return "".join(self.output)



def _is_mime_attachment(part) -> bool:
    """Exclude attachments, including inline files with a filename."""
    try:
        return (
            part.get_content_disposition() == "attachment"
            or part.get_filename() is not None
        )
    except Exception:
        return True


def _iter_mime_body_parts(part):
    """Recursively traverse MIME bodies while excluding attachment subtrees."""
    if _is_mime_attachment(part):
        return

    payload = part.get_payload()

    if part.is_multipart() or (
        part.get_content_maintype() == "message" and isinstance(payload, list)
    ):
        if isinstance(payload, list):
            for child in payload:
                yield from _iter_mime_body_parts(child)
        return

    yield part


def decode_mime_part(part) -> str:
    """Decode transfer encoding and declared charset with safe fallbacks."""
    try:
        payload = part.get_payload(decode=True)
    except Exception:
        payload = None

    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""

    declared = part.get_content_charset()
    charsets = []
    if declared:
        charsets.append(declared)
    charsets.extend(["utf-8", "windows-1252", "iso-8859-1"])

    tried = set()
    for charset in charsets:
        key = charset.lower()
        if key in tried:
            continue
        tried.add(key)
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, UnicodeError):
            continue

    return payload.decode("utf-8", errors="replace")


def _rtf_to_text(value: str) -> str:
    """Convert common RTF control words and escaped characters to readable text."""
    value = re.sub(
        r"\\'([0-9a-fA-F]{2})",
        lambda match: bytes.fromhex(match.group(1)).decode("cp1252", errors="replace"),
        value,
    )
    value = re.sub(
        r"\\u(-?\d+)\??",
        lambda match: chr(int(match.group(1)) % 65536),
        value,
    )
    value = re.sub(r"\\(par|line)\b\s?", "\n", value)
    value = re.sub(r"\\tab\b\s?", "\t", value)
    value = re.sub(r"\\([{}\\])", r"\1", value)
    value = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", value)
    value = value.replace("{", "").replace("}", "")
    return value.strip()


def extract_message_bodies(message) -> tuple[str, str]:
    """Extract readable body variants from common and nested MIME structures."""
    plain_parts = []
    html_parts = []

    for part in _iter_mime_body_parts(message):
        content_type = part.get_content_type().lower()

        if content_type in {"text/html", "application/xhtml+xml"}:
            html_parts.append(decode_mime_part(part))
            continue

        if content_type == "text/plain":
            plain_parts.append(decode_mime_part(part))
            continue

        if content_type in {"text/rtf", "application/rtf"}:
            plain_parts.append(_rtf_to_text(decode_mime_part(part)))
            continue

        # Calendar, enriched text, Markdown, XML and other textual MIME
        # bodies remain readable instead of silently disappearing.
        if content_type.startswith("text/"):
            value = decode_mime_part(part)
            if value.strip():
                plain_parts.append(value)

    plain = "\n\n".join(value for value in plain_parts if value.strip())
    raw_html = "\n".join(value for value in html_parts if value.strip())

    sanitizer = EmailHTMLSanitizer()
    if raw_html:
        try:
            sanitizer.feed(raw_html)
            sanitizer.close()
        except Exception:
            # A malformed HTML body must not prevent the message from opening.
            sanitizer = EmailHTMLSanitizer()
            sanitizer.feed(escape(raw_html))
            sanitizer.close()

    html_body = sanitizer.get_html()

    if not plain and not html_body:
        content_type = message.get_content_type().lower()
        if content_type in {
            "application/pkcs7-mime",
            "application/x-pkcs7-mime",
            "application/pgp-encrypted",
        }:
            plain = "[This message is encrypted and cannot be displayed without its decryption key.]"

    return plain, html_body


class EmailAccountCreate(BaseModel):
    provider: str = Field(default="other", min_length=1, max_length=40)
    email_address: EmailStr
    display_name: str = Field(default="", max_length=150)
    password: str = Field(min_length=1, max_length=500)
    imap_host: str = Field(min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_ssl: bool = True
    smtp_host: str = Field(min_length=1, max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_ssl: bool = False
    smtp_starttls: bool = True
    mailbox: str = Field(default="INBOX", min_length=1, max_length=255)


@router.get("/accounts")
def list_email_accounts(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    accounts = db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id
    ).order_by(EmailAccount.id.desc()).all()
    return {"items": [account_data(a) for a in accounts]}


@router.post("/accounts")
def save_email_account(
    payload: EmailAccountCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    address = str(payload.email_address).lower()
    account = db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id,
        EmailAccount.email_address == address,
    ).first()

    if account is None:
        account = EmailAccount(user_id=user.id, email_address=address)
        db.add(account)

    for field in (
        "provider", "display_name", "imap_host", "imap_port", "imap_ssl",
        "smtp_host", "smtp_port", "smtp_ssl", "smtp_starttls", "mailbox",
    ):
        setattr(account, field, getattr(payload, field))

    account.encrypted_password = encrypt_email_password(payload.password)
    account.is_active = True
    account.connection_status = "untested"
    account.last_tested_at = None

    db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id
    ).update({"is_active": False}, synchronize_session=False)
    account.is_active = True
    db.commit()
    db.refresh(account)
    return account_data(account)


@router.post("/accounts/{account_id}/activate")
def activate_email_account(
    account_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = owned_account(db, user, account_id)
    db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id
    ).update({"is_active": False}, synchronize_session=False)
    account.is_active = True
    db.commit()
    return {"status": "active", "account_id": account.id}


@router.delete("/accounts/{account_id}")
def delete_email_account(
    account_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = owned_account(db, user, account_id)
    db.delete(account)
    db.commit()
    return {"status": "deleted", "account_id": account_id}


@router.post("/accounts/{account_id}/test")
def test_saved_account(
    account_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = owned_account(db, user, account_id)
    result = {"imap": "not_tested", "smtp": "not_tested"}
    try:
        client = open_imap(account)
        try:
            result["imap"] = "connected"
        finally:
            try:
                client.logout()
            except Exception:
                pass
        client = open_smtp(account)
        try:
            result["smtp"] = "connected"
        finally:
            try:
                client.quit()
            except Exception:
                pass
        account.connection_status = "connected"
    except HTTPException:
        account.connection_status = "failed"
        account.last_tested_at = datetime.now(timezone.utc).isoformat()
        db.commit()
        raise
    account.last_tested_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return result


@router.get("/status")
def email_status(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    accounts = db.query(EmailAccount).filter(
        EmailAccount.user_id == user.id
    ).order_by(EmailAccount.id.desc()).all()
    active = next((a for a in accounts if a.is_active), None)
    if active is None:
        return {
            "configured": False,
            "accounts": [account_data(a) for a in accounts],
            "imap": {"configured": False},
            "smtp": {"configured": False},
            "mailbox": "INBOX",
            "from_address": None,
        }
    data = account_data(active)
    return {
        "configured": True,
        "accounts": [account_data(a) for a in accounts],
        "active_account": data,
        "imap": data["imap"],
        "smtp": data["smtp"],
        "mailbox": active.mailbox,
        "from_address": active.email_address,
    }


@router.post("/test")
def test_email_connections(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = active_account(db, user)
    client = open_imap(account)
    try:
        imap_result = "connected"
    finally:
        try:
            client.logout()
        except Exception:
            pass
    client = open_smtp(account)
    try:
        smtp_result = "connected"
    finally:
        try:
            client.quit()
        except Exception:
            pass
    account.connection_status = "connected"
    account.last_tested_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    return {"imap": imap_result, "smtp": smtp_result}


@router.get("/inbox")
def inbox(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = active_account(db, user)
    client = open_imap(account)
    try:
        result, _ = client.select(account.mailbox, readonly=True)
        if result != "OK":
            raise HTTPException(status_code=502, detail="Unable to open configured mailbox.")
        result, data = client.uid("search", None, "ALL")
        if result != "OK":
            raise HTTPException(status_code=502, detail="Unable to search mailbox.")
        uids = (data[0] or b"").split()[-limit:]
        messages = []
        for uid in reversed(uids):
            result, parts = client.uid(
                "fetch", uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] BODY.PEEK[TEXT]<0.4096>)"
            )
            if result != "OK":
                continue
            raw = b"".join(part[1] for part in parts if isinstance(part, tuple))
            msg = message_from_bytes(raw)
            sender = decode_text(msg.get("From"))
            snippet = raw[-4096:].decode("utf-8", errors="replace")[:1000]
            messages.append({
                "uid": uid.decode("ascii", errors="replace"),
                "from": sender,
                "from_address": parseaddr(sender)[1],
                "to": decode_text(msg.get("To")),
                "subject": decode_text(msg.get("Subject")) or "(no subject)",
                "date": decode_text(msg.get("Date")),
                "snippet": snippet,
            })
        return {"mailbox": account.mailbox, "count": len(messages), "items": messages}
    finally:
        try:
            client.logout()
        except Exception:
            pass


@router.get("/message/{uid}")
def get_email_message(
    uid: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not uid.isdigit() or len(uid) > 20:
        raise HTTPException(status_code=400, detail="Invalid message UID.")

    account = active_account(db, user)
    client = open_imap(account)

    try:
        result, _ = client.select(account.mailbox, readonly=True)
        if result != "OK":
            raise HTTPException(
                status_code=502,
                detail="Unable to open configured mailbox.",
            )

        result, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
        if result != "OK":
            raise HTTPException(
                status_code=502,
                detail="Unable to retrieve email message.",
            )

        raw_messages = [
            part[1] for part in parts
            if isinstance(part, tuple) and isinstance(part[1], bytes)
        ]

        if not raw_messages:
            raise HTTPException(status_code=404, detail="Email message not found.")

        message = message_from_bytes(b"".join(raw_messages), policy=policy.default)

        plain_body, html_body = extract_message_bodies(message)

        return {
            "uid": uid,
            "subject": decode_text(message.get("Subject")) or "(no subject)",
            "from": decode_text(message.get("From")),
            "to": decode_text(message.get("To")),
            "cc": decode_text(message.get("Cc")),
            "date": decode_text(message.get("Date")),
            "text": plain_body,
            "html": html_body,
            "content_type": message.get_content_type(),
        }

    finally:
        try:
            client.logout()
        except Exception:
            pass


class EmailSendAttachment(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=150)
    content_base64: str = Field(min_length=1, max_length=28 * 1024 * 1024)


class EmailSend(BaseModel):
    to: list[EmailStr] = Field(min_length=1, max_length=50)
    cc: list[EmailStr] = Field(default_factory=list, max_length=50)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=200000)
    attachments: list[EmailSendAttachment] = Field(default_factory=list, max_length=10)


class EmailFolderSave(BaseModel):
    id: int | None = None
    to: str = Field(default="", max_length=10000)
    cc: str = Field(default="", max_length=10000)
    subject: str = Field(default="", max_length=998)
    body: str = Field(default="", max_length=200000)
    attachments: list[dict] = Field(default_factory=list, max_length=10)


def _folder_message(item: EmailFolderItem) -> dict:
    return {
        "id": item.id,
        "folder": item.folder,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        **(item.message or {}),
    }


@router.get("/folders/{folder}")
def list_email_folder(
    folder: str,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if folder not in {"drafts", "sent", "trash"}:
        raise HTTPException(status_code=400, detail="Invalid local email folder.")
    account = active_account(db, user)
    items = db.query(EmailFolderItem).filter(
        EmailFolderItem.owner_id == user.id,
        EmailFolderItem.account_id == account.id,
        EmailFolderItem.folder == folder,
    ).order_by(EmailFolderItem.updated_at.desc(), EmailFolderItem.id.desc()).all()
    return {"folder": folder, "items": [_folder_message(item) for item in items]}


@router.post("/folders/drafts")
def save_email_draft(
    payload: EmailFolderSave,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = active_account(db, user)
    data = payload.model_dump(exclude={"id"})
    if payload.id is not None:
        item = db.query(EmailFolderItem).filter(
            EmailFolderItem.id == payload.id,
            EmailFolderItem.owner_id == user.id,
            EmailFolderItem.account_id == account.id,
            EmailFolderItem.folder == "drafts",
        ).first()
        if not item:
            raise HTTPException(status_code=404, detail="Draft not found.")
        item.message = data
    else:
        item = EmailFolderItem(
            owner_id=user.id, account_id=account.id,
            folder="drafts", message=data,
        )
        db.add(item)
    db.commit()
    db.refresh(item)
    return _folder_message(item)


@router.get("/folders/items/{item_id}")
def get_email_folder_item(
    item_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(EmailFolderItem).filter(
        EmailFolderItem.id == item_id,
        EmailFolderItem.owner_id == user.id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Email folder item not found.")
    return _folder_message(item)


@router.patch("/folders/items/{item_id}")
def move_email_folder_item(
    item_id: int,
    payload: dict,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    folder = payload.get("folder")
    if folder not in {"drafts", "sent", "trash"}:
        raise HTTPException(status_code=400, detail="Invalid destination folder.")
    item = db.query(EmailFolderItem).filter(
        EmailFolderItem.id == item_id,
        EmailFolderItem.owner_id == user.id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Email folder item not found.")
    item.folder = folder
    db.commit()
    db.refresh(item)
    return _folder_message(item)


@router.delete("/folders/items/{item_id}")
def permanently_delete_email_folder_item(
    item_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(EmailFolderItem).filter(
        EmailFolderItem.id == item_id,
        EmailFolderItem.owner_id == user.id,
        EmailFolderItem.folder == "trash",
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Trash item not found.")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


@router.post("/send")
def send_email(
    payload: EmailSend,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    account = active_account(db, user)
    message = EmailMessage()
    message["From"] = account.email_address
    message["To"] = ", ".join(str(address) for address in payload.to)
    if payload.cc:
        message["Cc"] = ", ".join(str(address) for address in payload.cc)
    message["Subject"] = payload.subject
    message.set_content(payload.body)

    total_attachment_bytes = 0
    for attachment in payload.attachments:
        filename = attachment.filename.replace("\\\\", "/").split("/")[-1]
        filename = re.sub(r"[\\x00-\\x1f\\x7f]", "", filename).strip()
        if not filename or filename in {".", ".."}:
            raise HTTPException(status_code=400, detail="Invalid attachment filename.")

        content_type = attachment.content_type.strip().lower()
        if not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", content_type):
            content_type = "application/octet-stream"

        try:
            file_data = base64.b64decode(attachment.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid attachment encoding.") from exc

        if not file_data:
            raise HTTPException(status_code=400, detail="Empty attachments are not allowed.")
        if len(file_data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each attachment must be 10 MB or smaller.")

        total_attachment_bytes += len(file_data)
        if total_attachment_bytes > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Total attachments must be 20 MB or smaller.")

        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(
            file_data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    client = open_smtp(account)
    try:
        recipients = [str(x) for x in payload.to + payload.cc]
        client.send_message(message, to_addrs=recipients)
    except smtplib.SMTPException as exc:
        raise HTTPException(status_code=502, detail="SMTP could not send the message.") from exc
    finally:
        try:
            client.quit()
        except Exception:
            pass
    archived = True
    try:
        db.add(EmailFolderItem(
            owner_id=user.id,
            account_id=account.id,
            folder="sent",
            message={
                "to": ", ".join(str(x) for x in payload.to),
                "cc": ", ".join(str(x) for x in payload.cc),
                "from": account.email_address,
                "subject": payload.subject,
                "body": payload.body,
                "attachments": [
                    {"filename": a.filename, "content_type": a.content_type}
                    for a in payload.attachments
                ],
                "date": datetime.now(timezone.utc).isoformat(),
            },
        ))
        db.commit()
    except Exception:
        db.rollback()
        archived = False
    return {
        "status": "sent",
        "recipients": len(recipients),
        "archive_saved": archived,
    }
