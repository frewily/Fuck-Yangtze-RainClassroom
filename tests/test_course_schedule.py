import json
import tempfile
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from function.course_schedule import (
    ScheduleError,
    _login,
    _recognize_captcha,
    current_monitor_window,
    current_schedule_window,
    fetch_schedule,
    load_schedule,
    semester_id,
)
from function.listen_window import CHINA_TIME


class FakeResponse:
    def __init__(self, *, url="", text="", data=None, content=b""):
        self.url = url
        self.text = text
        self.data = data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.posts = []

    def get(self, url, **kwargs):
        if url.endswith("for-std/course-table"):
            return FakeResponse(url=url + "/info/123456")
        if url.endswith("info/123456"):
            return FakeResponse(url=url, text="bizTypeId: 23")
        if url.endswith("get-data"):
            return FakeResponse(data={"lessonIds": [41, 42]})
        raise AssertionError(url)

    def post(self, url, **kwargs):
        self.posts.append(kwargs["json"])
        return FakeResponse(data={"result": {
            "lessonList": [{"id": "41", "courseName": "目标课"},
                           {"id": "42", "courseName": "其他课"}],
            "scheduleList": [
                {"lessonId": 41, "date": "2026-09-29", "startTime": 800, "endTime": 940},
                {"lessonId": 42, "date": "2026-09-29", "startTime": 1000, "endTime": 1140},
            ],
        }})


class CourseScheduleTests(unittest.TestCase):
    def test_captcha_uses_same_session_and_submits_ocr_code(self):
        class LoginSession:
            def __init__(self):
                self.get_paths = []
                self.posts = []

            def get(self, url, **kwargs):
                self.get_paths.append(url)
                if url.endswith("checkInitParams"):
                    return FakeResponse(data={"vercode": True})
                if url.endswith("cas/login"):
                    return FakeResponse(text='<input name="execution" value="e1s1">')
                if url.endswith("cas/vercode"):
                    return FakeResponse(content=b"fake-image")
                raise AssertionError(url)

            def post(self, url, **kwargs):
                self.posts.append(kwargs["data"])
                return FakeResponse(url="http://jxglstu.hfut.edu.cn/eams5-student/for-std/course-table")

        session = LoginSession()
        with patch("function.course_schedule._recognize_captcha", return_value="A1B2", create=True):
            _login(session, "dummy-user", "dummy-password")
        self.assertEqual(1, len(session.posts))
        self.assertEqual("A1B2", session.posts[0]["capcha"])
        self.assertTrue(any(path.endswith("cas/vercode") for path in session.get_paths))

    def test_failed_captcha_login_stops_after_two_posts(self):
        class LoginSession:
            def __init__(self):
                self.posts = 0
                self.images = 0

            def get(self, url, **kwargs):
                if url.endswith("checkInitParams"):
                    return FakeResponse(data={"vercode": True})
                if url.endswith("cas/login"):
                    return FakeResponse(text='<input name="execution" value="e1s1">')
                if url.endswith("cas/vercode"):
                    self.images += 1
                    return FakeResponse(content=b"fake-image")
                raise AssertionError(url)

            def post(self, url, **kwargs):
                self.posts += 1
                return FakeResponse(url="https://cas.hfut.edu.cn/cas/login")

        session = LoginSession()
        with patch("function.course_schedule._recognize_captcha", return_value="A1B2"):
            with self.assertRaises(ScheduleError):
                _login(session, "dummy-user", "dummy-password")
        self.assertEqual(2, session.posts)
        self.assertEqual(2, session.images)

    def test_ocr_failure_never_submits_password(self):
        class LoginSession:
            posts = 0

            def get(self, url, **kwargs):
                if url.endswith("checkInitParams"):
                    return FakeResponse(data={"vercode": True})
                if url.endswith("cas/login"):
                    return FakeResponse(text='<input name="execution" value="e1s1">')
                if url.endswith("cas/vercode"):
                    return FakeResponse(content=b"fake-image")
                raise AssertionError(url)

            def post(self, url, **kwargs):
                self.posts += 1
                raise AssertionError("password must not be submitted")

        session = LoginSession()
        with patch("function.course_schedule._recognize_captcha",
                   side_effect=ScheduleError("验证码 OCR 结果不可靠")):
            with self.assertRaises(ScheduleError):
                _login(session, "dummy-user", "dummy-password")
        self.assertEqual(0, session.posts)

    def test_ocr_normalizes_text_without_writing_image(self):
        from PIL import Image

        image = BytesIO()
        Image.new("RGB", (100, 45), "white").save(image, format="PNG")
        with patch("function.course_schedule.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"A1 B2\n"
            self.assertEqual("A1B2", _recognize_captcha(image.getvalue()))
        self.assertEqual(b"\x89PNG", run.call_args.kwargs["input"][:4])
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_semester_rollover(self):
        self.assertEqual(semester_id(date(2026, 1, 15)), semester_id(date(2025, 9, 1)))
        self.assertNotEqual(semester_id(date(2026, 1, 15)), semester_id(date(2026, 2, 1)))

    def test_active_window_and_old_term(self):
        schedule = {"semester": semester_id(date(2026, 9, 29)), "classes": [
            {"course": "目标课", "date": "2026-09-29", "start": "08:00", "end": "09:40"},
        ]}
        now = datetime(2026, 9, 29, 8, 30, tzinfo=CHINA_TIME)
        self.assertEqual(current_schedule_window(schedule, {"目标课"}, now).hour, 9)
        self.assertIsNone(current_schedule_window(schedule, {"其他课"}, now))
        self.assertIsNone(current_schedule_window(schedule, {"目标课"},
                                                   datetime(2026, 9, 29, 9, 40, tzinfo=CHINA_TIME)))
        schedule["semester"] -= 20
        self.assertIsNone(current_schedule_window(schedule, {"目标课"}, now))

    def test_store_only_selected_fields(self):
        session = FakeSession()
        with patch("function.course_schedule._login"):
            schedule = fetch_schedule("student", "password", {"目标课"},
                                      date(2026, 9, 29), session=session)
        self.assertEqual(schedule["classes"], [
            {"course": "目标课", "date": "2026-09-29", "start": "08:00", "end": "09:40"}
        ])
        self.assertEqual(session.posts[0]["weekIndex"], "")

    def test_invalid_schedule_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            path.write_text(json.dumps({"semester": 354, "classes": [
                {"course": "课", "date": "2026-09-29", "start": "10:00", "end": "09:00"}
            ]}), encoding="utf-8")
            with self.assertRaises(ScheduleError):
                load_schedule(path)

    def test_legacy_window_only_before_schedule_exists(self):
        now = datetime(2026, 9, 29, 8, 30, tzinfo=CHINA_TIME)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"LISTEN_WINDOWS": "TUE=08:00-09:40"}
        ):
            path = Path(directory) / "schedule.json"
            self.assertIsNotNone(current_monitor_window({"目标课"}, now, path))
            path.write_text(json.dumps({"semester": 0, "classes": []}), encoding="utf-8")
            self.assertIsNone(current_monitor_window({"目标课"}, now, path))


if __name__ == "__main__":
    unittest.main()
