"""Generate a public, read-only iCalendar feed from the minimal timetable."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from function.course_schedule import SCHEDULE_PATH, ScheduleError, load_schedule
from function.listen_window import CHINA_TIME


CALENDAR_PATH = SCHEDULE_PATH.with_suffix(".ics")


def _escape_text(value):
    return (value.replace("\\", "\\\\")
            .replace("\r\n", "\n").replace("\r", "\n")
            .replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,"))


def _fold(line):
    """Fold at 75 UTF-8 octets without splitting a Unicode character."""
    parts = []
    current = ""
    for char in line:
        if current and len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current += char
    parts.append(current)
    return "\r\n".join(parts)


def _utc_stamp(day, clock):
    local = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=CHINA_TIME)
    return local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_calendar(schedule):
    """Return a deterministic UTF-8 ICS with one local reminder per class."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//frewily//RainClassroom course calendar//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:雨课堂课程提醒",
    ]
    for item in schedule["classes"]:
        identity = json.dumps([schedule["semester"], item["course"], item["date"],
                               item["start"], item["end"]], ensure_ascii=False)
        uid = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        start = _utc_stamp(item["date"], item["start"])
        end = _utc_stamp(item["date"], item["end"])
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}@frewily.github.io",
            f"DTSTAMP:{start}",
            f"DTSTART:{start}",
            f"DTEND:{end}",
            f"SUMMARY:{_escape_text(item['course'])}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:上课前10分钟",
            "TRIGGER:-PT10M",
            "END:VALARM",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode("utf-8")


def generate(path=SCHEDULE_PATH, output=CALENDAR_PATH):
    schedule = load_schedule(path)
    if schedule is None:
        raise ScheduleError("尚无课表文件，无法生成日历")
    calendar = build_calendar(schedule)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(calendar)
    print(f"已生成课程日历，共 {len(schedule['classes'])} 个事件")


if __name__ == "__main__":
    try:
        generate()
    except ScheduleError as error:
        print(f"课程日历生成失败：{error}")
        raise SystemExit(1) from None
