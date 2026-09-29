import tempfile
import unittest
from pathlib import Path

from function.course_calendar import build_calendar, generate
from function.course_schedule import ScheduleError


class CourseCalendarTests(unittest.TestCase):
    def test_beijing_time_reminder_and_deterministic_output(self):
        schedule = {"semester": 354, "classes": [{
            "course": "测试,课程;A", "date": "2026-09-29",
            "start": "08:00", "end": "09:40",
        }]}
        result = build_calendar(schedule)
        self.assertEqual(result, build_calendar(schedule))
        content = result.decode("utf-8")
        self.assertIn("DTSTART:20260929T000000Z\r\n", content)
        self.assertIn("DTEND:20260929T014000Z\r\n", content)
        self.assertIn("SUMMARY:测试\\,课程\\;A\r\n", content)
        self.assertIn("TRIGGER:-PT10M\r\n", content)
        self.assertEqual(content.count("BEGIN:VEVENT"), 1)
        self.assertEqual(content.count("BEGIN:VALARM"), 1)
        self.assertNotIn("\n", content.replace("\r\n", ""))

    def test_long_utf8_titles_are_folded_without_changing_event_count(self):
        schedule = {"semester": 354, "classes": [{
            "course": "很长的课程名称" * 15 + "\n另起一行",
            "date": "2026-09-29", "start": "08:00", "end": "09:40",
        }]}
        content = build_calendar(schedule)
        self.assertTrue(all(len(line) <= 75 for line in content.split(b"\r\n")))
        self.assertEqual(content.count(b"BEGIN:VEVENT"), 1)
        self.assertIn(b"\\n", content)

    def test_missing_schedule_does_not_write_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            schedule = Path(directory) / "schedule.json"
            calendar = Path(directory) / "schedule.ics"
            with self.assertRaises(ScheduleError):
                generate(schedule, calendar)
            self.assertFalse(calendar.exists())


if __name__ == "__main__":
    unittest.main()
