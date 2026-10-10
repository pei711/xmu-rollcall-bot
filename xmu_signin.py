#!/usr/bin/env python3
"""
XMU Rollcall Auto Sign-in Bot
=============================
24/7 server-polling auto sign-in bot for Xiamen University TronClass
platform (lnt.xmu.edu.cn).

Features
--------
* Number roll-call : waits until >= 10 classmates have signed in, then
  fetches the number code via API and submits. Non-stop waiting until
  the threshold is met; gives up gracefully if the roll-call
  disappears from the list (closed by teacher / expired).
* Radar roll-call  : two-probe trilateration -- submits two probe
  coordinates, reads the distances reported by the server, computes
  the circle intersection, and answers with the solved position.
* QR-code roll-call: cannot be automated; sends a ServerChan (WeChat)
  reminder for manual scanning.
* Notification     : all results pushed to WeChat via ServerChan v3
  (sctapi.ftqq.com).
* Multi-account    : one script copy per account; config and session
  cache isolated via XMU_ROLLCALL_CONFIG_DIR.
* Survivability    : designed for systemd (Restart=always); cached
  sessions are reused and re-login happens automatically on expiry.

Usage
-----
1. Prepare config.json in the config directory
   (default /root/.xmu_rollcall, or set XMU_ROLLCALL_CONFIG_DIR):
   {"username": "<student_id>", "password": "<password>",
    "sendkey": "<ServerChan_SendKey>"}
2. Run directly:  python3 xmu_signin.py
   Or manage via systemd (see README.md).

Acknowledgement
---------------
The core answering logic is based on / inspired by
https://github.com/KrsMt-0113/XMU-Rollcall-Bot
This deployment focuses on 24/7 server polling.

Author : pei711 (ZHANG JUNPEI)
Repo   : https://github.com/pei711/xmu-rollcall-bot
"""

import os
import sys
import json
import time
import uuid
import math
import logging
import requests
from datetime import datetime, timedelta, timezone as dt_timezone
from xmulogin import xmulogin

# Python 3.8 兼容：手动 UTC+8 时区
CHINA_TZ = dt_timezone(timedelta(hours=8))

def china_now():
    """返回中国时区 (UTC+8) 的当前时间"""
    return datetime.now(dt_timezone.utc).astimezone(CHINA_TZ)

# ==================== 配置 ====================
BASE_URL = "https://lnt.xmu.edu.cn"
POLL_INTERVAL = 1  # 查询间隔（秒）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://ids.xmu.edu.cn/authserver/login",
}

# ==================== 日志 ====================
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler("/root/signin.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("xmu_signin")


# ==================== 配置管理 ====================
def load_config():
    config_path = os.environ.get("XMU_ROLLCALL_CONFIG_DIR", "/root/.xmu_rollcall")
    config_file = os.path.join(config_path, "config.json")

    if not os.path.exists(config_file):
        log.error(f"配置文件不存在: {config_file}")
        log.error("请先创建 /root/.xmu_rollcall/config.json")
        log.error('格式: {"username": "学号", "password": "密码", "sendkey": "方糖SendKey"}')
        sys.exit(1)

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    required = ["username", "password"]
    for key in required:
        if not config.get(key):
            log.error(f"配置缺少必要字段: {key}")
            sys.exit(1)

    return config


def get_session_cache_path():
    config_dir = os.environ.get("XMU_ROLLCALL_CONFIG_DIR", "/root/.xmu_rollcall")
    return os.path.join(config_dir, "cookies.json")


# ==================== Session 管理 ====================
def save_session(sess, path):
    try:
        cj_dict = requests.utils.dict_from_cookiejar(sess.cookies)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cj_dict, f)
    except Exception as e:
        log.warning(f"保存 session 失败: {e}")


