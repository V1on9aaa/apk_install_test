# 写一个python脚本，功能是
# 安装apk、运行5分钟、之后前后台切换两次、最后杀死应用重启3次
# 确认过程中没有发生闪退（应用在前台且进程存活）
#
# 脚本中用到的工具、python模块由你自己决定
#
# 这个在你空闲时间可以做一下
# AI协助率控制在20%，做资料查询、架构设计、后期优化是没问题的
import time
import subprocess
import re
import shutil
import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# 流程:检查链接->安装->启动->等待五分钟(每五秒检测一次是否闪退黑屏)->切后台*2->杀进程重启*3->输出结果
"""
缺陷:1.无法处理系统安装器弹窗
2.无法处理权限申请弹窗
todo:
"""
APK_PATH = r"E:\Test\sdk\origin\Tools_c++.apk"
PACKAGE_NAME = "" # 自动解析
DEVICE_LIST = ["45a2cacc","FA7831801432"]
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")   # 日志输出目录

logger = logging.getLogger("auto_test")
log_file_path = None
LAUNCH_ACTIVITY = None

def adb_cmd(device, args, timeout=30):
    cmd = ["adb", "-s", device] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print_and_log(logging.WARNING, f"  ⚠️ 设备 {device} adb 命令超时: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="timeout")


def setup_logging():
    """配置日志：只写文件，带时间戳，支持并行写入"""
    global log_file_path
    # 清除已有 handler，防止重复添加
    logger.handlers.clear()

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(LOG_DIR, f"test_{timestamp}.log")

    logger.setLevel(logging.DEBUG)
    # 只加文件 handler，不加控制台 handler
    fh = logging.FileHandler(log_file_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)
    print(f"📝 日志文件: {log_file_path}")


def log(level, msg):
    """写日志到文件"""
    logger.log(level, msg)

def print_and_log(level, msg):
    """同时输出到控制台（print）和日志文件"""
    print(msg)
    logger.log(level, msg)

def pull_sdk_log(device, save_dir):
    """从设备拉取 /sdcard/sdk/log.log 到本地"""
    local_path = os.path.join(save_dir, f"{device}_sdk_log.log")
    result = adb_cmd(device, ["pull", "/sdcard/sdk/log.log", local_path])
    if result.returncode == 0 and os.path.exists(local_path):
        print_and_log(logging.INFO, f"  ✅ {device} SDK日志已保存: {local_path}")
        return True
    else:
        print_and_log(logging.WARNING, f"  ⚠️ {device} 未找到 /sdcard/sdk/log.log（可能未生成）")
        return False


def get_package_name_from_apk(apk_path):
    """从 APK 文件解析包名和启动 Activity，返回 (package_name, launch_activity)"""
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


def resolve_package_name():
    global PACKAGE_NAME, LAUNCH_ACTIVITY
    if PACKAGE_NAME:
        print_and_log(logging.INFO, f"✅ 使用手动配置的包名: {PACKAGE_NAME}")
        return True
    pkg, activity = get_package_name_from_apk(APK_PATH)
    if not pkg:
        print_and_log(logging.ERROR, "❌ 自动解析包名失败...")
        return False
    PACKAGE_NAME = pkg
    LAUNCH_ACTIVITY = activity  # 可能为 None
    print_and_log(logging.INFO, f"✅ 从 APK 自动解析到包名: {PACKAGE_NAME}")
    if LAUNCH_ACTIVITY:
        print_and_log(logging.INFO, f"   ↳ 启动 Activity: {LAUNCH_ACTIVITY}")
    return True


def check_device(device):
    """检查设备状态"""
    # adb -s dstr get-state
    result = adb_cmd(device, ["get-state"])
    if result.returncode != 0:
        print_and_log(logging.ERROR, f"❌ 设备 {device} 未连接")
        return False
    print_and_log(logging.INFO, f"✅ 设备 {device} 已连接")
    return True

def install_apk(device):
    """安装apk"""
    # ❌adb -s dstr install -r a.apk
    # ❌--bypass-low-target-sdk-block (Android 12+支持)
    # pm install 尝试绕过系统安装器弹窗 (部分不支持)
    # ❌固定点位点击
    # 配合ui检测

    # 将 APK push 到设备临时目录
    result_push = adb_cmd(device, ["push", APK_PATH, "/data/local/tmp/temp.apk"])
    if result_push.returncode != 0:
        print_and_log(logging.ERROR, f"  ❌ {device} push APK 失败")
        return False
    result = adb_cmd(
        device,
        ["shell", "pm", "install", "-r", "-d", "-g", "-t", "/data/local/tmp/temp.apk"],
        timeout=120
    )
    # 清理临时文件
    adb_cmd(device, ["shell", "rm", "/data/local/tmp/temp.apk"])
    if result.returncode == 0 and "Success" in result.stdout:
        print_and_log(logging.INFO, f"  ✅ {device} 安装成功")
        return True
    else:
        print_and_log(logging.ERROR, f"  ❌ {device} 安装失败: {result.stdout}")
        return False

def launch_app(device):
    """启动+重试机制"""
    # adb -s dstr shell monkey -p com.example.app -c android.intent.category.LAUNCHER 1
    print_and_log(logging.INFO, f"  --正在启动: {PACKAGE_NAME}")
    adb_cmd(device, ["shell", "monkey", "-p", PACKAGE_NAME, "-c", "android.intent.category.LAUNCHER", "1"])
    # 等待并重试检测，最多等 15 秒
    for i in range(5):
        time.sleep(3)
        if check_process(device):
            time.sleep(2)
            print_and_log(logging.INFO, f"  ✅ {device} 启动成功")
            return True
        print_and_log(logging.INFO, f"    ⏳ 等待启动中... ({i + 1}/5)")
    print_and_log(logging.WARNING, f"  ⚠️ monkey 启动失败，尝试 am start...")
    if LAUNCH_ACTIVITY:
        activity_name = f"{PACKAGE_NAME}/{LAUNCH_ACTIVITY}"
    else:
        activity_name = f"{PACKAGE_NAME}/.MainActivity"
    adb_cmd(device, ["shell", "am", "start", "-n", activity_name])
    time.sleep(5)
    if check_process(device):
        print_and_log(logging.INFO, f"  ✅ {device} 启动成功（am start）")
        return True
    print_and_log(logging.ERROR, f"  ❌ {device} 启动失败")
    return False

def check_process(device):
    """检测进程是否存活，存活返回True"""
    # adb -s 45a2cacc shell pidof com.tencent.tools
    result = adb_cmd(device, ["shell", "pidof", PACKAGE_NAME])
    if result.stdout.strip():
        return True
    return False

def check_anr(device):
    """检测最近是否有 ANR 发生"""
    result = adb_cmd(device, [
        "shell", "logcat", "-d",
        "-s", "ActivityManager",
        "-t", "200"
    ])
    for line in result.stdout.splitlines():
        # ANR 的典型 log 特征
        if "ANR in " + PACKAGE_NAME in line or (
            "ANR in" in line and PACKAGE_NAME in line
        ):
            print_and_log(logging.ERROR, f"  ❌ {device} 检测到 ANR: {line.strip()}")
            return True
    return False

def get_foreground_package(device):
    """获取当前前台应用包名，取不到返回空串"""
    result = adb_cmd(device, ["shell", "dumpsys", "activity", "activities"])
    for line in result.stdout.splitlines():
        if "mResumedActivity" in line or "mFocusedActivity" in line:
            m = re.search(r"(\S+)/(\S+)", line)
            if m:
                return m.group(1)
    # 兼容部分系统版本
    result2 = adb_cmd(device, ["shell", "dumpsys", "window", "windows"])
    for line in result2.stdout.splitlines():
        if "mCurrentFocus" in line:
            m = re.search(r"(\S+)/", line)
            if m:
                return m.group(1)
    return ""

def check_crash(device):
    """检测闪退：进程存活 且 应用在前台 且 无ANR"""
    if not check_process(device):
        print_and_log(logging.ERROR, f"  ❌ {device} 应用进程已消失（闪退）")
        return False
    fg = get_foreground_package(device)
    if fg != PACKAGE_NAME:
        print_and_log(logging.WARNING, f"  ⚠️ {device} 进程存活但不在前台（当前前台: {fg or '未知'}）")
        return False
    if check_anr(device):
        print_and_log(logging.ERROR, f"  ❌ {device} 检测到 ANR，测试失败")
        return False
    return True

def wait_and_monitor(device, minutes=5, interval=5):
    """定时检测"""
    # adb -s dstr shell pidof com.example.app
    print_and_log(logging.INFO, f"  --等待 {minutes} 分钟，每 {interval} 秒检查一次...")
    for i in range(minutes * 60 // interval):
        time.sleep(interval)
        if not check_crash(device):
            return False
        if (i + 1) % 12 == 0:
            print_and_log(logging.INFO, f"    {device} 已运行 {(i + 1) * interval // 60} 分钟，正常")
    print_and_log(logging.INFO, f"  ✅ {device} 运行期间无闪退")
    return True

def switch_background_foreground(device, times=2):
    """切换前后台"""
    # adb -s dstr shell input keyevent KEYCODE_HOME
    # adb -s dstr shell monkey -p com.example.app -c android.intent.category.LAUNCHER 1
    for i in range(times):
        print_and_log(logging.INFO, f"  --第 {i + 1} 次前后台切换")
        adb_cmd(device, ["shell", "input", "keyevent", "KEYCODE_HOME"])
        time.sleep(2)
        adb_cmd(device, ["shell", "monkey", "-p", PACKAGE_NAME, "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)
        if not check_crash(device):
            print_and_log(logging.ERROR, f"  ❌ {device} 切换后应用闪退")
            return False
        print_and_log(logging.INFO, f"  ✅ {device} 第 {i + 1} 次切换正常")
    return True

def force_stop_until_dead(device, max_retries=3, wait=2):
    """
    反复 force-stop 直到进程确认消失。
    返回 True 表示已杀死；False 表示重试耗尽进程仍在。
    """
    # adb -s dstr shell am force-stop com.example.app
    if not check_process(device):
        return True
    for attempt in range(max_retries):
        adb_cmd(device, ["shell", "am", "force-stop", PACKAGE_NAME])
        time.sleep(wait)
        if not check_process(device):
            return True
        print_and_log(logging.WARNING, f"    ⚠️ 第 {attempt + 1} 次 force-stop 后进程仍在，重试...")
    return False

def kill_and_relaunch(device, times=3):
    """杀进程重启：杀到进程确认消失 -> 启动 -> 检测未闪退，重复 times 次"""
    # adb -s dstr shell monkey -p com.example.app -c android.intent.category.LAUNCHER 1
    for i in range(times):
        print_and_log(logging.INFO, f"  --第 {i + 1} 次杀进程重启")
        # 杀进程，并确认进程真的已退出
        if not force_stop_until_dead(device):
            print_and_log(logging.ERROR, f"  ❌ {device} 第 {i + 1} 次杀进程失败：进程仍在运行")
            return False
        # 重新启动
        adb_cmd(device, ["shell", "monkey", "-p", PACKAGE_NAME,
                         "-c", "android.intent.category.LAUNCHER", "1"])
        time.sleep(3)
        if not check_crash(device):
            print_and_log(logging.ERROR, f"  ❌ {device} 第 {i + 1} 次重启后闪退")
            return False
        print_and_log(logging.INFO, f"  ✅ {device} 第 {i + 1} 次重启正常")

    return True

def uninstall_apk(device):
    """卸载已安装的应用"""
    print_and_log(logging.INFO, f"  --正在卸载: {PACKAGE_NAME}")
    # 先 force-stop 确保进程已退出
    adb_cmd(device, ["shell", "am", "force-stop", PACKAGE_NAME])
    time.sleep(1)
    result = adb_cmd(device, ["uninstall", PACKAGE_NAME], timeout=30)
    if result.returncode == 0 and "Success" in result.stdout:
        print_and_log(logging.INFO, f"  ✅ {device} 卸载成功")
        return True
    else:
        print_and_log(logging.WARNING, f"  ⚠️ {device} 卸载失败（可能已卸载）: {result.stdout}")
        return False


def test_device(device):
    print_and_log(logging.INFO, f"\n{'='*40}\n开始测试设备: {device}\n{'='*40}")


    if not check_device(device):
        return False

    if not install_apk(device):
        return False

    if not launch_app(device):
        uninstall_apk(device)
        return False

    if not wait_and_monitor(device):
        print_and_log(logging.ERROR, f"❌ {device} 测试失败：运行期间闪退")
        uninstall_apk(device)
        return False

    if not switch_background_foreground(device):
        print_and_log(logging.ERROR, f"❌ {device} 测试失败：前后台切换异常")
        uninstall_apk(device)
        return False

    if not kill_and_relaunch(device):
        print_and_log(logging.ERROR, f"❌ {device} 测试失败：杀进程重启异常")
        uninstall_apk(device)
        return False


    print_and_log(logging.INFO, f"✅ {device} 全部测试通过")
    # 测试全部通过后，拉取 SDK 日志
    pull_sdk_log(device, LOG_DIR)
    # 测试结束后卸载应用
    uninstall_apk(device)
    return True

def main():
    global PACKAGE_NAME
    # 初始化日志
    setup_logging()

    print_and_log(logging.INFO, "=" * 40)
    print_and_log(logging.INFO, "多设备自动化测试开始")
    print_and_log(logging.INFO, f"待测设备: {DEVICE_LIST}")
    print_and_log(logging.INFO, f"APK 路径: {APK_PATH}")
    print_and_log(logging.INFO, "=" * 40)

    # 1. 设备列表检查
    if not DEVICE_LIST:
        print_and_log(logging.ERROR, "❌ 设备列表为空，请填写 DEVICE_LIST")
        return

    # 2. 确定包名（手动优先，否则从 APK 解析）
    if not resolve_package_name():
        return

    # 3. 逐台设备并行测试
    results = {}
    max_workers = len(DEVICE_LIST)
    print_and_log(logging.INFO, f"  并行测试 {max_workers} 台设备...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(test_device, device): device for device in DEVICE_LIST}
        for future in as_completed(future_map):
            device = future_map[future]
            try:
                results[device] = future.result()
            except Exception as e:
                print_and_log(logging.ERROR, f"  ❌ {device} 测试异常: {e}")
                results[device] = False

    # 4. 汇总结果
    print_and_log(logging.INFO, f"{'=' * 40}\n测试结果汇总\n{'=' * 40}")
    for device, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print_and_log(logging.INFO, f"  {device}: {status}")

    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    print_and_log(logging.INFO, f"{'-' * 40}\n  总计: {passed_count}/{total} 台通过")


if __name__ == '__main__':
    main()

