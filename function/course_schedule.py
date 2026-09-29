"""Read a minimal, committed timetable or refresh it from HFUT's student portal.

Only course name, date and start/end time are persisted. Authentication data and
raw portal responses must never be written to disk or printed.
"""

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

from function.listen_window import CHINA_TIME, current_window_end, parse_listen_windows


SCHEDULE_PATH = Path(__file__).resolve().parents[1] / "data" / "course_schedule.json"
CAS_BASE = "https://cas.hfut.edu.cn/cas/"
PORTAL_BASE = "https://jxglstu.hfut.edu.cn/eams5-student/"
# HFUT-Schedule registers the CAS service using HTTP even though the student
# timetable itself is available via HTTPS.
CAS_SERVICE = "http://jxglstu.hfut.edu.cn/eams5-student/neusoft-sso/login"
TIMEOUT = 20
MAX_LOGIN_ATTEMPTS = 2


class ScheduleError(RuntimeError):
    pass


class _ExecutionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.execution = None

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            attributes = dict(attrs)
            if attributes.get("name") == "execution":
                self.execution = attributes.get("value")


def semester_id(today=None):
    """Match HFUT-Schedule's semester numbering (Jan is prior autumn term)."""
    today = today or datetime.now(CHINA_TIME).date()
    first_year = today.year if today.month >= 8 else today.year - 1
    first_term = ((first_year - 2018) * 4 + 3) * 10 + 4
    return first_term if today.month in (1, 8, 9, 10, 11, 12) else first_term + 20


def parse_filtered_courses(raw):
    return {name.strip() for name in raw.split(",") if name.strip()}


def _validated_entries(raw_entries):
    if not isinstance(raw_entries, list):
        raise ScheduleError("课表格式无效")
    entries = []
    for entry in raw_entries:
        if not isinstance(entry, dict) or set(entry) != {"course", "date", "start", "end"}:
            raise ScheduleError("课表记录格式无效")
        try:
            day = date.fromisoformat(entry["date"])
            start = datetime.strptime(entry["start"], "%H:%M").time()
            end = datetime.strptime(entry["end"], "%H:%M").time()
        except (TypeError, ValueError) as error:
            raise ScheduleError("课表日期或时间无效") from error
        if not isinstance(entry["course"], str) or not entry["course"].strip() or end <= start:
            raise ScheduleError("课表课程或时间无效")
        entries.append({"course": entry["course"].strip(), "date": day.isoformat(),
                        "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")})
    return sorted({tuple(item.values()) for item in entries})


def load_schedule(path=SCHEDULE_PATH):
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("semester"), int):
            raise ScheduleError("课表元数据无效")
        tuples = _validated_entries(payload.get("classes"))
        selected_courses = payload.get("selected_courses")
        if selected_courses is not None and (
            not isinstance(selected_courses, list) or
            any(not isinstance(name, str) or not name.strip() for name in selected_courses)
        ):
            raise ScheduleError("课表课程筛选元数据无效")
        return {
            "semester": payload["semester"],
            "selected_courses": sorted(set(selected_courses)) if selected_courses is not None else None,
            "classes": [dict(zip(("course", "date", "start", "end"), item)) for item in tuples],
        }
    except (OSError, json.JSONDecodeError) as error:
        raise ScheduleError("课表文件无法读取") from error


def current_schedule_window(schedule, filtered_courses, now=None):
    """Return end of an active selected class, or None. No weekly fallback here."""
    now = (now or datetime.now(CHINA_TIME)).astimezone(CHINA_TIME)
    if schedule["semester"] != semester_id(now.date()):
        return None
    matching_ends = []
    for item in schedule["classes"]:
        if item["course"] not in filtered_courses or item["date"] != now.date().isoformat():
            continue
        start = datetime.fromisoformat(f"{item['date']}T{item['start']}").replace(tzinfo=CHINA_TIME)
        end = datetime.fromisoformat(f"{item['date']}T{item['end']}").replace(tzinfo=CHINA_TIME)
        if start <= now < end:
            matching_ends.append(end)
    return max(matching_ends, default=None)


def current_monitor_window(filtered_courses, now=None, path=SCHEDULE_PATH):
    """Use the committed timetable; retain legacy windows until the first import."""
    schedule = load_schedule(path)
    if schedule is not None:
        return current_schedule_window(schedule, set(filtered_courses), now)
    return current_window_end(parse_listen_windows(os.getenv("LISTEN_WINDOWS", "")), now)


def _get(session, url, **kwargs):
    response = session.get(url, timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response


def _recognize_captcha(image_bytes):
    """Run the reference app's grayscale + English single-line OCR in memory."""
    if not image_bytes or len(image_bytes) > 1_000_000:
        raise ScheduleError("验证码图片无效")
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            prepared = BytesIO()
            image.convert("L").save(prepared, format="PNG")
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"],
            input=prepared.getvalue(), capture_output=True, timeout=15, check=False,
        )
    except (OSError, UnidentifiedImageError, subprocess.TimeoutExpired, ValueError) as error:
        raise ScheduleError(f"验证码 OCR 无法运行：{type(error).__name__}") from None
    if result.returncode != 0:
        raise ScheduleError("验证码 OCR 运行失败")
    code = re.sub(r"[^A-Za-z0-9]", "", result.stdout.decode("utf-8", errors="ignore"))
    if not 2 <= len(code) <= 8:
        raise ScheduleError("验证码 OCR 结果不可靠，已停止登录")
    return code