def load_session(sess, path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cj_dict = json.load(f)
        sess.cookies = requests.utils.cookiejar_from_dict(cj_dict)
        return True
    except Exception:
        return False


def verify_session(sess):
    try:
        resp = sess.get(f"{BASE_URL}/api/profile", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "name" in data:
                return data
    except Exception:
        pass
    return None


def get_or_create_session(config):
    """获取或创建XMU登录session"""
    cache_path = get_session_cache_path()
    session = requests.Session()
    session.headers.update(HEADERS)

    # 尝试恢复缓存session
    if os.path.exists(cache_path):
        log.info("发现缓存的 session，尝试验证...")
        if load_session(session, cache_path):
            profile = verify_session(session)
            if profile:
                log.info(f"Session 恢复成功，用户: {profile.get('name', 'unknown')}")
                return session, profile
            else:
                log.warning("缓存的 session 已过期，重新登录...")
        else:
            log.warning("加载 session 缓存失败，重新登录...")

    # 重新登录
    log.info("正在登录 XMU 统一认证...")
    session = xmulogin(type=3, username=config["username"], password=config["password"])
    if not session:
        log.error("XMU 登录失败！请检查学号和密码。")
        sys.exit(1)

    session.headers.update(HEADERS)
    save_session(session, cache_path)
    log.info("登录成功，session 已缓存")

    profile = session.get(f"{BASE_URL}/api/profile", headers=HEADERS, timeout=15).json()
    log.info(f"欢迎, {profile.get('name', 'unknown')}")
    return session, profile


# ==================== 方糖通知 ====================
def send_ftqq_notification(sendkey, title, content):
    """通过方糖 Server酱 v3 发送微信公众号通知"""
    if not sendkey:
        log.warning("sendkey 未配置，跳过通知")
        return False

    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    data = {"title": title, "desp": content}

    try:
        resp = requests.post(url, data=data, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            log.info(f"方糖通知发送成功: {title}")
            return True
        else:
            log.warning(f"方糖通知失败: {result.get('info', '未知错误')}")
            return False
    except Exception as e:
        log.warning(f"方糖通知异常: {e}")
        return False


def notify_signin(sendkey, course_title, teacher, rollcall_type, success, detail=""):
    """构建签到通知并发送"""
    type_emoji = {"雷达签到": "📡", "数字签到": "🔢", "二维码签到": "⚠️"}

    emoji = type_emoji.get(rollcall_type, "📋")
    if success:
        title = f"{emoji} {rollcall_type} - 签到成功"
        status = "✅ 自动签到成功"
    else:
        title = f"{emoji} {rollcall_type} - 签到提醒"
        status = "❌ 签到失败" if detail else "⚠️ 需手动处理"

    content = f"""
## 📚 课程信息
- **课程**: {course_title}
- **教师**: {teacher}
- **类型**: {rollcall_type}

## 📋 状态
{status}
"""
    if detail:
        content += f"\n## 📝 详情\n{detail}\n"

    content += """
---
*XMU Rollcall Auto Sign-in*
"""

    send_ftqq_notification(sendkey, title, content)


# ==================== 数字签到 ====================
WAIT_CLASSMATES_COUNT = 10    # 等待至少 N 个同学签到后再签（数字/雷达通用）
WAIT_POLL_INTERVAL = 3        # 查询已签人数的间隔（秒）


def _fetch_signed_count(session, rollcall_id):
    """查询当前签到已签人数。"""
    try:
        url = f"{BASE_URL}/api/rollcall/{rollcall_id}/student_rollcalls"
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            students = resp.json().get("student_rollcalls", [])
            # 已签 = status 为 on_call/on_call_fine；updated_at 所有人都非空，不能作为依据
            return sum(1 for s in students
                       if str(s.get("status", "")).startswith("on_call"))
    except Exception:
        pass
    return None


def wait_for_classmates(session, rollcall_id, tag=""):
    """等待至少 WAIT_CLASSMATES_COUNT 个同学签到（数字/雷达通用）。
    无限等待直到人数达标；若签到从列表消失(老师关闭/过期)则放弃等待返回 False，
    避免主循环被僵尸签到永久卡死。"""
    log.info(f"  {tag}等待至少 {WAIT_CLASSMATES_COUNT} 个同学签到后再签...")
    attempts = 0
    while True:
        count = _fetch_signed_count(session, rollcall_id)
        if count is not None:
            if attempts % 20 == 0 or count >= WAIT_CLASSMATES_COUNT - 1:
                log.info(f"  {tag}当前已签人数: {count}/{WAIT_CLASSMATES_COUNT}")
            if count >= WAIT_CLASSMATES_COUNT:
                log.info(f"  {tag}已达到 {WAIT_CLASSMATES_COUNT} 人，开始签到!")
                return True
        # 每10轮(约30秒)检查一次：签到是否还存在于进行中列表
        if attempts % 10 == 9:
            try:
                resp = session.get(f"{BASE_URL}/api/radar/rollcalls",
                                   headers=HEADERS, timeout=15)
                if resp.status_code == 200:
                    ids = [rc.get("rollcall_id")
                           for rc in resp.json().get("rollcalls", [])]
                    if rollcall_id not in ids:
                        log.warning(f"  {tag}签到已从列表消失(老师关闭或过期)，放弃等待")
                        return False
            except Exception as e:
                log.warning(f"  {tag}检查签到存活状态失败: {e}")
        attempts += 1
        time.sleep(WAIT_POLL_INTERVAL)


def find_number_code(data, depth=0, max_depth=10):
    """从API响应中递归查找 number_code"""
    if depth > max_depth:
        return None
    if isinstance(data, dict):
        nc = data.get("number_code")
        if nc is not None:
            return str(nc)
        for v in data.values():
            result = find_number_code(v, depth + 1, max_depth)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_number_code(item, depth + 1, max_depth)
            if result:
                return result
    return None


def fetch_number_code_timetable(session, rollcall_id, course_id):
    """Fallback: /api/timetable_rollcalls still returns number_code
    after the official fix hid it from /student_rollcalls."""
    if course_id is None:
        return None
    try:
        resp = session.get(f"{BASE_URL}/api/timetable_rollcalls",
                           headers=HEADERS, timeout=15,
                           params={"course_ids": str(course_id),
                                   "rollcall_date": time.strftime("%Y-%m-%d")})
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    def walk(node):
        if isinstance(node, dict):
            if str(node.get("rollcall_id", "")) == str(rollcall_id):
                nc = node.get("number_code")
                if nc is not None:
                    return str(nc)
            for v in node.values():
                got = walk(v)
                if got:
                    return got
        elif isinstance(node, list):
            for v in node:
                got = walk(v)
                if got:
                    return got
        return None

    return walk(data)


def answer_number_rollcall(session, rollcall):
    """处理数字签到 - 等待 N 个同学签到后再获取签到码并提交"""
    rollcall_id = rollcall["rollcall_id"]
    course_title = rollcall.get("course_title", "?")
    answer_url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer_number_rollcall"

    log.info(f"  数字签到: {course_title}")

    # ---- 等待至少 WAIT_CLASSMATES_COUNT 个同学签到 ----
    if not wait_for_classmates(session, rollcall_id, tag="数字签到: "):
        return False, "签到已关闭或过期，放弃签到"

    # 获取签到码（timetable 接口；码生成有延迟，带重试兜底）
    number_code = None
    for attempt in range(12):
        number_code = fetch_number_code_timetable(session, rollcall_id, rollcall.get("course_id"))
        if number_code:
            break
        if attempt < 11:
            log.warning(f"  timetable 接口暂无签到码(第{attempt+1}/12次)，15秒后重试...")
            time.sleep(15)
    if not number_code:
        log.error("  timetable 接口始终未返回 number_code")
        return False, "服务端未返回签到码(接口已被官方修复)，请手动签到"

    log.info(f"  签到码: {number_code}")
    payload = {"deviceId": str(uuid.uuid4()), "numberCode": number_code}

    try:
        resp = session.put(answer_url, json=payload, headers=HEADERS, timeout=15)
    except Exception as e:
        log.error(f"  提交失败: {e}")
        return False, f"提交失败: {e}"

    if resp.status_code == 200:
        log.info(f"  数字签到成功! 码={number_code}")
        return True, f"签到码: {number_code} (已签人数达标后提交)"
    else:
        log.error(f"  提交失败 HTTP {resp.status_code}")
        return False, f"提交失败 HTTP {resp.status_code}"


# ==================== 雷达签到（三角定位） ====================
def _latlon_to_xy(lat, lon, lat0, lon0):
    radius = 6371000
    x = math.radians(lon - lon0) * radius * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * radius
    return x, y


def _xy_to_latlon(x, y, lat0, lon0):
    radius = 6371000
    lat = lat0 + math.degrees(y / radius)
    lon = lon0 + math.degrees(x / (radius * math.cos(math.radians(lat0))))
    return lat, lon


def _circle_intersections(x1, y1, d1, x2, y2, d2):
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist == 0:
        return None
    if dist > d1 + d2 or dist < abs(d1 - d2):
        return None

    a = (d1**2 - d2**2 + dist**2) / (2 * dist)
    h_sq = d1**2 - a**2
    if h_sq < 0:
        return None
    h = math.sqrt(h_sq)

    xm = x1 + a * (x2 - x1) / dist
    ym = y1 + a * (y2 - y1) / dist

    rx = -(y2 - y1) * (h / dist)
    ry = (x2 - x1) * (h / dist)

    return [(xm + rx, ym + ry), (xm - rx, ym - ry)]


def _solve_trilateration(lat1, lon1, dist1, lat2, lon2, dist2):
    lat0 = (lat1 + lat2) / 2
    lon0 = (lon1 + lon2) / 2
    x1, y1 = _latlon_to_xy(lat1, lon1, lat0, lon0)
    x2, y2 = _latlon_to_xy(lat2, lon2, lat0, lon0)

    intersections = _circle_intersections(x1, y1, dist1, x2, y2, dist2)
    if not intersections:
        return None

    return [_xy_to_latlon(x, y, lat0, lon0) for x, y in intersections]


def _build_radar_payload(lat, lon):
    return {
        "accuracy": 35,
        "altitude": 0,
        "altitudeAccuracy": None,
        "deviceId": str(uuid.uuid4()),
        "heading": None,
        "latitude": lat,
        "longitude": lon,
        "speed": None,
    }


def answer_radar_rollcall(session, rollcall):
    """处理雷达签到 - 两探针点三角定位法"""
    rollcall_id = rollcall["rollcall_id"]
    url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer"

    log.info(f"  雷达签到: {rollcall.get('course_title', '?')}")

    # ---- 等待至少 WAIT_CLASSMATES_COUNT 个同学签到 ----
    if not wait_for_classmates(session, rollcall_id, tag="雷达签到: "):
        return False, "签到已关闭或过期，放弃签到"

    # 两个探针点（厦门大学附近）
    probe_points = [
        (24.3, 118.0),
        (24.6, 118.2),
    ]

    # 第一轮：探针点探测
    probe_results = []
    for lat, lon in probe_points:
        try:
            resp = session.put(url, json=_build_radar_payload(lat, lon),
                             headers=HEADERS, timeout=15)
        except Exception as e:
            log.error(f"  探针请求失败: {e}")
            return False, f"网络错误: {e}"

        payload = resp.json() if resp.text else {}
        probe_results.append((lat, lon, resp.status_code, payload))

        if resp.status_code == 200:
            log.info(f"  雷达签到成功! (探针点直接命中 lat={lat}, lon={lon})")
            return True, f"位置: {lat:.5f}, {lon:.5f}"

    # 解析距离
    def parse_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    d1 = parse_float(probe_results[0][3].get("distance"))
    d2 = parse_float(probe_results[1][3].get("distance"))

    if d1 is None or d2 is None:
        log.error("  服务端未返回距离信息")
        return False, "雷达签到失败，无距离数据"

    log.info(f"  探针1距离={d1:.1f}m, 探针2距离={d2:.1f}m")

    # 三角定位求解
    solved = _solve_trilateration(
        probe_results[0][0], probe_results[0][1], d1,
        probe_results[1][0], probe_results[1][1], d2,
    )
    if not solved:
        log.error("  无法求解坐标")
        return False, "无法求解坐标"

    # 用求解出的坐标尝试
    for lat, lon in solved:
        try:
            resp = session.put(url, json=_build_radar_payload(lat, lon),
                             headers=HEADERS, timeout=15)
        except Exception as e:
            continue

        if resp.status_code == 200:
            log.info(f"  雷达签到成功! (求解坐标 lat={lat:.5f}, lon={lon:.5f})")
            return True, f"求解位置: {lat:.5f}, {lon:.5f}"

    log.error(f"  所有求解点均失败，HTTP {probe_results[-1][2]}")
    return False, f"所有求解点均失败"


# ==================== 主循环 ====================
def main_loop():
    log.info("=" * 50)
    log.info("XMU Rollcall Auto Sign-in 启动")
    log.info("=" * 50)

    config = load_config()
    sendkey = config.get("sendkey", "")

    if sendkey:
        log.info(f"方糖 sendkey 已配置: {sendkey[:10]}...")
        # 发送启动通知
        send_ftqq_notification(sendkey, "🚀 XMU签到服务已启动",
                              f"## 签到服务\n\n✅ 服务已成功启动\n- 时间: {china_now().strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        log.warning("方糖 sendkey 未配置，将不会收到微信通知")

    # 登录
    session, profile = get_or_create_session(config)
    name = profile.get("name", config["username"])
    log.info(f"用户: {name}")

    # 通知到的签到记录（避免重复通知）
    notified_rollcalls = set()
    prev_data = {"rollcalls": []}
    query_count = 0
    start_time = time.time()

    log.info("开始监控签到...")

    while True:
        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("收到中断信号，优雅退出...")
            send_ftqq_notification(sendkey, "🛑 XMU签到服务已停止",
                                  f"## 签到服务\n\n⚠️ 服务已停止\n- 查询次数: {query_count}")
            sys.exit(0)

        try:
            # 查询签到列表
            resp = session.get(f"{BASE_URL}/api/radar/rollcalls",
                             headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                # Session 可能过期，重试登录
                if resp.status_code in (401, 403):
                    log.warning(f"Session 过期 (HTTP {resp.status_code})，重新登录...")
                    session, profile = get_or_create_session(config)
                    continue
                log.warning(f"查询签到失败 HTTP {resp.status_code}")
                continue

            data = resp.json()
            query_count += 1

            # 每60次查询输出一个心跳
            if query_count % 60 == 0:
                elapsed = int(time.time() - start_time)
                log.info(f"心跳: {query_count}次查询, 运行{elapsed}s, "
                        f"当前{len(data.get('rollcalls', []))}个签到")

            # 检查是否有新签到
            if data == prev_data:
                continue

            prev_data = data
            rollcalls = data.get("rollcalls", [])

            if not rollcalls:
                continue

            log.info(f"{'!'*30} 检测到 {len(rollcalls)} 个签到 {'!'*30}")

            for rc in rollcalls:
                rc_id = rc.get("rollcall_id")
                course_title = rc.get("course_title", "未知课程")
                teacher = rc.get("created_by_name", "未知教师")
                is_radar = rc.get("is_radar", False)
                is_number = rc.get("is_number", False)
                is_expired = rc.get("is_expired", False)
                status = rc.get("status", "")
                rc_status = rc.get("rollcall_status", "")

                # 判断签到类型
                if is_radar:
                    rc_type = "雷达签到"
                elif is_number:
                    rc_type = "数字签到"
                else:
                    rc_type = "二维码签到"

                log.info(f"  [{rc_type}] {course_title} ({teacher}) status={status}")

                # 跳过已完成或已过期的
                if is_expired:
                    log.info(f"    已过期，跳过")
                    continue

                if status == "on_call_fine":
                    log.info(f"    已完成，跳过")
                    if rc_id not in notified_rollcalls:
                        notified_rollcalls.add(rc_id)
                        notify_signin(sendkey, course_title, teacher, rc_type,
                                     True, "该签到之前已完成")
                    continue

                # 跳过已通知的
                if rc_id in notified_rollcalls:
                    continue

                # 执行签到
                success = False
                detail = ""

                if is_radar:
                    if status == "on_call_fine":
                        success = True
                        detail = "已完成"
                    else:
                        success, detail = answer_radar_rollcall(session, rc)

                elif is_number:
                    if status == "absent":
                        # 数字签到接口 number_code 已被服务端隐藏(2026-10起)，立即提醒手动签，同时保留自动尝试
                        notify_signin(sendkey, course_title, teacher, '数字签到', False, '检测到数字签到！自动签可能失败(接口被修复)，请立即手动签App')
                        success, detail = answer_number_rollcall(session, rc)
                    elif status == "on_call_fine":
                        success = True
                        detail = "已完成"
                    else:
                        log.info(f"    数字签到状态={status}，跳过")
                        continue

                else:  # 二维码签到
                    log.info(f"    二维码签到无法自动处理")
                    detail = "二维码签到需手动扫码，请打开畅课App完成签到"

                # 发送通知
                notified_rollcalls.add(rc_id)
                notify_signin(sendkey, course_title, teacher, rc_type, success, detail)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error(f"主循环异常: {e}", exc_info=True)
            # 短暂暂停后继续
            time.sleep(5)
            # 尝试恢复session
            try:
                profile = verify_session(session)
                if not profile:
                    log.warning("Session 检查失败，尝试重新登录...")
                    session, profile = get_or_create_session(config)
            except Exception:
                pass


if __name__ == "__main__":
    main_loop()
