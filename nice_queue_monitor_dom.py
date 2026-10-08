"""NICE queue monitor using the rendered dashboard DOM instead of OCR.

This is preferred when NICE exposes tile text as HTML. It reads each tile by
finding its "Longest waiting time" label, then selects the timer immediately
before that label inside the same tile.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from nice_queue_monitor import CONFIG, QUEUES, normalize_text, time_to_seconds
from notifications import NotificationEvent, Notifier

TIMER_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")
DASHBOARD_URL =   "https://na1.nice-incontact.com/apps/#/dashboard/wrapper/supervisor-dashboards" 


def collect_tiles(page) -> list[dict]:
    """Return one DOM card per visible Longest-wait label across all frames."""
    script = """() => {
          const visible = el => {
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
          };
          const labels = [...document.querySelectorAll('body *')].filter(el =>
            visible(el) && (el.innerText || '').trim().toLowerCase() === 'longest waiting time');
          return labels.map(label => {
            let node = label, selected = null;
            for (let depth = 0; depth < 10 && node; depth++, node = node.parentElement) {
              const text = (node.innerText || '').trim();
              const rect = node.getBoundingClientRect();
              const timers = text.match(/\\b\\d{1,2}:\\d{2}:\\d{2}\\b/g) || [];
              if (visible(node) && timers.length && text.length <= 800) {
                selected = { text, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
              }
            }
            return selected;
          }).filter(Boolean);
        }"""
    tiles: list[dict] = []
    for frame in page.frames:
        try:
            frame_tiles = frame.evaluate(script)
            for tile in frame_tiles:
                tile["frame_url"] = frame.url
                tiles.append(tile)
        except Exception:
            # Cross-origin or protected frames may reject evaluation.
            continue
    return tiles


def parse_tile(tile: dict) -> tuple[str | None, str | None, int | None]:
    """Select the timer closest before the label, not the header timestamp."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in tile["text"].splitlines() if line.strip()]
    label_index = next((i for i, line in enumerate(lines) if normalize_text(line) == "longest waiting time"), len(lines))
    before_label = lines[:label_index]
    timers: list[tuple[int, str]] = []
    for index, line in enumerate(before_label):
        for match in TIMER_RE.findall(line):
            if time_to_seconds(match) is not None:
                timers.append((index, match))
    wait_text = timers[-1][1] if timers else None
    wait_seconds = time_to_seconds(wait_text) if wait_text else None
    return tile["text"], wait_text, wait_seconds


def choose_queue(tile_text: str) -> tuple[str, str, str] | None:
    normalized = normalize_text(tile_text)
    for queue_id, queue_name, queue_type in QUEUES:
        if normalize_text(queue_name) in normalized:
            return queue_id, queue_name, queue_type
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor NICE queue tiles using DOM text, without OCR.")
    # parser.add_argument("url")
    parser.add_argument("--interval", type=int, default=CONFIG["poll_interval_seconds"])
    parser.add_argument("--profile", default=str(Path(".playwright-profile").resolve()))
    parser.add_argument("--debug-dom", action="store_true", help="Print frame URLs and DOM tile candidates")
    parser.add_argument("--notifications", choices=("disabled", "teams", "outlook", "both"), default="disabled", help="Notification destination; disabled by default")
    args = parser.parse_args()
    states = {queue_id: {"breaches": 0, "alerted": False, "state": "NORMAL"} for queue_id, _, _ in QUEUES}
    notifier = Notifier(args.notifications)
    print(f"Notifications: {args.notifications}")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(args.profile, channel="msedge", headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60_000)
        page.bring_to_front()
        print("Log in and wait until all NICE queue tiles are visible.")
        input("Press Enter to start DOM monitoring: ")
        try:
            while True:
                readings = {}
                tiles = collect_tiles(page)
                if args.debug_dom:
                    print("Frames:")
                    for frame in page.frames:
                        print(f"  {frame.url}")
                        try:
                            info = frame.evaluate("""() => ({
                              bodyText: (document.body?.innerText || '').slice(0, 300).replace(/\\s+/g, ' '),
                              canvases: document.querySelectorAll('canvas').length,
                              elements: document.querySelectorAll('*').length
                            })""")
                            print(f"    elements={info['elements']} canvases={info['canvases']} text={info['bodyText']!r}")
                        except Exception as error:
                            print(f"    frame inspection unavailable: {type(error).__name__}")
                    print(f"DOM tile candidates: {len(tiles)}")
                    for tile in tiles:
                        print(f"  {tile['frame_url']}: {tile['text'].replace(chr(10), ' | ')}")
                for tile in tiles:
                    text, wait_text, wait_seconds = parse_tile(tile)
                    queue = choose_queue(text)
                    if queue:
                        readings[queue[0]] = (queue, wait_text, wait_seconds)
                print(f"\n[{datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M:%S}]")
                for queue_id, queue_name, _queue_type in QUEUES:
                    state = states[queue_id]
                    queue, wait_text, seconds = readings.get(queue_id, (None, None, None))
                    if seconds is None:
                        state["state"] = "DOM_ERROR"
                    elif seconds >= CONFIG["threshold_seconds"]:
                        state["breaches"] += 1
                        if state["breaches"] >= CONFIG["required_breaches"] and not state["alerted"]:
                            state["state"], state["alerted"] = "ALERTED", True
                            print(f"ALERT: {queue_name} longest wait {wait_text}")
                            try:
                                notifier.send(NotificationEvent("ALERT", queue_id, queue_name, _queue_type, wait_text, seconds, CONFIG["threshold_seconds"]))
                            except Exception as error:
                                print(f"Notification failed: {error}")
                        elif not state["alerted"]:
                            state["state"] = "BREACH_PENDING"
                    elif seconds < CONFIG["recovery_seconds"]:
                        was_alerted = state["alerted"]
                        state.update(breaches=0, alerted=False, state="NORMAL")
                        if was_alerted:
                            try:
                                notifier.send(NotificationEvent("RECOVERY", queue_id, queue_name, _queue_type, wait_text, seconds, CONFIG["threshold_seconds"]))
                            except Exception as error:
                                print(f"Recovery notification failed: {error}")
                    print(f"{queue_name:<32} {(wait_text or 'MISSING'):<12} {state['state']}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Stopping DOM monitor.")
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
