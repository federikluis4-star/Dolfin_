#!/usr/bin/env python3
"""
Local browser UI for the interactive Support Copilot CLI.

It wraps bot.py in a pseudo-terminal so the existing input()/print() flow keeps
working, while exposing a small local web panel for start/stop/send actions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ui_runtime import BOT_PATH, BotSessionManager


CASE_MEMORY_DIR = BOT_PATH.parent / "logs" / "case_memory"
CASE_TRANSCRIPT_DIR = BOT_PATH.parent / "logs" / "case_transcripts"
CASE_BINDINGS_PATH = BOT_PATH.parent / "logs" / "ui_case_bindings.json"


def _parse_iso_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1] + "+00:00")
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _format_local_iso(raw: str) -> str:
    dt = _parse_iso_dt(raw)
    if not dt:
        return raw or ""
    return dt.astimezone().isoformat()


def _format_local_human(raw: str) -> str:
    dt = _parse_iso_dt(raw)
    if not dt:
        return raw or ""
    return dt.astimezone().strftime("%d.%m.%Y %H:%M")


def _add_business_days(start_dt: datetime, business_days: int) -> datetime:
    current = start_dt
    remaining = max(0, int(business_days or 0))
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _compute_follow_up_due(anchor_at: str, deadline: str) -> tuple[str, bool]:
    anchor_dt = _parse_iso_dt(anchor_at)
    deadline_text = (deadline or "").strip()
    if not anchor_dt or not deadline_text:
        return "", False

    hours_match = re.search(r"(\d+)\s*hours?", deadline_text, flags=re.I)
    if hours_match:
        due_dt = anchor_dt + timedelta(hours=int(hours_match.group(1)))
        return due_dt.astimezone().isoformat(), datetime.now(timezone.utc) >= due_dt.astimezone(timezone.utc)

    business_range_match = re.search(r"(\d+)\s*-\s*(\d+)\s*business\s*days?", deadline_text, flags=re.I)
    if business_range_match:
        due_dt = _add_business_days(anchor_dt, int(business_range_match.group(2)))
        return due_dt.astimezone().isoformat(), datetime.now(timezone.utc) >= due_dt.astimezone(timezone.utc)

    business_days_match = re.search(r"(\d+)\s*business\s*days?", deadline_text, flags=re.I)
    if business_days_match:
        due_dt = _add_business_days(anchor_dt, int(business_days_match.group(1)))
        return due_dt.astimezone().isoformat(), datetime.now(timezone.utc) >= due_dt.astimezone(timezone.utc)

    days_range_match = re.search(r"(\d+)\s*-\s*(\d+)\s*days?", deadline_text, flags=re.I)
    if days_range_match:
        due_dt = anchor_dt + timedelta(days=int(days_range_match.group(2)))
        return due_dt.astimezone().isoformat(), datetime.now(timezone.utc) >= due_dt.astimezone(timezone.utc)

    days_match = re.search(r"(\d+)\s*days?", deadline_text, flags=re.I)
    if days_match:
        due_dt = anchor_dt + timedelta(days=int(days_match.group(1)))
        return due_dt.astimezone().isoformat(), datetime.now(timezone.utc) >= due_dt.astimezone(timezone.utc)

    return "", False


def _canonical_store_name(raw: str) -> str:
    store = (raw or "").strip().lower()
    if "lenovo" in store:
        return "Lenovo.com"
    if "amazon" in store:
        return "Amazon"
    if "zara" in store:
        return "Zara.com"
    if "walmart" in store:
        return "Walmart"
    if "ebay" in store:
        return "eBay"
    return raw or "Lenovo.com"


def _case_store_prefix(raw: str) -> str:
    store = (raw or "").strip().lower()
    if "lenovo" in store:
        return "LV"
    if "amazon" in store:
        return "AMZ"
    if "zara" in store:
        return "ZR"
    if "walmart" in store:
        return "WM"
    if "ebay" in store:
        return "EBY"
    return "CASE"


def _run_case_type(raw: str) -> str:
    case_type = (raw or "").strip().lower()
    if case_type in {"inr", "item not received"}:
        return "INR"
    if any(token in case_type for token in ["defective", "broken", "damaged", "doa"]):
        return "DOA"
    if any(token in case_type for token in ["refund", "return"]):
        return "RNR"
    return "RNR"


def _parse_profile_metadata(profile_name: str) -> dict:
    parts = [part.strip() for part in (profile_name or "").split("_") if part.strip()]
    result = {"store": "", "case_type": ""}
    if not parts:
        return result
    store_map = {
        "amazon": "Amazon",
        "amz": "Amazon",
        "lenovo": "Lenovo.com",
        "len": "Lenovo.com",
        "lnv": "Lenovo.com",
        "zara": "Zara.com",
        "walmart": "Walmart",
        "wm": "Walmart",
        "ebay": "eBay",
        "ca": "Lenovo.com",
    }
    case_map = {
        "inr": "INR",
        "rnr": "RNR",
        "refund": "RNR",
        "doa": "DOA",
        "damaged": "DOA",
        "defective": "DOA",
        "broken": "DOA",
    }
    for part in parts[1:]:
        lowered = part.lower()
        if not result["store"] and lowered in store_map:
            result["store"] = store_map[lowered]
        if not result["case_type"] and lowered in case_map:
            result["case_type"] = case_map[lowered]
    return result


def _detect_intake_key(raw_line: str) -> tuple[str, str]:
    line = (raw_line or "").strip()
    if not line:
        return "", ""
    if ":" in line:
        left, right = line.split(":", 1)
        left = left.strip().lower()
        right = right.strip()
    else:
        left = line.strip().lower()
        right = ""

    key_aliases = [
        ("dolphin profile", "profile_name"),
        ("customer name", "customer_name"),
        ("phone number", "customer_phone"),
        ("order number", "order_num"),
        ("order #", "order_num"),
        ("номер заказа", "order_num"),
        ("customer email", "customer_email"),
        ("номер телефона", "customer_phone"),
        ("case type", "case_type"),
        ("case id", "resume_case_id"),
        ("номер кейса", "resume_case_id"),
        ("номер дела", "resume_case_id"),
        ("электронная почта", "customer_email"),
        ("dolphin", "profile_name"),
        ("profile", "profile_name"),
        ("профиль", "profile_name"),
        ("email", "customer_email"),
        ("e-mail", "customer_email"),
        ("почта", "customer_email"),
        ("mail", "customer_email"),
        ("phone", "customer_phone"),
        ("телефон", "customer_phone"),
        ("order", "order_num"),
        ("заказ", "order_num"),
        ("store", "store"),
        ("shop", "store"),
        ("магазин", "store"),
        ("case", "case_type"),
        ("тип", "case_type"),
        ("кейс", "resume_case_id"),
        ("ticket", "resume_case_id"),
        ("reference", "resume_case_id"),
        ("wait", "resume_follow_up_deadline"),
        ("timeline", "resume_follow_up_deadline"),
        ("deadline", "resume_follow_up_deadline"),
        ("ждать", "resume_follow_up_deadline"),
        ("срок", "resume_follow_up_deadline"),
        ("problem", "details"),
        ("issue", "details"),
        ("details", "details"),
        ("comment", "details"),
        ("проблема", "details"),
        ("детали", "details"),
        ("описание", "details"),
        ("name", "customer_name"),
        ("имя", "customer_name"),
    ]
    key_map = dict(key_aliases)
    direct_match = key_map.get(left, "")
    if direct_match:
        return direct_match, right
    for alias, target in key_aliases:
        match = re.match(rf"^{re.escape(alias)}(?:\s*[:#-]\s*|\s+)(.+?)\s*$", line, flags=re.I)
        if match:
            candidate = match.group(1).strip()
            if target == "profile_name" and not _looks_like_profile_name(candidate):
                continue
            if target == "customer_email" and not _looks_like_email(candidate):
                continue
            if target == "customer_phone" and not _looks_like_phone(candidate):
                continue
            if target == "order_num" and not _looks_like_order_number(candidate):
                continue
            if target == "resume_case_id" and not _extract_case_id(candidate):
                continue
            if target == "resume_follow_up_deadline" and not (_extract_follow_up_deadline(candidate) or _extract_wait_expired(candidate)):
                continue
            return target, candidate
    return "", ""


def _looks_like_email(value: str) -> bool:
    return bool(re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", (value or "").strip()))


def _looks_like_phone(value: str) -> bool:
    if re.search(r"[A-Za-zА-Яа-я]", value or ""):
        return False
    digits = re.sub(r"\D+", "", value or "")
    return 7 <= len(digits) <= 15


def _looks_like_order_number(value: str) -> bool:
    text = (value or "").strip()
    if " " in text:
        return False
    compact = re.sub(r"[^A-Za-z0-9]+", "", text)
    if not compact or _looks_like_email(text):
        return False
    if compact.upper() in {"INR", "RNR", "DOA"}:
        return False
    if re.search(r"[A-Za-zА-Яа-я]", text) and not re.fullmatch(r"[A-Za-z0-9._#-]+", text):
        return False
    return len(compact) >= 6 and any(ch.isdigit() for ch in compact)


def _looks_like_profile_name(value: str) -> bool:
    text = (value or "").strip()
    if not text or " " in text or _looks_like_email(text) or _looks_like_phone(text) or _extract_case_id(text):
        return False
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", text):
        return False
    return any(ch.isalpha() for ch in text)


def _looks_like_person_name(value: str) -> bool:
    text = (value or "").strip()
    if not text or _looks_like_email(text) or _looks_like_phone(text) or _looks_like_order_number(text):
        return False
    if len(text.split()) > 5:
        return False
    return bool(re.fullmatch(r"[A-Za-z .'-]+", text))


def _guess_case_type(details: str, explicit: str = "") -> str:
    if explicit:
        return _run_case_type(explicit)
    lowered = (details or "").lower()
    if any(token in lowered for token in ["defective", "broken", "damaged", "doa", "replacement", "screen"]):
        return "DOA"
    if any(token in lowered for token in ["not received", "never received", "missing package", "package missing", "inr"]):
        return "INR"
    if any(token in lowered for token in ["refund", "return", "returned", "refund pending", "refund overdue"]):
        return "RNR"
    return "RNR"


def _extract_case_id(value: str) -> str:
    match = re.search(r"\bC[A-Z]?\d{6,12}\b", value or "", flags=re.I)
    return match.group(0).upper() if match else ""


def _extract_profile_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for pattern in [
        r"(?:dolphin\s+profile|profile|профиль)\s*[:#-]?\s*([A-Za-z0-9._-]{3,40})\b",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            candidate = match.group(1).strip()
            if _looks_like_profile_name(candidate):
                return candidate
    for line in text.splitlines():
        candidate = line.strip()
        if _looks_like_profile_name(candidate):
            return candidate
    return ""


def _extract_email(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value or "")
    return match.group(0).strip() if match else ""


def _extract_phone(value: str) -> str:
    labeled = re.search(
        r"(?:phone(?:\s+number)?|телефон|номер\s+телефона)\s*[:#-]?\s*((?:\+?\d|\(\d)[\d()\s.-]{6,}\d)",
        value or "",
        flags=re.I,
    )
    if labeled:
        candidate = labeled.group(1).strip(" ,.;")
        if _looks_like_phone(candidate):
            return candidate
    for match in re.finditer(r"(?:\+?\d|\(\d)[\d()\s.-]{6,}\d", value or ""):
        candidate = match.group(0).strip(" ,.;")
        if not _looks_like_phone(candidate):
            continue
        if any(token in candidate for token in ("(", ")", " ", "-", ".")):
            return candidate
    return ""


def _extract_order_number(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for pattern in [
        r"(?:order(?:\s+number)?|order\s*#|номер\s+заказа|заказ(?:а|у|е)?)\s*[:#-]?\s*([A-Za-z0-9-]{6,})\b",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            candidate = match.group(1).strip()
            if _looks_like_order_number(candidate):
                return candidate
    return ""


def _extract_customer_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for pattern in [
        r"(?:customer\s+name|name|имя|клиент)\s*[:#-]?\s*([A-Za-z][A-Za-z .'-]{2,60})",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        candidate = match.group(1).strip(" ,.;")
        if _looks_like_person_name(candidate):
            return candidate
    return ""


def _extract_follow_up_deadline(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    range_sep = r"\s*[-–—]\s*"
    russian_business_range = re.search(rf"(\d+){range_sep}(\d+)\s*рабоч(?:их|ие)?\s*дн", text, flags=re.I)
    if russian_business_range:
        return f"{russian_business_range.group(1)}-{russian_business_range.group(2)} business days"
    russian_business_days = re.search(r"(\d+)\s*рабоч(?:их|ие)?\s*дн", text, flags=re.I)
    if russian_business_days:
        return f"{russian_business_days.group(1)} business days"
    russian_hours_range = re.search(rf"(\d+){range_sep}(\d+)\s*час", text, flags=re.I)
    if russian_hours_range:
        return f"{russian_hours_range.group(1)}-{russian_hours_range.group(2)} hours"
    russian_hours = re.search(r"(\d+)\s*час", text, flags=re.I)
    if russian_hours:
        return f"{russian_hours.group(1)} hours"
    russian_days_range = re.search(rf"(\d+){range_sep}(\d+)\s*дн", text, flags=re.I)
    if russian_days_range:
        return f"{russian_days_range.group(1)}-{russian_days_range.group(2)} days"
    russian_days = re.search(r"(\d+)\s*дн", text, flags=re.I)
    if russian_days:
        return f"{russian_days.group(1)} days"
    for pattern in [
        rf"\b\d+{range_sep}\d+\s*business\s*days?\b",
        r"\b\d+\s*business\s*days?\b",
        rf"\b\d+{range_sep}\d+\s*hours?\b",
        r"\b\d+\s*hours?\b",
        rf"\b\d+{range_sep}\d+\s*days?\b",
        r"\b\d+\s*days?\b",
    ]:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return ""


def _extract_wait_expired(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    markers = [
        "time already passed",
        "time has already passed",
        "deadline already passed",
        "the time passed",
        "already passed",
        "already expired",
        "window has passed",
        "window already passed",
        "deadline passed",
        "deadline has passed",
        "time is up",
        "expired already",
        "время уже вышло",
        "срок уже вышел",
        "срок уже истек",
        "срок уже истёк",
        "время прошло",
        "время уже прошло",
        "срок прошел",
        "срок прошёл",
        "срок истек",
        "срок истёк",
        "48 часов прошло",
        "48 часов уже прошло",
    ]
    return any(marker in text for marker in markers)


def _clean_intake_details(details: str, fields: dict) -> str:
    text = (details or "").strip()
    if not text:
        return ""

    removal_patterns: list[str] = []

    def add_labeled_pattern(labels: list[str], value: str) -> None:
        cleaned = (value or "").strip()
        if not cleaned:
            return
        escaped = re.escape(cleaned)
        label_group = "|".join(re.escape(label) for label in labels)
        removal_patterns.append(rf"(?:{label_group})\s*[:#-]?\s*{escaped}\b")

    add_labeled_pattern(["dolphin profile", "profile", "профиль"], fields.get("profile_name", ""))
    add_labeled_pattern(["customer name", "name", "имя"], fields.get("customer_name", ""))
    add_labeled_pattern(["customer email", "email", "e-mail", "почта", "электронная почта"], fields.get("customer_email", ""))
    add_labeled_pattern(["phone", "phone number", "телефон", "номер телефона"], fields.get("customer_phone", ""))
    add_labeled_pattern(["order", "order number", "order #", "номер заказа", "заказ", "заказа", "заказу"], fields.get("order_num", ""))
    add_labeled_pattern(["case id", "номер кейса", "номер дела", "кейс"], fields.get("resume_case_id", ""))

    for pattern in removal_patterns:
        text = re.sub(pattern, " ", text, flags=re.I)

    for key in ["customer_email", "customer_phone", "resume_case_id"]:
        value = (fields.get(key) or "").strip()
        if value:
            text = re.sub(re.escape(value), " ", text, flags=re.I)

    sentences: list[str] = []
    for fragment in re.split(r"(?<=[.!?])\s+|\n+", text):
        candidate = fragment.strip(" ,;:-")
        if not candidate:
            continue
        if not re.search(r"[A-Za-zА-Яа-я]", candidate):
            continue
        if not re.search(r"[A-Za-zА-Яа-я]{3,}", candidate):
            continue
        sentences.append(candidate)

    cleaned = " ".join(sentences) if sentences else text
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([.,!?])", r"\1", cleaned)
    cleaned = re.sub(r"([.,!?])(?=[A-Za-zА-Яа-я])", r"\1 ", cleaned)
    cleaned = re.sub(r"(?:\s*[,;:]\s*){2,}", ", ", cleaned)
    cleaned = re.sub(r"(^|[.!?]\s*)[,;:-]+\s*", r"\1", cleaned)
    cleaned = cleaned.strip(" ,;:-")
    return cleaned


def _case_binding_key(store: str, order_num: str) -> str:
    store_key = re.sub(r"[^a-z0-9]+", "-", (store or "unknown").lower()).strip("-") or "unknown"
    order_key = re.sub(r"[^a-z0-9]+", "", (order_num or "").lower()) or "noorder"
    return f"{store_key}_{order_key}"


def parse_intake_block(raw_text: str) -> dict:
    fields = {
        "profile_name": "",
        "customer_name": "",
        "customer_email": "",
        "customer_phone": "",
        "order_num": "",
        "store": "",
        "case_type": "",
        "resume_case_id": "",
        "resume_follow_up_deadline": "",
        "resume_wait_expired": False,
        "details": "",
    }
    if not (raw_text or "").strip():
        raise RuntimeError("Вставьте блок с данными кейса.")

    pending_key = ""
    details_lines: list[str] = []
    loose_lines: list[str] = []

    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if pending_key == "details":
                pending_key = ""
            continue
        key, value = _detect_intake_key(line)
        if key:
            if key == "details":
                if value:
                    details_lines.append(value)
                pending_key = "details"
                continue
            if key == "resume_case_id":
                extracted = _extract_case_id(value or line)
                if extracted:
                    fields["resume_case_id"] = extracted
                    pending_key = ""
                    continue
            if key == "resume_follow_up_deadline":
                extracted = _extract_follow_up_deadline(value or line)
                if extracted:
                    fields["resume_follow_up_deadline"] = extracted
                if _extract_wait_expired(value or line):
                    fields["resume_wait_expired"] = True
                pending_key = ""
                continue
            if value:
                fields[key] = value
                pending_key = ""
            else:
                pending_key = key
            continue

        if pending_key == "details":
            details_lines.append(line)
            continue
        if pending_key and not fields[pending_key]:
            fields[pending_key] = line
            pending_key = ""
            continue

        loose_lines.append(line)

    if not fields["profile_name"]:
        fields["profile_name"] = _extract_profile_name(raw_text)
    if loose_lines and not fields["profile_name"] and _looks_like_profile_name(loose_lines[0]):
        fields["profile_name"] = loose_lines.pop(0)
    elif loose_lines and fields["profile_name"] and loose_lines[0].strip() == fields["profile_name"]:
        loose_lines.pop(0)

    remaining_lines: list[str] = []
    for line in loose_lines:
        upper = line.strip().upper()
        metadata_like_line = len(line) <= 64 and "," not in line and "." not in line
        if not fields["resume_case_id"]:
            extracted_case_id = _extract_case_id(line)
            if extracted_case_id:
                fields["resume_case_id"] = extracted_case_id
                if _extract_wait_expired(line):
                    fields["resume_wait_expired"] = True
                if metadata_like_line:
                    continue
        if not fields["resume_follow_up_deadline"]:
            extracted_deadline = _extract_follow_up_deadline(line)
            if extracted_deadline:
                fields["resume_follow_up_deadline"] = extracted_deadline
                if _extract_wait_expired(line):
                    fields["resume_wait_expired"] = True
                if metadata_like_line:
                    continue
        if _extract_wait_expired(line):
            fields["resume_wait_expired"] = True
        if not fields["customer_email"] and _looks_like_email(line):
            fields["customer_email"] = line.strip()
            continue
        if not fields["customer_phone"] and _looks_like_phone(line):
            fields["customer_phone"] = line.strip()
            continue
        if not fields["order_num"] and _looks_like_order_number(line):
            fields["order_num"] = line.strip()
            continue
        if not fields["case_type"] and upper in {"INR", "RNR", "DOA"}:
            fields["case_type"] = upper
            continue
        if not fields["customer_name"] and _looks_like_person_name(line):
            fields["customer_name"] = line.strip()
            continue
        remaining_lines.append(line)

    if not fields["details"] and details_lines:
        fields["details"] = "\n".join(details_lines).strip()
    elif details_lines:
        fields["details"] = "\n".join([fields["details"], *details_lines]).strip()

    if remaining_lines:
        extra_details = "\n".join(remaining_lines).strip()
        fields["details"] = "\n".join(part for part in [fields["details"], extra_details] if part).strip()

    if fields["details"]:
        if not fields["resume_case_id"]:
            fields["resume_case_id"] = _extract_case_id(fields["details"])
        if not fields["resume_follow_up_deadline"]:
            fields["resume_follow_up_deadline"] = _extract_follow_up_deadline(fields["details"])
        if _extract_wait_expired(fields["details"]):
            fields["resume_wait_expired"] = True
        detail_lines = [line.strip() for line in fields["details"].splitlines() if line.strip()]
        if detail_lines and not fields["order_num"] and _looks_like_order_number(detail_lines[0]):
            fields["order_num"] = detail_lines.pop(0)
        if detail_lines and not fields["customer_phone"] and _looks_like_phone(detail_lines[0]):
            fields["customer_phone"] = detail_lines.pop(0)
        fields["details"] = "\n".join(detail_lines).strip()

    source_text = raw_text or ""
    if not fields["profile_name"]:
        fields["profile_name"] = _extract_profile_name(source_text)
    if not fields["customer_email"]:
        fields["customer_email"] = _extract_email(source_text)
    if not fields["customer_phone"]:
        fields["customer_phone"] = _extract_phone(source_text)
    if not fields["order_num"]:
        fields["order_num"] = _extract_order_number(source_text)
    if not fields["customer_name"]:
        fields["customer_name"] = _extract_customer_name(source_text)
    if not fields["resume_case_id"]:
        fields["resume_case_id"] = _extract_case_id(source_text)
    if not fields["resume_follow_up_deadline"]:
        fields["resume_follow_up_deadline"] = _extract_follow_up_deadline(source_text)
    if _extract_wait_expired(source_text):
        fields["resume_wait_expired"] = True

    fields["details"] = _clean_intake_details(fields["details"], fields)

    profile_meta = _parse_profile_metadata(fields["profile_name"])
    if not fields["store"]:
        fields["store"] = profile_meta.get("store") or "Lenovo.com"
    fields["store"] = _canonical_store_name(fields["store"])
    fields["case_type"] = _guess_case_type(fields["details"], explicit=fields["case_type"] or profile_meta.get("case_type") or "")

    fields["profile_name"] = fields["profile_name"].strip()
    fields["customer_name"] = fields["customer_name"].strip()
    fields["customer_email"] = fields["customer_email"].strip()
    fields["customer_phone"] = fields["customer_phone"].strip()
    fields["order_num"] = fields["order_num"].strip()
    fields["details"] = fields["details"].strip()
    fields["resume_case_id"] = fields["resume_case_id"].strip()
    fields["resume_follow_up_deadline"] = fields["resume_follow_up_deadline"].strip()

    if not fields["profile_name"]:
        raise RuntimeError("В блоке не найден Dolphin profile.")
    if not any([fields["customer_name"], fields["customer_email"], fields["customer_phone"], fields["order_num"]]):
        raise RuntimeError("В блоке не найдены данные клиента или номер заказа.")
    return fields


def build_intake_launch_config(raw_text: str) -> dict:
    parsed = parse_intake_block(raw_text)
    if parsed["order_num"]:
        save_case_binding(_case_binding_key(parsed["store"], parsed["order_num"]), parsed["profile_name"])
    return {
        "profile_name": parsed["profile_name"],
        "autopilot": False,
        "store": parsed["store"],
        "case_type": parsed["case_type"],
        "use_block": False,
        "order_num": parsed["order_num"],
        "amount": "",
        "details": parsed["details"],
        "customer_name": parsed["customer_name"],
        "customer_email": parsed["customer_email"],
        "customer_phone": parsed["customer_phone"],
        "resume_case_id": parsed["resume_case_id"],
        "resume_follow_up_deadline": parsed["resume_follow_up_deadline"],
        "resume_wait_expired": parsed["resume_wait_expired"],
        "prechat_only": False,
        "auto_send_noncritical": True,
        "auto_send_critical": True,
        "force_auto_mode": True,
        "dolphin_session_token": os.getenv("DOLPHIN_SESSION_TOKEN", ""),
        "dolphin_cloud_api_key": os.getenv("DOLPHIN_CLOUD_API_KEY", ""),
    }


def parse_case_update_block(raw_text: str) -> dict:
    fields = {
        "customer_name": "",
        "customer_email": "",
        "customer_phone": "",
        "order_num": "",
        "details": "",
    }
    if not (raw_text or "").strip():
        raise RuntimeError("Вставьте данные для обновления кейса.")

    pending_key = ""
    detail_lines: list[str] = []
    loose_lines: list[str] = []

    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if pending_key == "details":
                pending_key = ""
            continue
        key, value = _detect_intake_key(line)
        if key in {"customer_name", "customer_email", "customer_phone", "order_num", "details"}:
            if key == "details":
                if value:
                    detail_lines.append(value)
                pending_key = "details"
                continue
            if value:
                fields[key] = value
                pending_key = ""
            else:
                pending_key = key
            continue
        if pending_key == "details":
            detail_lines.append(line)
            continue
        if pending_key and not fields[pending_key]:
            fields[pending_key] = line
            pending_key = ""
            continue
        loose_lines.append(line)

    remaining_lines: list[str] = []
    for line in loose_lines:
        if not fields["customer_email"] and _looks_like_email(line):
            fields["customer_email"] = line.strip()
            continue
        if not fields["customer_phone"] and _looks_like_phone(line):
            fields["customer_phone"] = line.strip()
            continue
        if not fields["order_num"] and _looks_like_order_number(line):
            fields["order_num"] = line.strip()
            continue
        if not fields["customer_name"] and _looks_like_person_name(line):
            fields["customer_name"] = line.strip()
            continue
        remaining_lines.append(line)

    details = "\n".join([*detail_lines, *remaining_lines]).strip()
    if details:
        fields["details"] = details

    source_text = raw_text or ""
    if not fields["customer_email"]:
        fields["customer_email"] = _extract_email(source_text)
    if not fields["customer_phone"]:
        fields["customer_phone"] = _extract_phone(source_text)
    if not fields["order_num"]:
        fields["order_num"] = _extract_order_number(source_text)
    if not fields["customer_name"]:
        fields["customer_name"] = _extract_customer_name(source_text)
    if not fields["details"]:
        fields["details"] = source_text.strip()

    if not any(value.strip() for value in fields.values()):
        raise RuntimeError("Не удалось распознать новые данные кейса.")
    return fields


def load_case_bindings() -> dict:
    try:
        if not CASE_BINDINGS_PATH.exists():
            return {}
        data = json.loads(CASE_BINDINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_case_binding(case_key: str, profile_name: str) -> dict:
    bindings = load_case_bindings()
    current = bindings.get(case_key, {}) if isinstance(bindings.get(case_key, {}), dict) else {}
    current["profile_name"] = (profile_name or "").strip()
    bindings[case_key] = current
    CASE_BINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASE_BINDINGS_PATH.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def _case_status_label(dialogue_state: str, due_passed: bool) -> tuple[str, str]:
    mapping = {
        "case_opened_waiting": ("Срок ожидания вышел" if due_passed else "Ждём апдейт магазина", "warn" if due_passed else "ok"),
        "escalated_pending_timeline": ("Эскалация открыта", "ok"),
        "timeline_offered": ("Дали срок", "ok"),
        "denial_or_deflection": ("Нужен жёсткий follow-up", "warn"),
        "holding": ("Диалог на паузе", "muted"),
        "opening": ("Кейс только открыт", "muted"),
        "active_negotiation": ("Идут переговоры", "ok"),
    }
    return mapping.get(dialogue_state or "", ("Статус не определён", "muted"))


def _case_next_step(data: dict, due_at: str, due_passed: bool) -> str:
    case_id = data.get("latest_case_id") or ""
    deadline = data.get("follow_up_deadline") or ""
    dialogue_state = data.get("dialogue_state") or ""
    due_text = _format_local_human(due_at) if due_at else ""

    if dialogue_state == "case_opened_waiting":
        if due_at and due_passed:
            return (
                f"Срок {deadline} уже прошёл. Нужно писать follow-up сегодня"
                + (f" по case ID {case_id}." if case_id else ".")
            )
        if due_text:
            return (
                f"Дождаться срока до {due_text} и, если ответа не будет,"
                + (f" написать follow-up по case ID {case_id}." if case_id else " написать повторный запрос.")
            )
        return "Ждём обещанный апдейт магазина и затем проверяем, был ли ответ."

    if dialogue_state in {"denial_or_deflection", "active_negotiation"}:
        return (
            "Следующий шаг: вернуться в чат и потребовать конкретный action item,"
            + (f" с опорой на case ID {case_id}." if case_id else " case ID и срок.")
        )

    if case_id:
        return f"Следующий шаг: продолжить кейс с опорой на case ID {case_id} и запросить письменный срок."
    return "Следующий шаг: открыть чат и зафиксировать case ID, owner и срок ответа."


def _requested_field_human(raw: str) -> str:
    mapping = {
        "email": "email",
        "phone": "phone",
        "order": "order number",
        "name": "name",
    }
    return mapping.get((raw or "").strip(), raw or "")


def _case_title(store: str, profile_name: str, customer_email: str, order_num: str) -> str:
    prefix = _case_store_prefix(store)
    base_name = (profile_name or "").strip() or (order_num or "").strip() or "untitled"
    email = (customer_email or "").strip()
    title = f"{prefix}-{base_name}"
    if email:
        title += f" ({email})"
    return title


def _case_subtitle(store: str, order_num: str, customer_name: str, case_type: str) -> str:
    meta = []
    if store:
        meta.append(store)
    if order_num and order_num != "Без номера":
        meta.append(order_num)
    if meta:
        return " / ".join(meta)
    return customer_name or case_type or "Без названия"


def _build_case_summary(path: Path, data: dict, bindings: dict | None = None) -> dict:
    bindings = bindings or {}
    binding = bindings.get(path.stem, {}) if isinstance(bindings.get(path.stem, {}), dict) else {}
    updated_at = data.get("updated_at", "")
    updated_dt = _parse_iso_dt(updated_at)
    last_event_at = data.get("last_event_at") or updated_at
    last_event_dt = _parse_iso_dt(last_event_at)
    follow_up_anchor_at = data.get("follow_up_anchor_at") or last_event_at or updated_at
    due_at, due_passed = _compute_follow_up_due(follow_up_anchor_at, data.get("follow_up_deadline", ""))
    status_label, status_tone = _case_status_label(data.get("dialogue_state", ""), due_passed)
    order_num = data.get("order_num") or "Без номера"
    store = data.get("store") or "unknown"
    profile_name = (binding.get("profile_name") or "").strip()
    customer_email = data.get("customer_email") or ""
    latest_case_id = data.get("latest_case_id") or ""
    transcript_tail = data.get("transcript_tail") or []
    transcript_count = int(data.get("transcript_count") or len(transcript_tail) or 0)
    transcript_path_raw = data.get("transcript_path") or str(CASE_TRANSCRIPT_DIR / f"{path.stem}.json")
    transcript_path = Path(transcript_path_raw)
    if transcript_path.exists():
        try:
            full_transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            if isinstance(full_transcript, list):
                transcript_count = max(transcript_count, len(full_transcript))
        except Exception:
            pass

    return {
        "key": path.stem,
        "file_name": path.name,
        "title": _case_title(store, profile_name, customer_email, order_num),
        "subtitle": _case_subtitle(store, order_num, data.get("customer_name") or "", data.get("case_type") or ""),
        "store": store,
        "case_type": data.get("case_type") or "",
        "order_num": order_num,
        "customer_name": data.get("customer_name") or "",
        "customer_email": customer_email,
        "customer_phone": data.get("customer_phone") or "",
        "profile_name": profile_name,
        "latest_case_id": latest_case_id,
        "latest_case_outcome": data.get("latest_case_outcome") or "",
        "follow_up_deadline": data.get("follow_up_deadline") or "",
        "follow_up_due_at": due_at,
        "follow_up_due_human": _format_local_human(due_at) if due_at else "",
        "follow_up_due_passed": due_passed,
        "dialogue_state": data.get("dialogue_state") or "",
        "status_label": status_label,
        "status_tone": status_tone,
        "updated_at": _format_local_iso(updated_at),
        "saved_at": _format_local_iso(updated_at),
        "last_event_at": _format_local_iso(last_event_at),
        "last_event_human": _format_local_human(last_event_at),
        "follow_up_anchor_at": _format_local_iso(follow_up_anchor_at),
        "follow_up_anchor_human": _format_local_human(follow_up_anchor_at),
        "sort_ts": (last_event_dt or updated_dt).timestamp() if (last_event_dt or updated_dt) else 0,
        "last_agent_message": data.get("last_agent_message") or "",
        "last_customer_message": data.get("last_customer_message") or "",
        "next_step": _case_next_step(data, due_at, due_passed),
        "transcript_count": transcript_count,
        "transcript_path": str(transcript_path),
        "operator_notes": list(data.get("operator_notes") or [])[-4:],
        "pending_requested_field": data.get("pending_requested_field") or "",
        "pending_requested_field_human": _requested_field_human(data.get("pending_requested_field") or ""),
        "confirmed_facts": list(data.get("confirmed_facts") or [])[-6:],
        "unresolved_demands": list(data.get("unresolved_demands") or [])[-6:],
        "contradictions": list(data.get("contradictions") or [])[-4:],
        "transcript_tail": transcript_tail[-6:],
    }


def load_case_summaries() -> dict:
    cases = []
    bindings = load_case_bindings()
    if CASE_MEMORY_DIR.exists():
        for path in CASE_MEMORY_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cases.append(_build_case_summary(path, data, bindings=bindings))

    cases.sort(key=lambda item: item.get("sort_ts") or 0, reverse=True)
    current_case_key = cases[0]["key"] if cases else ""
    return {
        "cases": cases,
        "current_case_key": current_case_key,
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def get_case_summary(case_key: str) -> dict | None:
    for item in load_case_summaries().get("cases", []):
        if item.get("key") == case_key:
            return item
    return None


def build_case_launch_config(case_key: str) -> dict:
    case = get_case_summary(case_key)
    if not case:
        raise RuntimeError("Кейс не найден.")

    profile_name = (case.get("profile_name") or "").strip()
    if not profile_name:
        raise RuntimeError("Сначала сохраните название профиля Dolphin для этого кейса.")

    return {
        "profile_name": profile_name,
        "autopilot": False,
        "store": _canonical_store_name(case.get("store") or ""),
        "case_type": _run_case_type(case.get("case_type") or ""),
        "use_block": False,
        "order_num": case.get("order_num") or "",
        "amount": "",
        "details": case.get("latest_case_outcome") or case.get("last_agent_message") or "",
        "customer_name": case.get("customer_name") or "",
        "customer_email": case.get("customer_email") or "",
        "customer_phone": case.get("customer_phone") or "",
        "prechat_only": False,
        "auto_send_noncritical": True,
        "auto_send_critical": True,
        "force_auto_mode": True,
        # Keep token prompts suppressed when the UI launches a saved case.
        "dolphin_session_token": os.getenv("DOLPHIN_SESSION_TOKEN", ""),
        "dolphin_cloud_api_key": os.getenv("DOLPHIN_CLOUD_API_KEY", ""),
    }


HTML_PAGE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Support Copilot Control Panel</title>
  <style>
    :root {
      --bg: #f4efe7;
      --paper: rgba(255, 250, 243, 0.88);
      --ink: #221b15;
      --muted: #655a4f;
      --line: rgba(34, 27, 21, 0.14);
      --accent: #154734;
      --accent-2: #af3f27;
      --accent-soft: rgba(21, 71, 52, 0.14);
      --warning-soft: rgba(175, 63, 39, 0.12);
      --shadow: 0 18px 60px rgba(70, 45, 25, 0.12);
      --radius: 24px;
      --terminal: #171412;
      --terminal-text: #f8eee1;
      --terminal-muted: #bda98d;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(244, 211, 153, 0.42), transparent 35%),
        radial-gradient(circle at top right, rgba(21, 71, 52, 0.16), transparent 28%),
        linear-gradient(160deg, #f9f4ee 0%, #f1e7da 52%, #ece4d8 100%);
      font-family: Georgia, "Times New Roman", serif;
    }

    .shell {
      width: min(1280px, calc(100vw - 32px));
      margin: 24px auto;
      display: grid;
      gap: 18px;
    }

    .hero, .panel {
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }

    .hero {
      padding: 28px;
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.9fr);
      gap: 18px;
      align-items: start;
    }

    .eyebrow {
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 12px;
    }

    h1 {
      margin: 0 0 10px;
      font-size: clamp(32px, 5vw, 52px);
      line-height: 0.95;
      letter-spacing: -0.03em;
    }

    .lead {
      margin: 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.5;
      max-width: 62ch;
    }

    .status-card {
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.74), rgba(255,255,255,0.4)),
        var(--accent-soft);
    }

    .status-top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 12px;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      background: rgba(255,255,255,0.72);
      border: 1px solid var(--line);
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent-2);
      box-shadow: 0 0 0 4px rgba(175, 63, 39, 0.16);
    }

    .status-pill.running .status-dot {
      background: #1f7a53;
      box-shadow: 0 0 0 4px rgba(31, 122, 83, 0.16);
    }

    .meta {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }

    .workspace {
      display: grid;
      grid-template-columns: minmax(300px, 1.65fr) minmax(280px, 0.85fr);
      gap: 18px;
    }

    .panel {
      padding: 20px;
    }

    .panel h2 {
      margin: 0 0 8px;
      font-size: 22px;
    }

    .panel p {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }

    button {
      appearance: none;
      border: 0;
      cursor: pointer;
      border-radius: 999px;
      padding: 12px 16px;
      font: inherit;
      font-weight: 700;
      transition: transform 120ms ease, box-shadow 120ms ease, opacity 120ms ease;
    }

    button:hover { transform: translateY(-1px); }
    button:active { transform: translateY(0); }
    button:disabled { opacity: 0.45; cursor: default; transform: none; }

    .primary {
      color: #f8f3ea;
      background: linear-gradient(135deg, #1a5e45, #154734);
      box-shadow: 0 10px 20px rgba(21, 71, 52, 0.2);
    }

    .secondary {
      color: var(--ink);
      background: rgba(255,255,255,0.68);
      border: 1px solid var(--line);
    }

    .danger {
      color: #fff5f1;
      background: linear-gradient(135deg, #c25234, #98301b);
      box-shadow: 0 10px 20px rgba(152, 48, 27, 0.16);
    }

    .quick-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }

    .quick-row button {
      padding-inline: 14px;
      min-width: 84px;
    }

    .terminal-wrap {
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid rgba(255,255,255,0.08);
      background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
    }

    .terminal-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 16px;
      color: var(--terminal-muted);
      background: #1f1a17;
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 13px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }

    .terminal {
      margin: 0;
      min-height: 540px;
      max-height: 68vh;
      overflow: auto;
      padding: 18px;
      white-space: pre-wrap;
      color: var(--terminal-text);
      background:
        radial-gradient(circle at top right, rgba(244, 211, 153, 0.08), transparent 24%),
        linear-gradient(180deg, #171412 0%, #1b1613 100%);
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      font-size: 14px;
      line-height: 1.5;
    }

    .input-shell {
      display: grid;
      gap: 14px;
    }

    label {
      display: block;
      margin-bottom: 8px;
      font-size: 14px;
      font-weight: 700;
      color: var(--muted);
      letter-spacing: 0.02em;
    }

    textarea {
      width: 100%;
      min-height: 180px;
      resize: vertical;
      border-radius: 20px;
      border: 1px solid var(--line);
      padding: 16px 18px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.74);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.36);
    }

    .text-input {
      width: 100%;
      min-height: auto;
      border-radius: 16px;
      border: 1px solid var(--line);
      padding: 12px 14px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.74);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.36);
    }

    textarea:focus,
    .text-input:focus {
      outline: 2px solid rgba(21, 71, 52, 0.18);
      border-color: rgba(21, 71, 52, 0.28);
    }

    .hint {
      padding: 14px 16px;
      border-radius: 18px;
      background: var(--warning-soft);
      color: #6e3f31;
      line-height: 1.45;
      font-size: 14px;
    }

    .steps {
      margin-top: 18px;
      display: grid;
      gap: 10px;
    }

    .step {
      padding: 12px 14px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.48);
      color: var(--muted);
    }

    .footer-note {
      font-size: 13px;
      color: var(--muted);
      margin-top: 16px;
    }

    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }

    .section-head .secondary {
      flex-shrink: 0;
    }

    .cases-board {
      display: grid;
      grid-template-columns: minmax(280px, 0.82fr) minmax(360px, 1.18fr);
      gap: 18px;
    }

    .case-list {
      margin-top: 18px;
      display: grid;
      gap: 10px;
    }

    .case-item {
      width: 100%;
      text-align: left;
      padding: 16px 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.55);
      display: grid;
      gap: 8px;
      box-shadow: none;
    }

    .case-item.active {
      background: linear-gradient(180deg, rgba(21, 71, 52, 0.12), rgba(255,255,255,0.7));
      border-color: rgba(21, 71, 52, 0.28);
    }

    .case-item-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }

    .case-title {
      font-size: 16px;
      color: var(--ink);
    }

    .case-subtitle, .case-meta {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .case-timer {
      color: var(--ink);
      font-weight: 700;
    }

    .case-timer.ready {
      color: #8a381f;
    }

    .case-status {
      display: inline-flex;
      align-items: center;
      width: fit-content;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.7);
    }

    .case-status.ok {
      color: #0f5e40;
      background: rgba(21, 71, 52, 0.12);
    }

    .case-status.warn {
      color: #8a381f;
      background: rgba(175, 63, 39, 0.12);
    }

    .case-status.muted {
      color: var(--muted);
      background: rgba(255,255,255,0.7);
    }

    .case-detail {
      margin-top: 18px;
      display: grid;
      gap: 14px;
    }

    .detail-summary {
      padding: 16px 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.5);
    }

    .detail-summary strong {
      display: block;
      margin-bottom: 8px;
      font-size: 14px;
    }

    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .detail-card {
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.58);
    }

    .detail-label {
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .detail-value {
      color: var(--ink);
      line-height: 1.5;
      word-break: break-word;
    }

    .inline-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }

    .timer-value {
      display: block;
      font-size: 20px;
      font-weight: 700;
      line-height: 1.2;
      color: var(--ink);
    }

    .timer-value.ready {
      color: #8a381f;
    }

    .timer-caption {
      display: block;
      margin-top: 8px;
      color: var(--muted);
      line-height: 1.45;
    }

    .mini-list, .timeline {
      display: grid;
      gap: 8px;
    }

    .mini-item, .timeline-item {
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.45);
      color: var(--muted);
      line-height: 1.45;
    }

    .timeline-role {
      display: block;
      margin-bottom: 6px;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .empty-state {
      padding: 18px;
      border-radius: 18px;
      border: 1px dashed var(--line);
      color: var(--muted);
      background: rgba(255,255,255,0.4);
      line-height: 1.45;
    }

    @media (max-width: 980px) {
      .hero, .workspace, .cases-board, .detail-grid {
        grid-template-columns: 1fr;
      }

      .terminal {
        min-height: 360px;
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <div class="eyebrow">Support Copilot / Browser Console</div>
        <h1>Интерфейс для юрбота поддержки</h1>
        <p class="lead">
          Эта панель запускает текущий <code>bot.py</code> в фоне и показывает его живой вывод.
          Логику бота менять не нужно: вы просто отвечаете на его вопросы через браузер вместо терминала.
        </p>
        <div class="steps">
          <div class="step">1. Нажмите «Запустить бота» и дождитесь первых вопросов.</div>
          <div class="step">2. Вставляйте ответы в нижнее поле. Для блока клиента используйте «Отправить как блок».</div>
          <div class="step">3. Следите за журналом слева: туда попадает весь вывод <code>bot.py</code> в реальном времени.</div>
        </div>
      </div>
      <aside class="status-card">
        <div class="status-top">
          <div id="status-pill" class="status-pill">
            <span class="status-dot"></span>
            <span id="status-label">Остановлен</span>
          </div>
          <div id="last-update" class="meta">Нет данных</div>
        </div>
        <div class="meta">
          <div><strong>Команда:</strong> <code>python3 bot.py</code></div>
          <div><strong>Интерфейс:</strong> <code>web_ui.py</code></div>
          <div><strong>Хост:</strong> <code id="ui-url">http://127.0.0.1</code></div>
          <div><strong>Запуск процесса:</strong> <span id="started-at">не запущен</span></div>
          <div><strong>Код завершения:</strong> <span id="exit-code">—</span></div>
        </div>
        <div class="toolbar">
          <button id="start-btn" class="primary">Запустить бота</button>
          <button id="stop-btn" class="danger">Остановить</button>
          <button id="refresh-btn" class="secondary">Обновить лог</button>
        </div>
        <div class="footer-note">
          Если у вас уже настроен <code>.env</code>, бот возьмёт ключи оттуда. Если нет, он сам спросит их в логе.
        </div>
      </aside>
    </section>

    <section class="panel">
      <h2>Новый кейс одним блоком</h2>
      <p>Вставьте профиль, данные клиента и описание проблемы одним текстом. Интерфейс сам соберёт стартовый конфиг и запустит бота без пошагового опроса.</p>
      <div class="input-shell">
        <div>
          <label for="intake-box">Стартовый блок</label>
          <textarea id="intake-box" placeholder="Katrin_NJ&#10;Diana Bardian&#10;omeli09@zohomail.com&#10;929-990-8067&#10;4649951015&#10;Returned defective laptop to Lenovo with UPS label; tracking shows delivered back, replacement never arrived, refund still pending."></textarea>
        </div>
        <div class="toolbar">
          <button id="start-intake-btn" class="primary">Запустить новый кейс</button>
          <button id="clear-intake-btn" class="secondary">Очистить блок</button>
        </div>
        <div class="hint">
          Поддерживаются оба варианта:
          простой порядок строк <code>profile / name / email / phone / order / проблема</code>
          или подписи вроде <code>profile:</code>, <code>email:</code>, <code>order:</code>, <code>problem:</code>.
        </div>
      </div>
    </section>

    <section class="workspace">
      <div class="panel">
        <h2>Живой журнал</h2>
        <p>Полный stdout/stderr текущей сессии. Здесь видны подсказки, статусы и ошибки запуска.</p>
        <div class="terminal-wrap">
          <div class="terminal-top">
            <span>CLI session output</span>
            <span id="cursor-info">cursor: 0</span>
          </div>
          <pre id="terminal" class="terminal"></pre>
        </div>
      </div>

      <div class="panel">
        <h2>Ответ боту</h2>
        <p>Однострочные ответы отправляйте обычной кнопкой. Ниже также можно дать подсказку текущей сессии бота или обновить данные кейса без перезапуска.</p>
        <div class="input-shell">
          <div>
            <label for="input-box">Текст ответа</label>
            <textarea id="input-box" placeholder="Например: Luna_CA&#10;или многострочный блок name/order/email/phone"></textarea>
          </div>
          <div class="toolbar">
            <button id="send-btn" class="primary">Отправить строку</button>
            <button id="send-block-btn" class="secondary">Отправить как блок</button>
            <button id="clear-btn" class="secondary">Очистить поле</button>
          </div>
          <div class="quick-row">
            <button data-quick="y" class="secondary">y</button>
            <button data-quick="n" class="secondary">n</button>
            <button data-quick="" class="secondary">Enter</button>
            <button data-quick="Lenovo.com" class="secondary">Lenovo.com</button>
            <button data-quick="INR" class="secondary">INR</button>
          </div>
          <div class="hint">
            Для блока клиента интерфейс отправляет дополнительную пустую строку в конце, чтобы
            завершить ввод в <code>read_multiline_block()</code>.
          </div>
          <div>
            <label for="hint-box">Подсказка боту</label>
            <textarea id="hint-box" placeholder="Например: не уходи в chargeback, сначала добейся письменного policy basis и owner case."></textarea>
          </div>
          <div class="toolbar">
            <button id="send-hint-btn" class="secondary">Подсказка боту</button>
            <button id="clear-hint-btn" class="secondary">Очистить подсказку</button>
          </div>
          <div class="hint">
            Подсказка не отправляется оператору напрямую. Она добавляется в память текущей сессии и влияет на следующий ответ бота.
          </div>
          <div>
            <label for="case-update-box">Обновить данные кейса</label>
            <textarea id="case-update-box" placeholder="email: omeli09@zohomail.com&#10;phone: 929-990-8067&#10;order: 4649951015&#10;name: Diana Bardian"></textarea>
          </div>
          <div class="toolbar">
            <button id="update-case-data-btn" class="secondary">Обновить данные кейса</button>
            <button id="clear-case-update-btn" class="secondary">Очистить данные</button>
          </div>
          <div class="hint">
            Можно вставлять одним блоком только то, чего не хватало: <code>email</code>, <code>phone</code>, <code>order</code>, <code>name</code> и при необходимости краткие <code>details</code>.
          </div>
        </div>
      </div>
    </section>

    <section class="cases-board">
      <div class="panel">
        <div class="section-head">
          <div>
            <h2>Кейсы</h2>
            <p>Сохранённые кейсы из <code>logs/case_memory</code>. Выберите кейс, сохраните профиль Dolphin и запускайте продолжение диалога вручную.</p>
          </div>
          <button id="refresh-cases-btn" class="secondary">Обновить кейсы</button>
        </div>
        <div id="case-list" class="case-list"></div>
      </div>

      <div class="panel">
        <h2>На чём остановились</h2>
        <p>Автосводка по выбранному кейсу: статус, дедлайн, последний апдейт и следующий шаг.</p>
        <div id="case-detail" class="case-detail"></div>
      </div>
    </section>
  </div>

  <script>
    const terminalEl = document.getElementById("terminal");
    const inputEl = document.getElementById("input-box");
    const intakeEl = document.getElementById("intake-box");
    const hintEl = document.getElementById("hint-box");
    const caseUpdateEl = document.getElementById("case-update-box");
    const statusPillEl = document.getElementById("status-pill");
    const statusLabelEl = document.getElementById("status-label");
    const startedAtEl = document.getElementById("started-at");
    const exitCodeEl = document.getElementById("exit-code");
    const cursorInfoEl = document.getElementById("cursor-info");
    const lastUpdateEl = document.getElementById("last-update");
    const uiUrlEl = document.getElementById("ui-url");
    const caseListEl = document.getElementById("case-list");
    const caseDetailEl = document.getElementById("case-detail");

    let cursor = 0;
    let pollTimer = null;
    let casesTimer = null;
    let selectedCaseKey = null;
    let casesCache = [];

    uiUrlEl.textContent = window.location.origin;

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "Request failed");
      }
      return data;
    }

    function appendTerminal(text, reset = false) {
      const shouldStick =
        terminalEl.scrollTop + terminalEl.clientHeight >= terminalEl.scrollHeight - 32;

      if (reset) {
        terminalEl.textContent = text;
      } else if (text) {
        terminalEl.textContent += text;
      }

      if (shouldStick || reset) {
        terminalEl.scrollTop = terminalEl.scrollHeight;
      }
    }

    function formatDate(value, emptyLabel = "не запущен") {
      if (!value) return emptyLabel;
      const dt = new Date(value);
      if (Number.isNaN(dt.getTime())) return value;
      return dt.toLocaleString("ru-RU");
    }

    function formatDuration(totalSeconds) {
      const seconds = Math.max(0, Math.floor(totalSeconds || 0));
      const days = Math.floor(seconds / 86400);
      const hours = Math.floor((seconds % 86400) / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = seconds % 60;
      if (days > 0) return `${days}д ${hours}ч ${minutes}м`;
      if (hours > 0) return `${hours}ч ${minutes}м`;
      if (minutes > 0) return `${minutes}м ${secs}с`;
      return `${secs}с`;
    }

    function getTimerSnapshot(anchorAt, dueAt) {
      const nowMs = Date.now();
      const anchorMs = anchorAt ? Date.parse(anchorAt) : Number.NaN;
      const dueMs = dueAt ? Date.parse(dueAt) : Number.NaN;
      const hasAnchor = !Number.isNaN(anchorMs);
      const hasDue = !Number.isNaN(dueMs);
      const elapsedSeconds = hasAnchor ? Math.max(0, Math.floor((nowMs - anchorMs) / 1000)) : null;
      const remainingSeconds = hasDue ? Math.floor((dueMs - nowMs) / 1000) : null;

      return {
        elapsedLabel: hasAnchor ? formatDuration(elapsedSeconds) : "—",
        remainingLabel: !hasDue ? "—" : remainingSeconds <= 0 ? "Пора писать" : formatDuration(remainingSeconds),
        remainingReady: Boolean(hasDue && remainingSeconds <= 0),
      };
    }

    function escapeHtml(value) {
      return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function renderSimpleList(items, emptyText) {
      if (!items || !items.length) {
        return `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
      }
      return `<div class="mini-list">${items
        .map((item) => `<div class="mini-item">${escapeHtml(item)}</div>`)
        .join("")}</div>`;
    }

    function renderTranscript(items) {
      if (!items || !items.length) {
        return `<div class="empty-state">Хвост переписки пока не сохранён.</div>`;
      }
      return `<div class="timeline">${items
        .map((item) => `
          <div class="timeline-item">
            <span class="timeline-role">${escapeHtml(item.role === "agent" ? "Оператор" : "Мы")}</span>
            ${escapeHtml(item.content || "")}
          </div>
        `)
        .join("")}</div>`;
    }

    function renderCaseList() {
      if (!casesCache.length) {
        caseListEl.innerHTML = `<div class="empty-state">Пока нет сохранённых кейсов. Они появятся после первого сохранения в <code>logs/case_memory</code>.</div>`;
        return;
      }

      caseListEl.innerHTML = casesCache
        .map((item) => `
          <button class="case-item ${item.key === selectedCaseKey ? "active" : ""}" data-case-key="${escapeHtml(item.key)}">
            <div class="case-item-head">
              <strong class="case-title">${escapeHtml(item.title)}</strong>
              <span class="case-status ${escapeHtml(item.status_tone)}">${escapeHtml(item.status_label)}</span>
            </div>
            <div class="case-subtitle">${escapeHtml(item.subtitle)}</div>
            <div class="case-meta">Case ID: ${escapeHtml(item.latest_case_id || "—")}</div>
            <div class="case-meta">Последнее событие: ${escapeHtml(formatDate(item.last_event_at, "—") || "—")}</div>
            <div class="case-meta">Сохранено сообщений: ${escapeHtml(item.transcript_count || 0)}</div>
            <div class="case-meta case-timer" data-timer-kind="elapsed" data-anchor-at="${escapeHtml(item.last_event_at || "")}">Прошло: —</div>
            <div class="case-meta case-timer" data-timer-kind="remaining" data-due-at="${escapeHtml(item.follow_up_due_at || "")}">Ждать: —</div>
          </button>
        `)
        .join("");

      caseListEl.querySelectorAll("[data-case-key]").forEach((button) => {
        button.addEventListener("click", () => {
          selectedCaseKey = button.dataset.caseKey;
          renderCaseList();
          renderCaseDetail();
        });
      });
    }

    async function saveCaseBinding(caseKey, profileName) {
      try {
        const payload = await api("/api/case-binding", {
          method: "POST",
          body: JSON.stringify({ case_key: caseKey, profile_name: profileName }),
        });
        updateCases(payload);
        lastUpdateEl.textContent = "Профиль кейса сохранён.";
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка сохранения профиля: ${error.message}`;
      }
    }

    async function launchSelectedCase(caseKey, profileName) {
      const preparedProfile = String(profileName || "").trim();
      if (!preparedProfile) {
        lastUpdateEl.textContent = "Сначала укажите профиль Dolphin для этого кейса.";
        return;
      }

      try {
        const savedCases = await api("/api/case-binding", {
          method: "POST",
          body: JSON.stringify({ case_key: caseKey, profile_name: preparedProfile }),
        });
        updateCases(savedCases);
        cursor = 0;
        const payload = await api("/api/case-launch", {
          method: "POST",
          body: JSON.stringify({ case_key: caseKey }),
        });
        updateState(payload.state);
        updateCases(payload.cases);
        lastUpdateEl.textContent = "Бот запущен для выбранного кейса.";
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка запуска кейса: ${error.message}`;
      }
    }

    function renderCaseDetail() {
      const active = casesCache.find((item) => item.key === selectedCaseKey);
      if (!active) {
        caseDetailEl.innerHTML = `<div class="empty-state">Выберите кейс слева.</div>`;
        return;
      }

      caseDetailEl.innerHTML = `
        <div class="detail-summary">
          <strong>${escapeHtml(active.title)}</strong>
          <div class="case-status ${escapeHtml(active.status_tone)}">${escapeHtml(active.status_label)}</div>
          <p>${escapeHtml(active.latest_case_outcome || active.last_agent_message || "Итог пока не зафиксирован.")}</p>
        </div>

        <div class="detail-grid">
          <div class="detail-card">
            <span class="detail-label">На чём остановились</span>
            <div class="detail-value">${escapeHtml(active.last_agent_message || "Последнее сообщение оператора не сохранено.")}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Следующий шаг</span>
            <div class="detail-value">${escapeHtml(active.next_step || "Следующий шаг пока не вычислен.")}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Ожидание данных</span>
            <div class="detail-value">${escapeHtml(active.pending_requested_field_human ? `Оператор ждёт: ${active.pending_requested_field_human}` : "Сейчас недостающие данные не запрошены.")}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Case ID</span>
            <div class="detail-value">${escapeHtml(active.latest_case_id || "—")}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Прошло с последнего события</span>
            <div class="detail-value">
              <span class="timer-value" data-timer-kind="elapsed-value" data-anchor-at="${escapeHtml(active.last_event_at || "")}">—</span>
              <span class="timer-caption">С последнего сообщения в диалоге. Последнее событие: ${escapeHtml(formatDate(active.last_event_at, "—") || "—")}</span>
            </div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Осталось ждать до повторного follow-up</span>
            <div class="detail-value">
              <span class="timer-value" data-timer-kind="remaining-value" data-due-at="${escapeHtml(active.follow_up_due_at || "")}">—</span>
              <span class="timer-caption">
                ${escapeHtml(active.follow_up_deadline || "Срок не задан")}
                ${active.follow_up_due_human ? `<br>Не раньше: ${escapeHtml(active.follow_up_due_human)}` : ""}
              </span>
            </div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Память диалога</span>
            <div class="detail-value">
              Сохранено сообщений: ${escapeHtml(active.transcript_count || 0)}<br>
              Бот продолжит кейс с полным контекстом из transcript-файла.<br>
              <span class="timer-caption">Файл: ${escapeHtml(active.transcript_path || "—")}</span>
            </div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Клиент</span>
            <div class="detail-value">${escapeHtml(active.customer_name || "—")}<br>${escapeHtml(active.customer_email || "")}${active.customer_phone ? `<br>${escapeHtml(active.customer_phone)}` : ""}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Состояние кейса</span>
            <div class="detail-value">${escapeHtml(active.dialogue_state || "—")}<br>${escapeHtml(active.status_label || "—")}<br>Сохранено сообщений: ${escapeHtml(active.transcript_count || 0)}</div>
          </div>
          <div class="detail-card">
            <span class="detail-label">Профиль Dolphin для этого кейса</span>
            <input id="case-profile-input" class="text-input" type="text" value="${escapeHtml(active.profile_name || "")}" placeholder="Например: Luna_CA">
            <div class="inline-actions">
              <button id="save-case-profile-btn" class="secondary">Сохранить профиль</button>
              <button id="launch-case-btn" class="primary">Запустить и продолжить</button>
            </div>
          </div>
        </div>

        <div>
          <span class="detail-label">Подсказки боту</span>
          ${renderSimpleList(active.operator_notes, "Подсказки для этого кейса пока не сохранены.")}
        </div>

        <div>
          <span class="detail-label">Подтверждённые факты</span>
          ${renderSimpleList(active.confirmed_facts, "Подтверждённые факты пока не сохранены.")}
        </div>

        <div>
          <span class="detail-label">Незакрытые запросы</span>
          ${renderSimpleList(active.unresolved_demands, "Незакрытые запросы не сохранены.")}
        </div>

        <div>
          <span class="detail-label">Противоречия</span>
          ${renderSimpleList(active.contradictions, "Явных противоречий по кейсу пока не зафиксировано.")}
        </div>

        <div>
          <span class="detail-label">Хвост переписки</span>
          ${renderTranscript(active.transcript_tail)}
        </div>
      `;

      const saveButton = document.getElementById("save-case-profile-btn");
      const launchButton = document.getElementById("launch-case-btn");
      const profileInput = document.getElementById("case-profile-input");

      if (saveButton && profileInput) {
        saveButton.addEventListener("click", async () => {
          await saveCaseBinding(active.key, profileInput.value);
        });
      }

      if (launchButton && profileInput) {
        launchButton.addEventListener("click", async () => {
          await launchSelectedCase(active.key, profileInput.value);
        });
      }
    }

    function updateCases(payload) {
      casesCache = payload.cases || [];
      if (!casesCache.length) {
        selectedCaseKey = null;
      } else if (!selectedCaseKey || !casesCache.some((item) => item.key === selectedCaseKey)) {
        selectedCaseKey = payload.current_case_key || casesCache[0].key;
      }
      renderCaseList();
      renderCaseDetail();
      refreshCaseTimers();
    }

    function refreshCaseTimers() {
      document.querySelectorAll("[data-timer-kind]").forEach((node) => {
        const kind = node.dataset.timerKind;
        const snapshot = getTimerSnapshot(node.dataset.anchorAt || node.dataset.updatedAt, node.dataset.dueAt);

        if (kind === "elapsed") {
          node.textContent = `Прошло: ${snapshot.elapsedLabel}`;
          return;
        }

        if (kind === "remaining") {
          node.textContent = `Ждать: ${snapshot.remainingLabel}`;
          node.classList.toggle("ready", snapshot.remainingReady);
          return;
        }

        if (kind === "elapsed-value") {
          node.textContent = snapshot.elapsedLabel;
          return;
        }

        if (kind === "remaining-value") {
          node.textContent = snapshot.remainingLabel;
          node.classList.toggle("ready", snapshot.remainingReady);
        }
      });
    }

    function updateState(state) {
      if (state.reset_cursor) {
        appendTerminal(state.output || "", true);
      } else {
        appendTerminal(state.output || "", false);
      }

      cursor = state.cursor;
      cursorInfoEl.textContent = `cursor: ${cursor}`;

      const running = Boolean(state.running);
      statusPillEl.classList.toggle("running", running);
      statusLabelEl.textContent = running ? "Бот запущен" : "Остановлен";
      startedAtEl.textContent = formatDate(state.started_at);
      exitCodeEl.textContent = state.exit_code === null ? "—" : state.exit_code;
      lastUpdateEl.textContent = `Обновлено: ${new Date().toLocaleTimeString("ru-RU")}`;
    }

    async function pollState() {
      try {
        const state = await api(`/api/state?cursor=${cursor}`, { method: "GET" });
        updateState(state);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка: ${error.message}`;
      }
    }

    async function pollCases() {
      try {
        const payload = await api("/api/cases", { method: "GET" });
        updateCases(payload);
      } catch (error) {
        caseListEl.innerHTML = `<div class="empty-state">Ошибка загрузки кейсов: ${escapeHtml(error.message)}</div>`;
      }
    }

    async function sendInput(text, asBlock = false) {
      await api("/api/send", {
        method: "POST",
        body: JSON.stringify({ text, block: asBlock }),
      });
      inputEl.value = "";
      inputEl.focus();
      await pollState();
      await pollCases();
    }

    async function startFromIntake(rawText) {
      const prepared = String(rawText || "").trim();
      if (!prepared) {
        lastUpdateEl.textContent = "Вставьте стартовый блок для нового кейса.";
        return;
      }
      cursor = 0;
      const payload = await api("/api/intake-start", {
        method: "POST",
        body: JSON.stringify({ raw_text: prepared }),
      });
      updateState(payload.state);
      updateCases(payload.cases);
      lastUpdateEl.textContent = "Новый кейс запущен из одного блока.";
    }

    async function sendRuntimeHint(text) {
      const prepared = String(text || "").trim();
      if (!prepared) {
        lastUpdateEl.textContent = "Введите подсказку для бота.";
        return;
      }
      await api("/api/runtime-hint", {
        method: "POST",
        body: JSON.stringify({ text: prepared }),
      });
      hintEl.value = "";
      await pollState();
      await pollCases();
      lastUpdateEl.textContent = "Подсказка отправлена в текущую сессию.";
    }

    async function updateRunningCaseData(rawText) {
      const prepared = String(rawText || "").trim();
      if (!prepared) {
        lastUpdateEl.textContent = "Вставьте данные для обновления кейса.";
        return;
      }
      await api("/api/runtime-case-data", {
        method: "POST",
        body: JSON.stringify({ raw_text: prepared }),
      });
      caseUpdateEl.value = "";
      await pollState();
      await pollCases();
      lastUpdateEl.textContent = "Данные кейса отправлены в текущую сессию.";
    }

    document.getElementById("start-btn").addEventListener("click", async () => {
      try {
        cursor = 0;
        const state = await api("/api/start", { method: "POST", body: "{}" });
        updateState(state);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка запуска: ${error.message}`;
      }
    });

    document.getElementById("stop-btn").addEventListener("click", async () => {
      try {
        const state = await api("/api/stop", { method: "POST", body: "{}" });
        updateState(state);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка остановки: ${error.message}`;
      }
    });

    document.getElementById("refresh-btn").addEventListener("click", pollState);
    document.getElementById("refresh-cases-btn").addEventListener("click", pollCases);

    document.getElementById("start-intake-btn").addEventListener("click", async () => {
      try {
        await startFromIntake(intakeEl.value);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка запуска кейса: ${error.message}`;
      }
    });

    document.getElementById("clear-intake-btn").addEventListener("click", () => {
      intakeEl.value = "";
      intakeEl.focus();
    });

    document.getElementById("send-hint-btn").addEventListener("click", async () => {
      try {
        await sendRuntimeHint(hintEl.value);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка подсказки: ${error.message}`;
      }
    });

    document.getElementById("clear-hint-btn").addEventListener("click", () => {
      hintEl.value = "";
      hintEl.focus();
    });

    document.getElementById("update-case-data-btn").addEventListener("click", async () => {
      try {
        await updateRunningCaseData(caseUpdateEl.value);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка обновления кейса: ${error.message}`;
      }
    });

    document.getElementById("clear-case-update-btn").addEventListener("click", () => {
      caseUpdateEl.value = "";
      caseUpdateEl.focus();
    });

    document.getElementById("send-btn").addEventListener("click", async () => {
      const value = inputEl.value;
      if (!value.trim()) {
        lastUpdateEl.textContent = "Введите текст или используйте кнопку Enter.";
        return;
      }
      try {
        await sendInput(value, false);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка отправки: ${error.message}`;
      }
    });

    document.getElementById("send-block-btn").addEventListener("click", async () => {
      const value = inputEl.value;
      if (!value.trim()) {
        lastUpdateEl.textContent = "Для режима блока нужен текст.";
        return;
      }
      try {
        await sendInput(value, true);
      } catch (error) {
        lastUpdateEl.textContent = `Ошибка отправки блока: ${error.message}`;
      }
    });

    document.getElementById("clear-btn").addEventListener("click", () => {
      inputEl.value = "";
      inputEl.focus();
    });

    inputEl.addEventListener("keydown", async (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        const value = inputEl.value;
        if (!value.trim()) {
          return;
        }
        try {
          await sendInput(value, event.shiftKey);
        } catch (error) {
          lastUpdateEl.textContent = `Ошибка отправки: ${error.message}`;
        }
      }
    });

    intakeEl.addEventListener("keydown", async (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        try {
          await startFromIntake(intakeEl.value);
        } catch (error) {
          lastUpdateEl.textContent = `Ошибка запуска кейса: ${error.message}`;
        }
      }
    });

    hintEl.addEventListener("keydown", async (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        try {
          await sendRuntimeHint(hintEl.value);
        } catch (error) {
          lastUpdateEl.textContent = `Ошибка подсказки: ${error.message}`;
        }
      }
    });

    caseUpdateEl.addEventListener("keydown", async (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        try {
          await updateRunningCaseData(caseUpdateEl.value);
        } catch (error) {
          lastUpdateEl.textContent = `Ошибка обновления кейса: ${error.message}`;
        }
      }
    });

    document.querySelectorAll("[data-quick]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await sendInput(button.dataset.quick || "", false);
        } catch (error) {
          lastUpdateEl.textContent = `Ошибка отправки: ${error.message}`;
        }
      });
    });

    pollState();
    pollCases();
    pollTimer = window.setInterval(pollState, 1200);
    casesTimer = window.setInterval(pollCases, 5000);
    window.setInterval(refreshCaseTimers, 1000);
  </script>
</body>
</html>
"""


