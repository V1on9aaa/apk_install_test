"""
APK 自动化测试核心逻辑
"""
import time
import subprocess
import re
import shutil
import os
import logging
import threading
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("apk_test")

# ============================================================
# 弹窗配置
# ============================================================

# 系统安装器弹窗文本（安装过程中可能出现）
INSTALL_POPUP_TEXTS = [
    "完成",
    "详细情况",
    "仍要安装",
    "继续安装",
    "始终允许",
    "允许",
    "确定",
    "安装",
    "允许访问一次",
    "已了解应用的风险检测结果",
    "更多详细",
    "下一步",
    "开始使用",
    "同意",
    "我知道了",
]

# 权限申请弹窗文本（启动后可能出现）
PERMISSION_POPUP_TEXTS = [
    "允许",
    "始终允许",
    "仅在使用中允许",
    "确定",
    "同意",
    "授予",
    "允许访问一次",
    "我知道了",
    "继续",
]

# 系统安装器包名列表
INSTALL_PKG_NAMES = [
    "com.android.packageinstaller",
    "com.coloros.safecenter",
    "com.oplus.appdetail",
    "com.android.vending",
    "com.android.systemui",
    "com.google.android.packageinstaller",
    "com.miui.packageinstaller",
    "com.oneplus.packageinstaller",
    "com.huawei.packageinstaller",
]

# 权限弹窗包名列表
PERMISSION_PKG_NAMES = [
    "com.android.packageinstaller",
    "com.android.permissioncontroller",
    "com.android.systemui",
    "com.google.android.permissioncontroller",
    "com.lbe.security.miui",
    "com.coloros.safecenter",
    "com.oneplus.permission",
    "com.huawei.systemmanager",
]

# ============================================================
# PopupHandler 弹窗处理类
# ============================================================

class PopupHandler:
    """弹窗处理类 - 使用 uiautomator2 检测和点击弹窗"""

    def __init__(self, device: str):
        self.device = device
        self._u2 = None

    @property
    def u2(self):
        """延迟初始化 uiautomator2 连接"""
        if self._u2 is None:
            try:
                import uiautomator2 as u2
                self._u2 = u2.connect(self.device)
            except ImportError:
                logger.warning("  ⚠️ uiautomator2 未安装，弹窗处理功能不可用")
                self._u2 = None
            except Exception as e:
                logger.warning(f"  ⚠️ uiautomator2 连接失败: {e}")
                self._u2 = None
        return self._u2

    def get_ui2_texts(self) -> Dict[str, bool]:
        """获取当前界面上所有可见文本及其是否可点击"""
        if self.u2 is None:
            return {}
        try:
            xml_str = self.u2.dump_hierarchy()
            root = ET.fromstring(xml_str)
            texts = {}
            for element in root.iter():
                clickable = element.attrib.get("clickable", "false")
                text = element.attrib.get("text", "")
                if text and text.strip():
                    texts[text.strip()] = clickable == "true"
            return texts
        except Exception:
            return {}

    def get_foreground_package(self) -> str:
        """获取当前前台包名"""
        if self.u2 is None:
            return ""
        try:
            return self.u2.app_current().get("package", "")
        except Exception:
            return ""

    def click_text(self, text: str, exact: bool = True) -> bool:
        """点击指定文本"""
        if self.u2 is None:
            return False
        try:
            if exact:
                self.u2(text=text).click(timeout=3)
            else:
                self.u2(textContains=text).click(timeout=3)
            return True
        except Exception:
            return False

    def adb_tap(self, text: str) -> bool:
        """通过 adb shell input tap 点击指定文本的控件（兜底方案）"""
        if self.u2 is None:
            return False
        try:
            element = self.u2(text=text)
            bounds = element.info.get("bounds", {})
            if bounds:
                cx = (bounds["left"] + bounds["right"]) // 2
                cy = (bounds["top"] + bounds["bottom"]) // 2
                result = adb_cmd(self.device, ["shell", "input", "tap", str(cx), str(cy)])
                return result.returncode == 0
        except Exception:
            return False
        return False

    def keyevent(self, keycode: str) -> bool:
        """通过 adb shell input keyevent 模拟按键"""
        result = adb_cmd(self.device, ["shell", "input", "keyevent", keycode])
        return result.returncode == 0

    def handle_popup_by_text(self, popup_texts: List[str]) -> bool:
        """遍历点击弹窗文本，找到并点击第一个匹配的"""
        ui2_texts = self.get_ui2_texts()
        for text in popup_texts:
            if text in ui2_texts:
                logger.info(f"  🪟 检测到弹窗文本: '{text}'，正在点击...")
                # 先用 uiautomator2 点击
                if self.click_text(text):
                    time.sleep(1)
                    return True
                # 如果 UI2 点击失败，用 adb tap 兜底
                if self.adb_tap(text):
                    time.sleep(1)
                    return True
        return False

    def handle_popup_by_package(self, pkg_names: List[str]) -> bool:
        """检测前台包名是否在弹窗列表中"""
        fg_pkg = self.get_foreground_package()
        for pkg in pkg_names:
            if pkg in fg_pkg:
                logger.info(f"  🪟 检测到系统弹窗包: '{fg_pkg}'")
                return True
        return False

    def handle_install_popups(self, timeout: int = 30) -> bool:
        """处理安装过程中的系统安装器弹窗"""
        deadline = time.time() + timeout
        handled = False
        while time.time() < deadline:
            if self.handle_popup_by_text(INSTALL_POPUP_TEXTS):
                handled = True
                time.sleep(1)
                continue
            # 检查是否还有安装器在前台
            fg_pkg = self.get_foreground_package()
            if not any(pkg in fg_pkg for pkg in INSTALL_PKG_NAMES):
                break
            time.sleep(0.5)
        return handled

    def handle_permission_popups(self, timeout: int = 30) -> bool:
        """处理权限申请弹窗"""
        deadline = time.time() + timeout
        handled = False
        while time.time() < deadline:
            if self.handle_popup_by_text(PERMISSION_POPUP_TEXTS):
                handled = True
                time.sleep(1)
                continue
            # 检查是否还有权限弹窗包在前台
            fg_pkg = self.get_foreground_package()
            if not any(pkg in fg_pkg for pkg in PERMISSION_PKG_NAMES):
                break
            time.sleep(0.5)
        return handled

    def handle_all_popups(self, timeout: int = 10) -> bool:
        """通用弹窗处理 - 同时处理安装和权限弹窗"""
        deadline = time.time() + timeout
        handled = False
        while time.time() < deadline:
            all_texts = INSTALL_POPUP_TEXTS + PERMISSION_POPUP_TEXTS
            if self.handle_popup_by_text(all_texts):
                handled = True
                time.sleep(1)
                continue
            time.sleep(0.5)
        return handled