def _login(session, username, password):
    # The CAS service URL is a URL parameter; never log response URLs, bodies,
    # headers or exceptions, since they may contain tickets/cookies.
    init = _get(session, urljoin(CAS_BASE, "checkInitParams"))
    try:
        init_data = init.json()
        if not isinstance(init_data, dict) or not isinstance(init_data.get("vercode"), bool):
            raise ScheduleError("教务登录初始化响应无效")
    except ValueError:
        raise ScheduleError("教务登录初始化响应无效") from None
    login_url = urljoin(CAS_BASE, "login")
    attempts = MAX_LOGIN_ATTEMPTS if init_data["vercode"] else 1
    for _ in range(attempts):
        page = _get(session, login_url, params={"service": CAS_SERVICE})
        parser = _ExecutionParser()
        parser.feed(page.text)
        if not parser.execution:
            raise ScheduleError("教务登录页面缺少必要参数")
        code = ""
        if init_data["vercode"]:
            captcha = _get(session, urljoin(CAS_BASE, "vercode"))
            code = _recognize_captcha(captcha.content)
        response = session.post(login_url, params={"service": CAS_SERVICE}, data={
            "username": username, "password": password, "execution": parser.execution,
            "_eventId": "submit", "capcha": code,
        }, timeout=TIMEOUT)
        response.raise_for_status()
        destination = urlparse(response.url)
        if destination.hostname == "jxglstu.hfut.edu.cn" and destination.path.startswith(
            "/eams5-student/"
        ):
            return
    raise ScheduleError("教务登录未通过，验证码识别最多尝试两次；也可能是账号或网络问题")


def _time(value):
    if not isinstance(value, int) or not 0 <= value <= 2359 or value % 100 >= 60:
        raise ScheduleError("教务课表时间无效")
    return f"{value // 100:02d}:{value % 100:02d}"


def fetch_schedule(username, password, selected_courses, today=None, session=None):
    """Fetch one term and reduce it to the public, selected-course schema."""
    import requests

    if not username or not password or not selected_courses:
        raise ScheduleError("缺少教务账号、密码或 FILTERED_COURSES")
    session = session or requests.Session()
    today = today or datetime.now(CHINA_TIME).date()
    try:
        _login(session, username, password)
        landing = _get(session, urljoin(PORTAL_BASE, "for-std/course-table"))
        match = re.search(r"/for-std/course-table/info/(\d+)", landing.url)
        if not match:
            raise ScheduleError("无法从教务课表入口获取学生标识")
        student_id = match.group(1)
        info = _get(session, urljoin(PORTAL_BASE, f"for-std/course-table/info/{student_id}"))
        biz = re.search(r"bizTypeId\s*:\s*(\d+)", info.text)
        if not biz:
            raise ScheduleError("无法获取教务课表类型")
        term = semester_id(today)
        lesson_data = _get(session, urljoin(PORTAL_BASE, "for-std/course-table/get-data"),
                           params={"bizTypeId": biz.group(1), "semesterId": term,
                                   "dataId": student_id}).json()
        lesson_ids = lesson_data.get("lessonIds", [])
        if not isinstance(lesson_ids, list) or not lesson_ids:
            raise ScheduleError("本学期尚无课程数据，稍后自动重试")
        datum = session.post(urljoin(PORTAL_BASE, "ws/schedule-table/datum"),
                             json={"lessonIds": lesson_ids, "studentId": int(student_id),
                                   "weekIndex": ""}, timeout=TIMEOUT)
        datum.raise_for_status()
        result = datum.json()["result"]
        names = {int(item["id"]): item["courseName"] for item in result["lessonList"]}
        entries = []
        for item in result["scheduleList"]:
            name = names.get(int(item["lessonId"]))
            if name not in selected_courses:
                continue
            entries.append({"course": name, "date": item["date"],
                            "start": _time(item["startTime"]), "end": _time(item["endTime"])})
        tuples = _validated_entries(entries)
        if not tuples:
            raise ScheduleError("课表中没有匹配 FILTERED_COURSES 的课程，未覆盖旧课表")
        return {"semester": term, "selected_courses": sorted(selected_courses),
                "classes": [dict(zip(("course", "date", "start", "end"), item)) for item in tuples]}
    except requests.RequestException as error:
        raise ScheduleError(f"教务网络请求失败：{type(error).__name__}") from None
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ScheduleError(f"教务课表响应格式不符合预期：{type(error).__name__}") from None


def refresh(path=SCHEDULE_PATH):
    today = datetime.now(CHINA_TIME).date()
    selected = parse_filtered_courses(os.getenv("FILTERED_COURSES", ""))
    if not selected:
        raise ScheduleError("FILTERED_COURSES 不能为空，避免公开整个课表")
    try:
        old = load_schedule(path)
    except ScheduleError:
        # A broken cache should be replaceable by a successful fresh download.
        print("已有课表文件无效，尝试重新获取")
        old = None
    if old and old["semester"] == semester_id(today) and old["selected_courses"] == sorted(selected):
        print("本学期课表已保存，跳过教务登录")
        return False
    payload = fetch_schedule(os.getenv("HFUT_USERNAME", ""), os.getenv("HFUT_PASSWORD", ""),
                             selected, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成本学期精简课表，共 {len(payload['classes'])} 条")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("refresh", "check"))
    args = parser.parse_args()
    try:
        if args.command == "refresh":
            refresh()
        else:
            selected = parse_filtered_courses(os.getenv("FILTERED_COURSES", ""))
            active = bool(current_monitor_window(selected))
            if os.getenv("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                    output.write(f"active={'true' if active else 'false'}\n")
            print("当前处于课表监听时段" if active else "当前不在课表监听时段")
    except ScheduleError as error:
        print(f"课表处理失败：{error}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
