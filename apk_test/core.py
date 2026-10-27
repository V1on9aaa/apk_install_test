"""
APK 自动化测试核心逻辑
"""
import time
import subprocess
import re
import shutil
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("apk_test")


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


def install_apk(device, apk_path, package_name):
    """安装 apk"""
    result_push = adb_cmd(device, ["push", apk_path, "/data/local/tmp/temp.apk"])
    if result_push.returncode != 0:
        logger.error(f"  ❌ {device} push APK 失败")
        return False
    result = adb_cmd(
        device,
        ["shell", "pm", "install", "-r", "-d", "-g", "-t", "/data/local/tmp/temp.apk"],
        timeout=120
    )
    adb_cmd(device, ["shell", "rm", "/data/local/tmp/temp.apk"])
    if result.returncode == 0 and "Success" in result.stdout:
        logger.info(f"  ✅ {device} 安装成功")
        return True
    else:
        logger.error(f"  ❌ {device} 安装失败: {result.stdout}")
        return False


def launch_app(device, package_name, launch_activity=None):
    """启动应用，带重试机制"""
    logger.info(f"  --正在启动: {package_name}")
    adb_cmd(device, ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"])
    for i in range(5):
        time.sleep(3)
        if check_process(device, package_name):
            time.sleep(2)
            logger.info(f"  ✅ {device} 启动成功")
            return True
        logger.info(f"    ⏳ 等待启动中... ({i + 1}/5)")
    logger.warning(f"  ⚠️ monkey 启动失败，尝试 am start...")
    if launch_activity:
        activity_name = f"{package_name}/{launch_activity}"
    else:
        activity_name = f"{package_name}/.MainActivity"
    adb_cmd(device, ["shell", "am", "start", "-n", activity_name])
    time.sleep(5)
    if check_process(device, package_name):
        logger.info(f"  ✅ {device} 启动成功（am start）")
        return True
    logger.error(f"  ❌ {device} 启动失败")
    return False


def check_process(device, package_name):
    """检测进程是否存活"""
    result = adb_cmd(device, ["shell", "pidof", package_name])
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
    """获取当前前台应用包名"""
    result = adb_cmd(device, ["shell", "dumpsys", "activity", "activities"])
    for line in result.stdout.splitlines():
        if "mResumedActivity" in line or "mFocusedActivity" in line:
            m = re.search(r"(\S+)/(\S+)", line)
            if m:
                return m.group(1)
    result2 = adb_cmd(device, ["shell", "dumpsys", "window", "windows"])
    for line in result2.stdout.splitlines():
        if "mCurrentFocus" in line:
            m = re.search(r"(\S+)/", line)
            if m:
                return m.group(1)
    return ""


def check_crash(device, package_name):
    """检测闪退：进程存活 且 应用在前台 且 无ANR"""
    if not check_process(device, package_name):
        logger.error(f"  ❌ {device} 应用进程已消失（闪退）")
        return False
    fg = get_foreground_package(device)
    if fg != package_name:
        logger.warning(f"  ⚠️ {device} 进程存活但不在前台（当前前台: {fg or '未知'}）")
        return False
    if check_anr(device, package_name):
        logger.error(f"  ❌ {device} 检测到 ANR，测试失败")
        return False
    return True


def wait_and_monitor(device, package_name, minutes=5, interval=5):
    """定时检测应用运行状态"""
    logger.info(f"  --等待 {minutes} 分钟，每 {interval} 秒检查一次...")
    for i in range(minutes * 60 // interval):
        time.sleep(interval)
        if not check_crash(device, package_name):
            return False
        if (i + 1) % 12 == 0:
            logger.info(f"    {device} 已运行 {(i + 1) * interval // 60} 分钟，正常")
    logger.info(f"  ✅ {device} 运行期间无闪退")
    return True


def switch_background_foreground(device, package_name, times=2):
    """前后台切换测试"""
    for i in range(times):
        logger.info(f"  --第 {i + 1} 次前后台切换")
        adb_cmd(device, ["shell", "input", "keyevent", "KEYCODE_HOME"])
        time.sleep(2)
        adb_cmd(device, ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)
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
    """杀进程重启测试"""
    for i in range(times):
        logger.info(f"  --第 {i + 1} 次杀进程重启")
        if not force_stop_until_dead(device, package_name):
            logger.error(f"  ❌ {device} 第 {i + 1} 次杀进程失败：进程仍在运行")
            return False
        adb_cmd(device, ["shell", "monkey", "-p", package_name,
                         "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)
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

def pull_sdk_log(device, save_dir):
    """从设备拉取 /sdcard/sdk/log.log 到本地"""
    local_path = os.path.join(save_dir, f"{device}_sdk_log.log")
    result = adb_cmd(device, ["pull", "/sdcard/sdk/log.log", local_path])
    if result.returncode == 0 and os.path.exists(local_path):
        logger.info(f"  ✅ {device} SDK日志已保存: {local_path}")
        return True
    else:
        logger.warning(f"  ⚠️ {device} 未找到 /sdcard/sdk/log.log（可能未生成）")
        return False


def test_device(device, apk_path, package_name, launch_activity=None,
                run_minutes=5, switch_times=2, relaunch_times=3,
                skip_install=False, skip_uninstall=False,log_dir=None):
    """单设备完整测试流程"""
    logger.info(f"\n{'='*40}\n开始测试设备: {device}\n{'='*40}")

    if not check_device(device):
        return False

    if not skip_install:
        if not install_apk(device, apk_path, package_name):
            return False
    else:
        logger.info(f"  --跳过安装步骤")

    if not launch_app(device, package_name, launch_activity):
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not wait_and_monitor(device, package_name, minutes=run_minutes):
        logger.error(f"❌ {device} 测试失败：运行期间闪退")
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not switch_background_foreground(device, package_name, times=switch_times):
        logger.error(f"❌ {device} 测试失败：前后台切换异常")
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    if not kill_and_relaunch(device, package_name, launch_activity, times=relaunch_times):
        logger.error(f"❌ {device} 测试失败：杀进程重启异常")
        if not skip_uninstall:
            uninstall_apk(device, package_name)
        return False

    logger.info(f"✅ {device} 全部测试通过")
    # 测试全部通过后，拉取 SDK 日志
    pull_sdk_log(device, log_dir or os.getcwd())
    if not skip_uninstall:
        uninstall_apk(device, package_name)
    return True


def run_tests(devices, apk_path, package_name=None, launch_activity=None,
              run_minutes=5, switch_times=2, relaunch_times=3,
              skip_install=False, skip_uninstall=False,
              max_workers=None,log_dir=None):
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
                skip_install, skip_uninstall,log_dir
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