SESSION_MANAGER = BotSessionManager(BOT_PATH)


class UIRequestHandler(BaseHTTPRequestHandler):
    server_version = "SupportCopilotUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_PAGE)
            return
        if parsed.path == "/api/state":
            params = parse_qs(parsed.query)
            raw_cursor = params.get("cursor", ["0"])[0]
            try:
                cursor = int(raw_cursor)
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "cursor must be an integer")
                return
            self._send_json(SESSION_MANAGER.get_state(cursor))
            return
        if parsed.path == "/api/cases":
            self._send_json(load_case_summaries())
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        data = self._read_json_body()
        if data is None:
            return

        try:
            if parsed.path == "/api/start":
                self._send_json(SESSION_MANAGER.start())
                return
            if parsed.path == "/api/stop":
                self._send_json(SESSION_MANAGER.stop())
                return
            if parsed.path == "/api/intake-start":
                current_state = SESSION_MANAGER.get_state(0)
                if current_state.get("running"):
                    self._send_error_json(HTTPStatus.CONFLICT, "Сначала остановите текущую сессию бота.")
                    return
                raw_text = str(data.get("raw_text", "") or "")
                launch_config = build_intake_launch_config(raw_text)
                state = SESSION_MANAGER.start(run_config=launch_config)
                self._send_json({
                    "state": state,
                    "cases": load_case_summaries(),
                })
                return
            if parsed.path == "/api/runtime-hint":
                text = str(data.get("text", "") or "").strip()
                if not text:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "text is required")
                    return
                state = SESSION_MANAGER.queue_command("hint", {"text": text})
                self._send_json({
                    "state": state,
                    "cases": load_case_summaries(),
                })
                return
            if parsed.path == "/api/runtime-case-data":
                payload = parse_case_update_block(str(data.get("raw_text", "") or ""))
                state = SESSION_MANAGER.queue_command("case_data", payload)
                self._send_json({
                    "state": state,
                    "cases": load_case_summaries(),
                    "queued_payload": payload,
                })
                return
            if parsed.path == "/api/case-binding":
                case_key = str(data.get("case_key", "") or "").strip()
                profile_name = str(data.get("profile_name", "") or "").strip()
                if not case_key:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "case_key is required")
                    return
                save_case_binding(case_key, profile_name)
                self._send_json(load_case_summaries())
                return
            if parsed.path == "/api/case-launch":
                case_key = str(data.get("case_key", "") or "").strip()
                if not case_key:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, "case_key is required")
                    return
                current_state = SESSION_MANAGER.get_state(0)
                if current_state.get("running"):
                    self._send_error_json(HTTPStatus.CONFLICT, "Сначала остановите текущую сессию бота.")
                    return
                launch_config = build_case_launch_config(case_key)
                state = SESSION_MANAGER.start(run_config=launch_config)
                self._send_json({
                    "state": state,
                    "cases": load_case_summaries(),
                    "launched_case_key": case_key,
                })
                return
            if parsed.path == "/api/send":
                text = data.get("text", "")
                block = bool(data.get("block"))
                self._send_json(SESSION_MANAGER.send(text, block=block))
                return
        except RuntimeError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
            return
        except Exception as exc:  # pragma: no cover - safety for runtime HTTP errors
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
            return None

        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid JSON payload")
            return None

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web UI for Support Copilot.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind to.")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not BOT_PATH.exists():
        raise SystemExit(f"bot.py not found: {BOT_PATH}")

    server = ThreadingHTTPServer((args.host, args.port), UIRequestHandler)
    url = f"http://{args.host}:{args.port}"

    print(f"Support Copilot UI is running at {url}")
    print("Press Ctrl+C to stop the UI server.")

    if not args.no_open:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server...")
    finally:
        server.server_close()
        try:
            SESSION_MANAGER.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
