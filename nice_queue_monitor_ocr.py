"""NICE queue monitor: Playwright screenshots + pytesseract OCR.

Playwright handles the browser session and screenshot capture. Tesseract reads
the screenshot; Playwright is not used as an OCR substitute.
"""

from __future__ import annotations

import argparse
import io
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from playwright.sync_api import sync_playwright
from pytesseract import Output, TesseractNotFoundError

from nice_queue_monitor import CONFIG, QUEUES, find_phrase_boxes, find_queue, find_timer, normalize_text
from nice_queue_monitor import Item
from notifications import NotificationEvent, Notifier


def validate_url(value: str) -> str:
    """Reject documentation placeholders before Playwright attempts navigation."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Provide a complete NICE URL beginning with https:// or http://.")
    if any(token in value.lower() for token in ("your-real", "actual-nice", "<", ">")):
        raise ValueError("Replace the placeholder URL with the real NICE dashboard URL.")
    return value


def self_test() -> None:
    """Validate installed Python modules and the native Tesseract executable."""
    print("Python imports: OK")
    try:
        version = pytesseract.get_tesseract_version()
    except (TesseractNotFoundError, FileNotFoundError) as error:
        raise SystemExit(
            "Tesseract OCR executable was not found. Install Tesseract or run with "
            '--tesseract-cmd "C:\\Program Files\\Tesseract-OCR\\tesseract.exe".'
        ) from error
    print(f"Tesseract executable: OK ({version})")
    print("OCR monitor self-test: PASSED")


def ocr_screenshot(image: Image.Image, scale: float = 3.0, psm: int = 11) -> list[Item]:
    """Use the enhanced OCR preprocessing that was present before notifications."""
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    prepared = image.resize((width, height), Image.Resampling.LANCZOS)
    prepared = ImageOps.autocontrast(ImageOps.grayscale(prepared))
    prepared = ImageEnhance.Contrast(prepared).enhance(1.8).filter(ImageFilter.SHARPEN)
    data = pytesseract.image_to_data(prepared, output_type=Output.DICT, config=f"--oem 3 --psm {psm}")
    words: list[Item] = []
    for index, text in enumerate(data["text"]):
        text = text.strip()
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = 0
        if not text or confidence < 40:
            continue
        words.append(Item(text, float(data["left"][index]), float(data["top"][index]), float(data["width"][index]), float(data["height"][index])))
    return words


def combine_lines(words: list[Item]) -> list[Item]:
    """Combine nearby OCR words so multi-word queue names can be fuzzy matched."""
    lines: list[list[Item]] = []
    for word in sorted(words, key=lambda item: (item.y, item.x)):
        line = next((line for line in lines if abs(line[0].center_y - word.center_y) <= max(12, word.height * 0.7)), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    result = list(words)
    for line in lines:
        line.sort(key=lambda item: item.x)
        text = " ".join(item.text for item in line)
        x = min(item.x for item in line); y = min(item.y for item in line)
        right = max(item.x + item.width for item in line); bottom = max(item.y + item.height for item in line)
        result.append(Item(text, x, y, right - x, bottom - y))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor NICE queues using Playwright screenshots and Tesseract OCR.")
    parser.add_argument("url", nargs="?", help="NICE dashboard URL")
    parser.add_argument("--interval", type=int, default=CONFIG["poll_interval_seconds"])
    parser.add_argument("--profile", default=".playwright-profile")
    parser.add_argument("--tesseract-cmd", help="Full path to tesseract.exe when it is not on PATH")
    parser.add_argument("--self-test", action="store_true", help="Check Python packages and native Tesseract without opening Edge")
    parser.add_argument("--debug-ocr", action="store_true", help="Print every OCR word detected in each cycle")
    parser.add_argument("--ocr-scale", type=float, default=3.0, help="Image enlargement before OCR; default is 3x")
    parser.add_argument("--ocr-psm", type=int, default=11, help="Tesseract page segmentation mode; default is 11")
    parser.add_argument("--notifications", choices=("disabled", "teams", "outlook", "both"), default="disabled", help="Notification destination; disabled by default")
    args = parser.parse_args()
    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd
    if args.self_test:
        self_test()
        return
    if not args.url:
        parser.error("Provide the real NICE dashboard URL, or use --self-test.")
    try:
        args.url = validate_url(args.url)
    except ValueError as error:
        parser.error(str(error))
    states = {queue_id: {"breaches": 0, "alerted": False, "state": "NORMAL"} for queue_id, _, _ in QUEUES}
    notifier = Notifier(args.notifications)
    print(f"Notifications: {args.notifications}")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(args.profile, channel="msedge", headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as error:
            context.close()
            raise SystemExit(f"Could not open NICE URL. Check the URL, VPN, and network access.\nDetails: {error}") from error
        page.bring_to_front()
        print("Complete NICE login and wait until the supervisor dashboard tiles are visible.")
        input("Press Enter when the dashboard is fully visible to begin OCR: ")
        print("OCR monitoring is running; Ctrl+C stops it.")
        try:
            while True:
                image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                words = ocr_screenshot(image, scale=args.ocr_scale, psm=args.ocr_psm)
                items = combine_lines(words)
                if args.debug_ocr:
                    print("OCR words:", " | ".join(word.text for word in words) or "<none>")
                labels = find_phrase_boxes(words, "Longest waiting time")
                print(f"\n[{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}]")
                for queue_id, queue_name, _queue_type in QUEUES:
                    match = find_queue(items, queue_name)
                    timer = find_timer(items, match[0], labels) if match else None
                    if args.debug_ocr:
                        title_debug = f"{match[0].text} ({match[1]:.2f})" if match else "NONE"
                        timer_debug = f"{timer[0].text} ({timer[2]:.2f})" if timer else "NONE"
                        print(f"DEBUG {queue_name}: title={title_debug}; timer={timer_debug}")
                    state = states[queue_id]
                    seconds = timer[1] if timer else None
                    if seconds is None:
                        state["state"] = "OCR_ERROR"; state["breaches"] = 0
                    elif seconds >= CONFIG["threshold_seconds"]:
                        state["breaches"] += 1
                        if state["breaches"] >= CONFIG["required_breaches"] and not state["alerted"]:
                            state["state"], state["alerted"] = "ALERTED", True
                            print(f"ALERT: {queue_name} longest wait {timer[0].text}")
                            try:
                                notifier.send(NotificationEvent("ALERT", queue_id, queue_name, _queue_type, timer[0].text, seconds, CONFIG["threshold_seconds"]))
                            except Exception as error:
                                print(f"Notification failed: {error}")
                        elif not state["alerted"]:
                            state["state"] = "BREACH_PENDING"
                    elif seconds < CONFIG["recovery_seconds"]:
                        was_alerted = state["alerted"]
                        state.update(breaches=0, alerted=False, state="NORMAL")
                        if was_alerted:
                            try:
                                notifier.send(NotificationEvent("RECOVERY", queue_id, queue_name, _queue_type, timer[0].text, seconds, CONFIG["threshold_seconds"]))
                            except Exception as error:
                                print(f"Recovery notification failed: {error}")
                    print(f"{queue_name:<32} {(timer[0].text if timer else 'MISSING'):<12} {state['state']}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Stopping OCR monitor.")
        finally:
            try:
                context.close()
            except Exception:
                # Playwright may already have closed its driver after Ctrl+C.
                pass


if __name__ == "__main__":
    main()
