#!/usr/bin/env python3
"""
APK 自动化测试 CLI 入口

用法:
  apk-test run --apk path/to/app.apk --device 45a2cacc
  apk-test run --apk path/to/app.apk --device 45a2cacc,FA7831801432 --minutes 3
  apk-test run --apk-dir ./apks --device 45a2cacc
  apk-test devices
  apk-test info --apk path/to/app.apk
"""
import argparse
import sys
import os
import time
import logging

from .core import (
    run_tests, run_tests_batch, check_device, get_package_name_from_apk, adb_cmd, logger
)


def setup_logging(log_dir, log_level=logging.INFO):
    """配置日志输出到文件和控制台"""
    logger.handlers.clear()
    logger.setLevel(log_level)

    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "apk_test.log")

    # 文件 handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return log_file


def cmd_devices(args):
    """列出已连接的设备"""
    result = adb_cmd("", ["devices"])
    if result.returncode != 0:
        print("❌ adb 命令执行失败，请确认 adb 已安装并配置")
        return 1
    print("已连接的设备:")
    for line in result.stdout.splitlines():
        if "\tdevice" in line:
            device_id = line.split("\t")[0]
            print(f"  ✅ {device_id}")
        elif "\tunauthorized" in line:
            device_id = line.split("\t")[0]
            print(f"  ⚠️  {device_id} (未授权)")
        elif "\toffline" in line:
            device_id = line.split("\t")[0]
            print(f"  ❌ {device_id} (离线)")
    return 0


def cmd_info(args):
    """解析 APK 信息"""
    if not os.path.isfile(args.apk):
        print(f"❌ 文件不存在: {args.apk}")
        return 1
    pkg, activity = get_package_name_from_apk(args.apk)
    if pkg:
        print(f"📦 包名: {pkg}")
        print(f"🚀 启动 Activity: {activity or '未知'}")
    else:
        print("❌ 无法解析 APK 信息，请确认 aapt/aapt2 已安装")
        return 1
    return 0


def cmd_run(args):
    """执行测试"""
    # 确定 APK 路径列表
    apk_paths = []
    if args.apk_dir:
        # 文件夹模式：扫描所有 .apk 文件
        if not os.path.isdir(args.apk_dir):
            print(f"❌ 文件夹不存在: {args.apk_dir}")
            return 1
        apk_paths = sorted([
            os.path.join(args.apk_dir, f)
            for f in os.listdir(args.apk_dir)
            if f.lower().endswith(".apk")
        ])
        if not apk_paths:
            print(f"❌ 文件夹内未找到 .apk 文件: {args.apk_dir}")
            return 1
        print(f"📁 从文件夹找到 {len(apk_paths)} 个 APK:")
        for p in apk_paths:
            print(f"   - {os.path.basename(p)}")
    else:
        # 单 APK 模式
        if not os.path.isfile(args.apk):
            print(f"❌ APK 文件不存在: {args.apk}")
            return 1
        apk_paths = [args.apk]

    devices = [d.strip() for d in args.device.split(",") if d.strip()]
    if not devices:
        print("❌ 请指定至少一个设备")
        return 1

    # 创建时间戳文件夹作为本次测试的根日志目录
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_log_dir = os.path.join(
        args.log_dir or os.path.dirname(apk_paths[0]),
        "log",
        timestamp
    )
    log_file = setup_logging(base_log_dir)
    print(f"📝 日志目录: {base_log_dir}")
    print(f"📝 日志文件: {log_file}")

    logger.info("=" * 50)
    logger.info("APK 自动化测试开始")
    logger.info(f"  APK 数量: {len(apk_paths)}")
    logger.info(f"  设备: {devices}")
    logger.info(f"  运行时长: {args.minutes} 分钟")
    logger.info(f"  前后台切换: {args.switch} 次")
    logger.info(f"  杀进程重启: {args.relaunch} 次")
    logger.info(f"  跳过安装: {args.skip_install}")
    logger.info(f"  跳过卸载: {args.skip_uninstall}")
    logger.info("=" * 50)

    if len(apk_paths) == 1:
        # 单 APK 模式
        apk_name = os.path.basename(apk_paths[0])
        apk_log_dir = os.path.join(base_log_dir, apk_name)
        os.makedirs(apk_log_dir, exist_ok=True)

        results = run_tests(
            devices=devices,
            apk_path=apk_paths[0],
            package_name=args.package,
            launch_activity=args.activity,
            run_minutes=args.minutes,
            switch_times=args.switch,
            relaunch_times=args.relaunch,
            skip_install=args.skip_install,
            skip_uninstall=args.skip_uninstall,
            max_workers=args.parallel,
            log_dir=apk_log_dir,
            apk_name=apk_name,
        )

        # 汇总结果
        logger.info(f"\n{'='*50}\n测试结果汇总\n{'='*50}")
        passed = 0
        for device, ok in results.items():
            status = "✅ 通过" if ok else "❌ 失败"
            logger.info(f"  {device}: {status}")
            if ok:
                passed += 1
        logger.info(f"{'-'*50}")
        logger.info(f"  总计: {passed}/{len(results)} 台通过")
        logger.info(f"{'='*50}")

        return 0 if passed == len(results) else 1
    else:
        # 批量模式
        results = run_tests_batch(
            devices=devices,
            apk_paths=apk_paths,
            run_minutes=args.minutes,
            switch_times=args.switch,
            relaunch_times=args.relaunch,
            skip_install=args.skip_install,
            skip_uninstall=args.skip_uninstall,
            max_workers=args.parallel,
            log_dir=base_log_dir,
        )

        # 统计最终结果
        total_passed = 0
        total_tests = 0
        for apk_path, device_results in results.items():
            for ok in device_results.values():
                total_tests += 1
                if ok:
                    total_passed += 1

        return 0 if total_passed == total_tests else 1


