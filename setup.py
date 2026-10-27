from setuptools import setup, find_packages

setup(
    name="apk-test",
    version="1.0.0",
    description="APK 自动化测试工具 - 安装、运行、前后台切换、杀进程重启",
    author="apk-test",
    packages=find_packages(),
    python_requires=">=3.7",
    entry_points={
        "console_scripts": [
            "apk-test=apk_test.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)