# ============================================================
# 原有函数（增强版）
# ============================================================

def adb_cmd(device, args, timeout=30):
    """执行 adb 命令"""
    cmd = ["adb", "-s", device] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"  ⚠️ 设备 {device} adb 命令超时: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="timeout")

def get_package_name_from_apk(apk_path):
    """从 APK 文件解析包名和启动 Activity"""
    for tool in ("aapt2", "aapt"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            result = subprocess.run(
                [exe, "dump", "badging", apk_path],
                capture_output=True, text=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            continue
        pkg_match = re.search(r"package:\s*name='([^']+)'", result.stdout)
        activity_match = re.search(r"launchable-activity:\s*name='([^']+)'", result.stdout)
        pkg = pkg_match.group(1) if pkg_match else None
        activity = activity_match.group(1) if activity_match else None
        if pkg:
            return pkg, activity
    return None, None

def check_device(device):
    """检查设备状态"""
    result = adb_cmd(device, ["get-state"])
    if result.returncode != 0:
        logger.error(f"❌ 设备 {device} 未连接")
        return False
    logger.info(f"✅ 设备 {device} 已连接")
    return True

# ============================================================
# 安装超时计算
# ============================================================

# APK 大小(MB) -> 基础安装时间(秒)
SIZE_2_TIME = {
    50: 30,
    100: 45,
    200: 60,
    512: 120,
    1024: 300,
    2048: 600,
    5120: 900,
    10240: 1200
}

# Android 大版本 -> 时间倍率
VER_2_TIME = {
    4: 3, 5: 3, 6: 3, 7: 3, 8: 2, 9: 2,
    10: 1.5, 11: 1.5, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1
}

# API 级别 -> Android 大版本号映射
API_TO_ANDROID_VER = {
    35: 15, 34: 14, 33: 13, 32: 12, 31: 12,
    30: 11, 29: 10, 28: 9, 27: 8, 26: 8,
    25: 7, 24: 7, 23: 6, 22: 5, 21: 5,
    20: 4, 19: 4
}

def get_apk_size_mb(apk_path):
    """获取 APK 文件大小（MB）"""
    try:
        size_bytes = os.path.getsize(apk_path)
        return size_bytes / (1024 * 1024)
    except OSError:
        return 0

def get_device_api_level(device):
    """获取设备 Android API 级别"""
    result = adb_cmd(device, ["shell", "getprop", "ro.build.version.sdk"])
    try:
        return int(result.stdout.strip())
    except (ValueError, TypeError):
        return 30  # 默认 Android 11

def api_level_to_android_ver(api_level):
    """将 API 级别映射为 Android 大版本号"""
    return API_TO_ANDROID_VER.get(api_level, api_level)

def calc_install_timeout(device, apk_path):
    """根据 APK 大小和设备版本动态计算安装超时时间（秒）"""
    size_mb = get_apk_size_mb(apk_path)
    api_level = get_device_api_level(device)
    android_ver = api_level_to_android_ver(api_level)

    # 计算基础时间
    base_time = 1500  # 默认
    for s in sorted(SIZE_2_TIME.keys()):
        if size_mb <= s:
            base_time = SIZE_2_TIME[s]
            break

    # 计算倍率
    ratio = 1.0
    for v in sorted(VER_2_TIME.keys(), reverse=True):
        if android_ver >= v:
            ratio = VER_2_TIME[v]
            break

    timeout = int(base_time * ratio)
    logger.debug(f"  安装超时: APK={size_mb:.1f}MB, API={api_level}(Android {android_ver}), 超时={timeout}s")
    return max(timeout, 60)  # 最少 60 秒

def install_apk(device, apk_path, package_name):
    """安装 apk（增强版：安装过程中并行处理系统安装器弹窗）"""
    install_timeout = calc_install_timeout(device, apk_path)
    push_timeout = min(install_timeout, 60)  # push 超时单独设，最多 60s

    result_push = adb_cmd(device, ["push", apk_path, "/data/local/tmp/temp.apk"], timeout=push_timeout)
    if result_push.returncode != 0:
        logger.error(f"  ❌ {device} push APK 失败")
        return False

    logger.info(f"  --正在安装: {apk_path}")

    # 启动弹窗处理线程（并行处理安装弹窗）
    handler = PopupHandler(device)
    stop_event = threading.Event()

    def popup_worker():
        while not stop_event.is_set():
            handler.handle_install_popups(timeout=5)
            time.sleep(1)

    popup_thread = threading.Thread(target=popup_worker, daemon=True)
    popup_thread.start()

    # 执行安装（使用动态超时）
    result = adb_cmd(
        device,
        ["shell", "pm", "install", "-r", "-d", "-g", "-t", "/data/local/tmp/temp.apk"],
        timeout=install_timeout
    )

    # 停止弹窗处理线程
    stop_event.set()
    popup_thread.join(timeout=3)

    adb_cmd(device, ["shell", "rm", "/data/local/tmp/temp.apk"])

    if result.returncode == 0 and "Success" in result.stdout:
        logger.info(f"  ✅ {device} 安装成功")
        return True
    else:
        logger.warning(f"  ⚠️ {device} 安装可能被弹窗干扰: {result.stdout.strip()[:100]}")
        # 再处理一次弹窗后重试
        handler.handle_install_popups(timeout=10)

        # 重试安装
        logger.info(f"  --处理弹窗后重试安装...")
        result_push = adb_cmd(device, ["push", apk_path, "/data/local/tmp/temp.apk"], timeout=push_timeout)
        if result_push.returncode != 0:
            return False

        stop_event2 = threading.Event()
        def popup_worker2():
            while not stop_event2.is_set():
                handler.handle_install_popups(timeout=5)
                time.sleep(1)

        popup_thread2 = threading.Thread(target=popup_worker2, daemon=True)
        popup_thread2.start()

        result = adb_cmd(
            device,
            ["shell", "pm", "install", "-r", "-d", "-g", "-t", "/data/local/tmp/temp.apk"],
            timeout=install_timeout
        )

        stop_event2.set()
        popup_thread2.join(timeout=3)

        adb_cmd(device, ["shell", "rm", "/data/local/tmp/temp.apk"])
        if result.returncode == 0 and "Success" in result.stdout:
            logger.info(f"  ✅ {device} 重试安装成功")
            return True
        logger.error(f"  ❌ {device} 安装失败: {result.stdout}")
        return False

def launch_app(device, package_name, launch_activity=None):
    """启动应用（增强版：启动后处理权限申请弹窗）"""
    logger.info(f"  --正在启动: {package_name}")

    # 清空 logcat 缓冲区，避免旧日志干扰 ANR 检测
    adb_cmd(device, ["shell", "logcat", "-c"])

    # 先尝试 monkey 启动
    adb_cmd(device, ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"])

    handler = PopupHandler(device)

    for i in range(5):
        time.sleep(3)
        # 处理可能出现的权限弹窗
        handler.handle_permission_popups(timeout=5)
        # 处理其他弹窗
        handler.handle_all_popups(timeout=3)

        if check_process(device, package_name):
            time.sleep(2)
            # 再处理一次弹窗
            handler.handle_permission_popups(timeout=5)
            handler.handle_all_popups(timeout=3)
            logger.info(f"  ✅ {device} 启动成功")
            return True
        logger.info(f"    ⏳ 等待启动中... ({i + 1}/5)")

    # monkey 启动失败，尝试 am start
    logger.warning(f"  ⚠️ monkey 启动失败，尝试 am start...")
    if launch_activity:
        activity_name = f"{package_name}/{launch_activity}"
    else:
        activity_name = f"{package_name}/.MainActivity"
    adb_cmd(device, ["shell", "am", "start", "-n", activity_name])
    time.sleep(5)

    # 处理权限弹窗
    handler.handle_permission_popups(timeout=8)
    handler.handle_all_popups(timeout=5)

    if check_process(device, package_name):
        logger.info(f"  ✅ {device} 启动成功（am start）")
        return True
    logger.error(f"  ❌ {device} 启动失败")
    return False

def check_process(device, package_name):
    """检测进程是否存活（pidof + ps 兜底）"""
    # 方式1: pidof（Android 8+ 支持）
    result = adb_cmd(device, ["shell", "pidof", package_name])
    if result.stdout.strip():
        return True
    # 方式2: ps 兜底（兼容旧版 Android 或精简系统）
    result = adb_cmd(device, ["shell", "ps", "-A", "-o", "NAME"])
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if package_name in line.strip():
                return True
    # 方式3: pgrep 兜底（部分系统支持）
    result = adb_cmd(device, ["shell", "pgrep", "-f", package_name])
    if result.stdout.strip():
        return True
    return False

def check_anr(device, package_name):
    """检测最近是否有 ANR 发生"""
    result = adb_cmd(device, [
        "shell", "logcat", "-d",
        "-s", "ActivityManager",
        "-t", "200"
    ])
    for line in result.stdout.splitlines():
        if "ANR in " + package_name in line or (
            "ANR in" in line and package_name in line
        ):
            logger.error(f"  ❌ {device} 检测到 ANR: {line.strip()}")
            return True
    return False

def get_foreground_package(device):
    """获取当前前台应用包名（多方式兜底）"""
    # 方式1: dumpsys window displays（Android 15+ 推荐）
    result = adb_cmd(device, ["shell", "dumpsys", "window", "displays"])
    for line in result.stdout.splitlines():
        # 格式: mCurrentFocus=Window{xxx u0 com.example.pkg/com.example.pkg.MainActivity}
        m = re.search(r"mCurrentFocus=.*?(\S+)/(\S+)", line)
        if m:
            return m.group(1)

    # 方式2: dumpsys activity activities（传统方式）
    result = adb_cmd(device, ["shell", "dumpsys", "activity", "activities"])
    for line in result.stdout.splitlines():
        if "mResumedActivity" in line or "mFocusedActivity" in line:
            m = re.search(r"(\S+)/(\S+)", line)
            if m:
                return m.group(1)

    # 方式3: dumpsys window windows（兜底）
    result2 = adb_cmd(device, ["shell", "dumpsys", "window", "windows"])
    for line in result2.stdout.splitlines():
        if "mCurrentFocus" in line:
            m = re.search(r"mCurrentFocus=.*?(\S+)/(\S+)", line)
            if m:
                return m.group(1)

    return ""

_fg_warned = {}  # device -> bool: 记录是否已输出过前台警告
_fg_ok_warned = {}  # device -> bool: 记录是否已输出过前台正常

def check_crash(device, package_name):
    """检测闪退：以进程存活为主要判断依据，前台检查仅警告不判定失败"""
    if not check_process(device, package_name):
        logger.error(f"  ❌ {device} 应用进程已消失（闪退）")
        return False
    fg = get_foreground_package(device)
    if fg and fg != package_name:
        if not _fg_warned.get(device):
            logger.warning(f"  ⚠️ {device} 进程存活但不在前台（当前前台: {fg}）")
            _fg_warned[device] = True
    elif not fg:
        if not _fg_warned.get(device):
            logger.warning(f"  ⚠️ {device} 进程存活，但无法获取前台应用信息（可能为系统版本兼容问题）")
            _fg_warned[device] = True
    else:
        if not _fg_ok_warned.get(device):
            logger.info(f"  ✅ {device} 应用在前台运行")
            _fg_ok_warned[device] = True
    if check_anr(device, package_name):
        logger.error(f"  ❌ {device} 检测到 ANR，测试失败")
        return False
    return True

def wait_and_monitor(device, package_name, minutes=5, interval=5,
                     log_dir=None):
    """定时检测应用运行状态（增强版：持续处理弹窗）"""
    logger.info(f"  --等待 {minutes} 分钟，每 {interval} 秒检查一次...")
    handler = PopupHandler(device)

    for i in range(minutes * 60 // interval):
        time.sleep(interval)

        # 持续处理可能出现的弹窗
        handler.handle_all_popups(timeout=3)

        if not check_crash(device, package_name):
            # 检查是否是因为弹窗导致的"不在前台"
            handler.handle_all_popups(timeout=5)
            handler.handle_permission_popups(timeout=5)
            # 再次检查
            if not check_crash(device, package_name):
                return False

        if (i + 1) % 12 == 0:
            logger.info(f"    {device} 已运行 {(i + 1) * interval // 60} 分钟，正常")
    logger.info(f"  ✅ {device} 运行期间无闪退")
    # 在应用保持前台运行的最后阶段拉取日志，确保 SDK 有足够时间生成完整日志
    pull_sdk_log(device, log_dir or os.getcwd(), package_name)
    return True

def switch_background_foreground(device, package_name, times=2):
    """前后台切换测试（增强版：切换后处理弹窗）"""
    handler = PopupHandler(device)

    for i in range(times):
        logger.info(f"  --第 {i + 1} 次前后台切换")
        adb_cmd(device, ["shell", "input", "keyevent", "KEYCODE_HOME"])
        time.sleep(2)
        adb_cmd(device, ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)

        # 处理弹窗
        handler.handle_all_popups(timeout=5)
        handler.handle_permission_popups(timeout=5)

        if not check_crash(device, package_name):
            logger.error(f"  ❌ {device} 切换后应用闪退")
            return False
        logger.info(f"  ✅ {device} 第 {i + 1} 次切换正常")
    return True

def force_stop_until_dead(device, package_name, max_retries=3, wait=2):
    """反复 force-stop 直到进程确认消失"""
    if not check_process(device, package_name):
        return True
    for attempt in range(max_retries):
        adb_cmd(device, ["shell", "am", "force-stop", package_name])
        time.sleep(wait)
        if not check_process(device, package_name):
            return True
        logger.warning(f"    ⚠️ 第 {attempt + 1} 次 force-stop 后进程仍在，重试...")
    return False

def kill_and_relaunch(device, package_name, launch_activity=None, times=3):
    """杀进程重启测试（增强版：重启后处理弹窗）"""
    handler = PopupHandler(device)

    for i in range(times):
        logger.info(f"  --第 {i + 1} 次杀进程重启")
        if not force_stop_until_dead(device, package_name):
            logger.error(f"  ❌ {device} 第 {i + 1} 次杀进程失败：进程仍在运行")
            return False
        adb_cmd(device, ["shell", "monkey", "-p", package_name,
                         "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)

        # 处理弹窗
        handler.handle_all_popups(timeout=5)
        handler.handle_permission_popups(timeout=5)

        if not check_crash(device, package_name):
            logger.error(f"  ❌ {device} 第 {i + 1} 次重启后闪退")
            return False
        logger.info(f"  ✅ {device} 第 {i + 1} 次重启正常")
    return True

def uninstall_apk(device, package_name):
    """卸载应用"""
    logger.info(f"  --正在卸载: {package_name}")
    adb_cmd(device, ["shell", "am", "force-stop", package_name])
    time.sleep(1)
    result = adb_cmd(device, ["uninstall", package_name], timeout=30)
    if result.returncode == 0 and "Success" in result.stdout:
        logger.info(f"  ✅ {device} 卸载成功")
        return True
    else:
        logger.warning(f"  ⚠️ {device} 卸载失败（可能已卸载）: {result.stdout}")
        return False

def _try_pull(device, remote_path, local_path):
    """尝试 pull，成功返回 True（旧版 adb 失败时 returncode 也可能是 0，必须查输出）"""
    if os.path.exists(local_path):
        os.remove(local_path)  # 清掉上次残留，避免误判
    result = adb_cmd(device, ["pull", remote_path, local_path], timeout=30)
    out = (result.stdout or "") + (result.stderr or "")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0 \
            and "No such file" not in out and "does not exist" not in out:
        return True
    # 清理失败时可能产生的空文件
    if os.path.exists(local_path) and os.path.getsize(local_path) == 0:
        os.remove(local_path)
    return False


def _try_read(device, remote_path, local_path):
    """尝试 shell cat（普通 + su）读取内容写本地"""
    result = adb_cmd(device, ["shell", f"cat {remote_path}"])
    out = (result.stdout or "") + (result.stderr or "")
    if result.stdout and result.stdout.strip() and "Permission denied" not in out:
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(result.stdout)
        return True
    # 无权限时用 su 读（模拟器一般自带 root）
    result = adb_cmd(device, ["shell", f"su -c 'cat {remote_path}'"])
    if result.stdout and result.stdout.strip():
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(result.stdout)
        return True
    return False


def pull_sdk_log(device, save_dir, pkg_name=None):
    """从设备拉取 SDK 日志 log.log 到本地（按 API 分界排序路径 + 多方式回退）

    路径规则（2026-10-28 官方口径确认）：
      - Android 10+（API >= 29）: /sdcard/Android/data/{被测应用自身包名}/files/log.log
      - Android 10 以下（API < 29）: /sdcard/sdk/log.log
      - 部分机型可能不同：主路径未命中时另一条路径兜底
    关键纠偏：高版本路径的"{包名}"一律指被测应用自身的包名；
    com.tencent.green.ace 只是 ACE_Tools 演示包的包名，并非固定 SDK 目录
    （ACE_Tools 的日志落在该目录，是因为那本来就是它自己的包目录）。
    注意：device 可能含冒号（如 127.0.0.1:5555），Windows 文件名必须先转义，
    否则会触发 NTFS 备用数据流，导致宿主文件为 0 字节（内容进不可见的流）。
    """
    os.makedirs(save_dir, exist_ok=True)
    safe_device = device.replace(":", "_")
    local_path = os.path.join(save_dir, f"{safe_device}_sdk_log.log")

    # 按设备 API 级别决定探测顺序，另一条路径兜底（部分机型可能不同）
    api_level = get_device_api_level(device)
    host_dir = f"/sdcard/Android/data/{pkg_name}/files" if pkg_name else None
    if api_level >= SDK_API_BOUNDARY:
        candidate_dirs = ([host_dir] if host_dir else []) + ["/sdcard/sdk"]
    else:
        candidate_dirs = ["/sdcard/sdk"] + ([host_dir] if host_dir else [])
    logger.info(f"  --{device} API={api_level}，优先探测: {candidate_dirs[0]}")

    def _fetch_one(remote_dir):
        """拉取 remote_dir/log.log，成功返回 True"""
        remote_path = f"{remote_dir}/log.log"
        # 先探测文件状态，把真实原因打出来（不静默吞错）
        ls_result = adb_cmd(device, ["shell", f"ls -l {remote_path} 2>&1"])
        ls_out = (ls_result.stdout or "") + (ls_result.stderr or "")
        if "No such file" in ls_out or "not found" in ls_out:
            logger.info(f"  --{device} 路径不存在: {remote_path}")
            return False
        if "Permission denied" in ls_out:
            logger.info(f"  --{device} 路径无权限: {remote_path}，尝试 root 方式读取")
        # 方式1: pull
        if _try_pull(device, remote_path, local_path):
            logger.info(f"  ✅ {device} log.log 已保存: {local_path} ({remote_path})")
            return True
        # 方式2: cat / su cat
        if _try_read(device, remote_path, local_path):
            logger.info(f"  ✅ {device} log.log 已保存: {local_path} ({remote_path}, cat方式)")
            return True
        return False

    for remote_dir in candidate_dirs:
        if _fetch_one(remote_dir):
            return True

    # 最后手段：模拟器可尝试 adb root 后重来（adbd 会重启，等 2 秒）
    root_result = adb_cmd(device, ["root"], timeout=10)
    root_out = (root_result.stdout or "") + (root_result.stderr or "")
    if "cannot run as root" not in root_out and "adbd cannot" not in root_out:
        time.sleep(2)  # adbd 重启等待
        for remote_dir in candidate_dirs:
            if _fetch_one(remote_dir):
                logger.info(f"  ✅ {device} SDK日志已保存（adb root 后）: {local_path}")
                return True

    logger.warning(f"  ⚠️ {device} 所有路径均未找到 log.log（应用可能未接入SDK或日志未生成）")
    return False


# ============================================================
# Enable 设置
# ============================================================

# SDK 读写路径规则（2026-10-28 官方口径确认）：
#   Android 10+（API >= 29）: /sdcard/Android/data/{被测应用自身包名}/files
#   Android 10 以下（API < 29）: /sdcard/sdk
#   部分机型可能不同——按被测包实测为准
# 已知 SDK 接入包（软件名 -> 包名）：
#   tlog_sdk     -> com.tencent.sdk            (Sdk_Tools_*_c++_64.apk)
#   tlog_sdk_ano -> com.tencent.sdk_ano        (ANO_Tools_*_c++_64.apk)
#   ACE_Tools    -> com.tencent.green.ace      (ACE_Tools_*_c#.apk)
#   ANO_Tools    -> com.tencent.green.ace.ano  (ANO_Tools_*_c#.apk)
# 历史纠偏：com.tencent.green.ace 不是"固定 SDK 目录"，它只是 ACE_Tools
# 演示包自己的包名；高版本路径的"{包名}"一律指被测应用自身包名
SDK_API_BOUNDARY = 29

# 5 种预设 enable 值
ENABLE_TYPES = {
    'sdk':      '0000000000000000090900009000000000000',
    'shell':    '0000000000000000090910009000000000000',
    'all_1':    '1111111111111111111111111111111111111111111',
    'official': '0000000000000000090000009000000000000',
    'perf_68':  '000000000000000009009000000000000000000000000000000000000000000000009',
}


def set_sdk_enable(device, pkg_name, enable_set='sdk'):
    """
    设置 SDK enable 开关到设备

    参数:
        device: 设备序列号
        pkg_name: 被测应用自身包名。高版本路径为 /sdcard/Android/data/<pkg>/files
                  （2026-10-28 官方确认："{包名}"指应用自身包名，
                   com.tencent.green.ace 只是 ACE_Tools 演示包的包名，并非固定 SDK 目录）
        enable_set: 预设类型，可选: sdk / shell / all_1 / official / perf_68
    """
    if enable_set not in ENABLE_TYPES:
        logger.error(f"  ❌ {device} 未知的 enable 类型: '{enable_set}'，可选: {list(ENABLE_TYPES.keys())}")
        return False

    enable_value = ENABLE_TYPES[enable_set]
    logger.info(f"  --设置 enable: type={enable_set}, value={enable_value}")

    # 获取 Android API 级别
    api_level = get_device_api_level(device)
    logger.info(f"  --设备 API 级别: {api_level}（分界: Android 10 / API {SDK_API_BOUNDARY}）")

    host_dir = f"/sdcard/Android/data/{pkg_name}/files" if pkg_name else None  # 被测应用自身外部私有目录（官方确认的高版本路径）
    low_dir = "/sdcard/sdk"  # 低版本路径（Android 10 以下）

    def _write_enable(dir_path, use_root):
        """向指定目录写入 enable.log 并读回验证"""
        if use_root:
            adb_cmd(device, ["shell", f"su -c 'mkdir -p {dir_path}'"])
            result = adb_cmd(device, ["shell", f"su -c 'echo {enable_value} > {dir_path}/enable.log'"])
            verify = adb_cmd(device, ["shell", f"su -c 'cat {dir_path}/enable.log'"])
        else:
            adb_cmd(device, ["shell", f"mkdir -p {dir_path} 2>/dev/null; true"])
            result = adb_cmd(device, ["shell", f"echo {enable_value} > {dir_path}/enable.log 2>/dev/null; true"])
            verify = adb_cmd(device, ["shell", f"cat {dir_path}/enable.log 2>/dev/null"])
        out = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0 and verify.stdout.strip() == enable_value:
            return True
        logger.warning(f"    ⚠️ 写入未通过验证: {dir_path} | rc={result.returncode} out='{out.strip()[:80]}'")
        return False

    # root 能力探测（高版本目录是应用自身私有目录，普通写失败时用 root 重试）
    su_check = adb_cmd(device, ["shell", "su", "-c", "id"])
    has_root = su_check.returncode == 0 and "uid=0" in (su_check.stdout or "")

    try:
        # root 能力补救：无 su 时尝试 adb root（部分模拟器支持）
        if not has_root:
            logger.warning(f"  --{device} 无 su 权限，尝试 adb root（模拟器一般支持）...")
            root_result = adb_cmd(device, ["root"], timeout=10)
            root_out = (root_result.stdout or "") + (root_result.stderr or "")
            if "cannot run as root" not in root_out and "adbd cannot" not in root_out:
                time.sleep(2)  # adbd 重启等待
                su_check = adb_cmd(device, ["shell", "su", "-c", "id"])
                has_root = su_check.returncode == 0 and "uid=0" in (su_check.stdout or "")
            else:
                logger.warning(f"  ⚠️ {device} adb root 失败")

        # 双路径写入策略（2026-10-28 官方口径，部分机型可能不同）：
        # 主路径与兜底路径都写，哪条读回验证通过算哪条，
        # 最大化覆盖不同机型上 SDK 的实际读取路径
        if api_level >= SDK_API_BOUNDARY:
            targets = [(host_dir, "高版本路径（应用自身目录）"), (low_dir, "低版本路径")]
        else:
            targets = [(low_dir, "低版本路径"), (host_dir, "高版本路径（应用自身目录）")]

        success_dirs = []
        for dir_path, label in targets:
            if not dir_path:
                continue
            ok = _write_enable(dir_path, use_root=False)
            if not ok and has_root:
                ok = _write_enable(dir_path, use_root=True)
            if ok:
                logger.info(f"  ✅ {device} enable 写入成功（{label}）: {dir_path}")
                success_dirs.append(dir_path)
            else:
                logger.warning(f"  ⚠️ {device} enable 写入失败（{label}）: {dir_path}")

        if success_dirs:
            logger.info(f"  ✅ {device} enable 设置完成，成功路径: {success_dirs}")
            return True

        logger.error(f"  ❌ {device} enable 写入失败（所有路径均未成功）")
        return False

    except Exception as e:
        logger.error(f"  ❌ {device} 设置 enable 异常: {e}")
        return False


def test_device(device, apk_path, package_name, launch_activity=None,
                run_minutes=5, switch_times=2, relaunch_times=3,
                skip_install=False, skip_uninstall=False, log_dir=None,
                apk_name=None, enable_set=None):
    """单设备完整测试流程"""
    logger.info(f"\n{'='*40}\n开始测试设备: {device}\n{'='*40}")

    if not check_device(device):
        return False

    if not skip_install:
        if not install_apk(device, apk_path, package_name):
            # 安装失败时应用从未运行，SDK 日志必然不存在，无需拉取
            return False
    else:
        logger.info(f"  --跳过安装步骤")

    # 设置 SDK enable 开关（安装后、启动前）
    if enable_set:
        set_sdk_enable(device, package_name, enable_set)

    if not launch_app(device, package_name, launch_activity):
        pull_sdk_log(device, log_dir or os.getcwd(), package_name)
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not wait_and_monitor(device, package_name, minutes=run_minutes,
                            log_dir=log_dir):
        logger.error(f"❌ {device} 测试失败：运行期间闪退")
        pull_sdk_log(device, log_dir or os.getcwd(), package_name)
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not switch_background_foreground(device, package_name, times=switch_times):
        logger.error(f"❌ {device} 测试失败：前后台切换异常")
        pull_sdk_log(device, log_dir or os.getcwd(), package_name)
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not kill_and_relaunch(device, package_name, launch_activity, times=relaunch_times):
        logger.error(f"❌ {device} 测试失败：杀进程重启异常")
        # 测试失败也拉取日志
        pull_sdk_log(device, log_dir or os.getcwd(), package_name)
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    logger.info(f"✅ {device} 全部测试通过")
    if not skip_uninstall:
        uninstall_apk(device, package_name)
    return True

def run_tests(devices, apk_path, package_name=None, launch_activity=None,
              run_minutes=5, switch_times=2, relaunch_times=3,
              skip_install=False, skip_uninstall=False,
              max_workers=None, log_dir=None, apk_name=None,
              enable_set=None):
    """多设备并行测试入口"""
    # 解析包名
    if not package_name:
        pkg, activity = get_package_name_from_apk(apk_path)
        if not pkg:
            logger.error("❌ 自动解析包名失败，请通过 --package 手动指定")
            return {}
        package_name = pkg
        launch_activity = launch_activity or activity
        logger.info(f"✅ 从 APK 自动解析到包名: {package_name}")
        if launch_activity:
            logger.info(f"   ↳ 启动 Activity: {launch_activity}")

    if not devices:
        logger.error("❌ 设备列表为空")
        return {}

    if max_workers is None:
        max_workers = len(devices)

    results = {}
    logger.info(f"  并行测试 {max_workers} 台设备...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                test_device, device, apk_path, package_name, launch_activity,
                run_minutes, switch_times, relaunch_times,
                skip_install, skip_uninstall, log_dir, apk_name, enable_set
            ): device for device in devices
        }
        for future in as_completed(future_map):
            device = future_map[future]
            try:
                results[device] = future.result()
            except Exception as e:
                logger.error(f"  ❌ {device} 测试异常: {e}")
                results[device] = False

    return results

def run_tests_batch(devices, apk_paths, run_minutes=5, switch_times=2,
                    relaunch_times=3, skip_install=False, skip_uninstall=False,
                    max_workers=None, log_dir=None, enable_set=None):
    """
    批量测试多个 APK（支持单 APK 或文件夹内所有 APK）

    参数:
        devices: 设备列表
        apk_paths: APK 文件路径列表
        其余参数同 run_tests

    返回:
        Dict[str, Dict[str, bool]]: {apk_path: {device: True/False, ...}, ...}
    """
    if not apk_paths:
        logger.error("❌ APK 列表为空")
        return {}

    all_results = {}
    total = len(apk_paths)

    for idx, apk_path in enumerate(apk_paths, 1):
        apk_name = os.path.basename(apk_path)
        logger.info(f"\n{'='*60}")
        logger.info(f"📦 测试 APK [{idx}/{total}]: {apk_name}")
        logger.info(f"   路径: {apk_path}")
        logger.info(f"{'='*60}")

        # 为当前 APK 创建子文件夹
        apk_log_dir = None
        if log_dir:
            apk_log_dir = os.path.join(log_dir, apk_name)
            os.makedirs(apk_log_dir, exist_ok=True)

        results = run_tests(
            devices=devices,
            apk_path=apk_path,
            run_minutes=run_minutes,
            switch_times=switch_times,
            relaunch_times=relaunch_times,
            skip_install=skip_install,
            skip_uninstall=skip_uninstall,
            max_workers=max_workers,
            log_dir=apk_log_dir,
            apk_name=apk_name,
            enable_set=enable_set,
        )
        all_results[apk_path] = results

        # 汇总当前 APK 结果
        passed = sum(1 for v in results.values() if v)
        logger.info(f"\n  📊 {apk_name} 结果: {passed}/{len(results)} 台通过")

    # 最终汇总
    logger.info(f"\n{'='*60}")
    logger.info("📊 全部 APK 测试汇总")
    logger.info(f"{'='*60}")
    total_passed = 0
    total_devices = 0
    for apk_path, results in all_results.items():
        apk_name = os.path.basename(apk_path)
        passed = sum(1 for v in results.values() if v)
        total_passed += passed
        total_devices += len(results)
        status = "✅" if passed == len(results) else "⚠️"
        logger.info(f"  {status} {apk_name}: {passed}/{len(results)} 台通过")
    logger.info(f"{'-'*60}")
    logger.info(f"  总计: {total_passed}/{total_devices} 项测试通过")
    logger.info(f"{'='*60}")

    return all_results