def main():
    parser = argparse.ArgumentParser(
        description="APK 自动化测试工具 - 安装、运行、切换、重启测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  %(prog)s run --apk app.apk --device 45a2cacc
  %(prog)s run --apk app.apk --device 45a2cacc,FA7831801432 --minutes 3
  %(prog)s run --apk-dir ./apks --device 45a2cacc
  %(prog)s run --apk app.apk --device 45a2cacc --skip-install --skip-uninstall
  %(prog)s devices
  %(prog)s info --apk app.apk
        """
    )
    parser.add_argument("--version", action="version", version="apk-test 1.0.0")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="执行完整测试流程")
    run_parser.add_argument("--apk", default=None, help="APK 文件路径（与 --apk-dir 二选一）")
    run_parser.add_argument("--apk-dir", default=None,
                            help="APK 文件夹路径，自动扫描所有 .apk 文件（与 --apk 二选一）")
    run_parser.add_argument("--device", "-d", required=True,
                            help="设备序列号，多个用逗号分隔，如: 45a2cacc,FA7831801432")
    run_parser.add_argument("--package", "-p", default=None,
                            help="应用包名（可选，不指定则自动从 APK 解析）")
    run_parser.add_argument("--activity", default=None,
                            help="启动 Activity（可选，不指定则自动解析）")
    run_parser.add_argument("--minutes", "-m", type=int, default=5,
                            help="运行时长（分钟），默认 5")
    run_parser.add_argument("--switch", "-s", type=int, default=2,
                            help="前后台切换次数，默认 2")
    run_parser.add_argument("--relaunch", "-r", type=int, default=3,
                            help="杀进程重启次数，默认 3")

    run_parser.add_argument("--parallel", type=int, default=None,
                            help="并行设备数，默认全部同时")
    run_parser.add_argument("--log-dir", default=None,
                            help="日志输出目录，默认 APK 所在目录")
    run_parser.add_argument("--skip-install", action="store_true",
                            help="跳过安装步骤（应用已安装时使用）")
    run_parser.add_argument("--skip-uninstall", action="store_true",
                            help="跳过卸载步骤")

    # devices 子命令
    subparsers.add_parser("devices", help="列出已连接的设备")

    # info 子命令
    info_parser = subparsers.add_parser("info", help="解析 APK 信息")
    info_parser.add_argument("--apk", required=True, help="APK 文件路径")

    args = parser.parse_args()

    if args.command == "run":
        # 校验：--apk 和 --apk-dir 二选一
        if not args.apk and not args.apk_dir:
            print("❌ 请指定 --apk（单文件）或 --apk-dir（文件夹）")
            return 1
        if args.apk and args.apk_dir:
            print("❌ --apk 和 --apk-dir 不能同时使用，请二选一")
            return 1
        return cmd_run(args)
    elif args.command == "devices":
        return cmd_devices(args)
    elif args.command == "info":
        return cmd_info(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
