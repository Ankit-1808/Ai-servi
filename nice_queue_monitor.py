"""NICE CXone queue monitor using Playwright DOM inspection.

This intentionally does not use NICE API endpoints or OCR. It reads visible text
from the dashboard and associates queue titles, timers, and the "Longest waiting
time" label using their rendered screen geometry.
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright


def validate_url(value: str) -> str:
    if not value.startswith(("http://", "https://")) or "<" in value or ">" in value or "your-real" in value.lower():
        raise ValueError("Provide the real NICE dashboard URL, beginning with https:// or http://.")
    return value

QUEUES = [
    ("rapid-response-pro-calls", "Rapid Response Pro Calls", "call"),
    ("service-coordinators-calls", "Service Coordinators Calls", "call"),
    ("ukg-ready-chats", "UKG Ready Chats", "chat"),
    ("ascentis-calls", "Ascentis Calls", "call"),
    ("eec-calls", "EEC Calls", "call"),
    ("eec-chats", "EEC Chats", "chat"),
    ("rapid-response-support-chats", "Rapid Response Support Chats", "chat"),
]
TIMER_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
CONFIG = {"threshold_seconds": 120, "poll_interval_seconds": 30, "required_breaches": 2, "recovery_seconds": 90, "similarity_threshold": 0.78}


@dataclass
class Item:
    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def normalize_text(value: str) -> str:
    value = value.lower().replace("|", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def time_to_seconds(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) == 2:
        if not re.fullmatch(r"\d{1,2}:\d{2}", value):
            return None
        hours = 0
        minutes, seconds = map(int, parts)
    elif len(parts) == 3 and TIMER_RE.fullmatch(value):
        hours, minutes, seconds = map(int, parts)
    else:
        return None
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def collect_visible_items(page: Page) -> list[Item]:
    """Collect short, visible DOM text elements and their rendered rectangles."""
    raw = page.locator("body *").evaluate_all(
        """els => els.map(el => {
          const r = el.getBoundingClientRect();
          const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
          const style = getComputedStyle(el);
          return { text, x:r.x, y:r.y, width:r.width, height:r.height,
                   visible:r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none',
                   childText: Array.from(el.children).some(c => (c.innerText || '').trim() === text) };
        }).filter(x => x.visible && x.text && x.text.length <= 120 && !x.childText)"""
    )
    return [Item(x["text"], x["x"], x["y"], x["width"], x["height"]) for x in raw]


def find_queue(items: list[Item], configured_name: str) -> tuple[Item, float] | None:
    target = normalize_text(configured_name)
    target_tokens = target.split()
    candidates: list[tuple[Item, float]] = []
    # Use only single OCR words. A whole OCR line can span several tiles.
    words = [item for item in items if len(normalize_text(item.text).split()) == 1]
    rows: list[list[Item]] = []
    for item in sorted(words, key=lambda value: (value.center_y, value.x)):
        row = next((row for row in rows if abs(row[0].center_y - item.center_y) <= max(14, item.height)), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    for row in rows:
        row.sort(key=lambda value: value.x)
        for start in range(len(row)):
            for end in range(start + 1, min(len(row), start + len(target_tokens) + 1) + 1):
                selected = row[start:end]
                if any(selected[index + 1].x - (selected[index].x + selected[index].width) > max(180, selected[index].height * 5) for index in range(len(selected) - 1)):
                    continue
                token_list = normalize_text(" ".join(item.text for item in selected)).split()
                if not token_list:
                    continue
                aligned = [SequenceMatcher(None, target_token, token_list[index]).ratio() for index, target_token in enumerate(target_tokens[:len(token_list)])]
                coverage = len(token_list) / len(target_tokens)
                score = sum(aligned) / len(aligned)
                if coverage >= 0.5 and score >= 0.68 and aligned[0] >= 0.68:
                    x = min(item.x for item in selected); y = min(item.y for item in selected)
                    right = max(item.x + item.width for item in selected); bottom = max(item.y + item.height for item in selected)
                    candidates.append((Item(" ".join(item.text for item in selected), x, y, right - x, bottom - y), score))
    return max(candidates, key=lambda x: x[1]) if candidates else None


def find_phrase_boxes(items: list[Item], phrase: str) -> list[Item]:
    """Find a phrase from adjacent OCR words on one rendered row."""
    target_tokens = normalize_text(phrase).split()
    words = [item for item in items if len(normalize_text(item.text).split()) == 1]
    rows: list[list[Item]] = []
    for item in sorted(words, key=lambda value: (value.center_y, value.x)):
        row = next((row for row in rows if abs(row[0].center_y - item.center_y) <= max(14, item.height)), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    matches: list[Item] = []
    for row in rows:
        row.sort(key=lambda value: value.x)
        for start in range(0, max(0, len(row) - len(target_tokens) + 1)):
            selected = row[start:start + len(target_tokens)]
            if any(selected[index + 1].x - (selected[index].x + selected[index].width) > max(180, selected[index].height * 5) for index in range(len(selected) - 1)):
                continue
            tokens = normalize_text(" ".join(item.text for item in selected)).split()
            score = sum(SequenceMatcher(None, target, actual).ratio() >= 0.65 for target, actual in zip(target_tokens, tokens)) / len(target_tokens)
            if score >= 0.8:
                x = min(item.x for item in selected); y = min(item.y for item in selected)
                right = max(item.x + item.width for item in selected); bottom = max(item.y + item.height for item in selected)
                matches.append(Item(" ".join(item.text for item in selected), x, y, right - x, bottom - y))
    return matches


def find_timer(items: list[Item], queue: Item, labels: list[Item]) -> tuple[Item, int, float] | None:
    timers = []
    if labels:
        label = min(labels, key=lambda x: abs(x.center_x - queue.center_x))
        queue_label_gap = abs(label.center_x - queue.center_x)
        if queue_label_gap > max(260, queue.width * 2):
            return None
        relevant_labels = [label]
    else:
        relevant_labels = []
    for item in items:
        corrected = item.text.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
        seconds = time_to_seconds(corrected)
        if seconds is None or item.center_y <= queue.center_y:
            continue
        score = 40.0
        score += max(0, 40 - abs(item.center_x - queue.center_x) / 8)
        if relevant_labels:
            below_labels = [label for label in relevant_labels if label.y >= item.y + item.height and label.y - (item.y + item.height) <= 240]
            if not below_labels:
                continue
            label = min(below_labels, key=lambda x: abs(x.center_x - item.center_x))
            vertical_gap = label.y - (item.y + item.height)
            horizontal_gap = abs(label.center_x - item.center_x)
            if horizontal_gap > max(220, label.width * 2):
                continue
            score += max(0, 130 - vertical_gap / 2)
            score += max(0, 120 - horizontal_gap / 2)
        score -= max(0, abs(item.center_y - queue.center_y) - 300) / 3
        timers.append((item, seconds, score))
    if not timers:
        return None
    item, seconds, score = max(timers, key=lambda x: x[2])
    return item, seconds, min(1.0, max(0.0, score / 210))


def scan(page: Page) -> list[dict[str, Any]]:
    items = collect_visible_items(page)
    labels = [item for item in items if SequenceMatcher(None, normalize_text(item.text), "longest waiting time").ratio() >= 0.65]
    readings = []
    for queue_id, queue_name, queue_type in QUEUES:
        match = find_queue(items, queue_name)
        reading: dict[str, Any] = {"queue_id": queue_id, "queue": queue_name, "type": queue_type, "wait": None, "wait_seconds": None, "association_confidence": 0.0, "detected_at": datetime.now(timezone.utc).isoformat()}
        if match:
            reading["detected_text"], reading["title_similarity"] = match[0].text, round(match[1], 3)
            timer = find_timer(items, match[0], labels)
            if timer:
                reading["wait"] = timer[0].text
                reading["wait_seconds"] = timer[1]
                reading["association_confidence"] = round(timer[2], 3)
        readings.append(reading)
    return readings


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor NICE CXone queue timers through visible DOM text.")
    parser.add_argument("url", help="NICE dashboard URL")
    parser.add_argument("--interval", type=int, default=CONFIG["poll_interval_seconds"])
    parser.add_argument("--profile", default=str(Path(".playwright-profile").resolve()))
    args = parser.parse_args()
    try:
        args.url = validate_url(args.url)
    except ValueError as error:
        parser.error(str(error))

    states = {queue_id: {"state": "NORMAL", "breaches": 0, "alerted": False} for queue_id, _, _ in QUEUES}
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(args.profile, channel="msedge", headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        print("NICE monitor started. Complete login manually if required; Ctrl+C stops monitoring.")
        try:
            while True:
                readings = scan(page)
                print(f"\n[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}]")
                for reading in readings:
                    state = states[reading["queue_id"]]
                    seconds = reading["wait_seconds"]
                    if seconds is None:
                        state["breaches"] = 0
                        state["state"] = "OCR/DOM_ERROR"
                    elif seconds >= CONFIG["threshold_seconds"]:
                        state["breaches"] += 1
                        if state["breaches"] >= CONFIG["required_breaches"] and not state["alerted"]:
                            state["state"], state["alerted"] = "ALERTED", True
                            print(f"ALERT: {reading['queue']} longest wait {reading['wait']}")
                        elif not state["alerted"]:
                            state["state"] = "BREACH_PENDING"
                    elif seconds < CONFIG["recovery_seconds"]:
                        state["breaches"], state["alerted"], state["state"] = 0, False, "NORMAL"
                    print(f"{reading['queue']:<32} {str(reading['wait'] or 'MISSING'):<12} {state['state']} (association={reading['association_confidence']})")
                time.sleep(args.interval)
        except (KeyboardInterrupt, PlaywrightError):
            print("Stopping NICE monitor.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
