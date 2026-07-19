# -*- coding: utf-8 -*-
"""在桌面和开始菜单创建本工具的启动快捷方式（Windows）。

用法：
    python create_shortcut.py
    .venv\\Scripts\\python create_shortcut.py   # 用项目虚拟环境

说明：
- 依赖系统自带的 PowerShell 与 WScript.Shell COM，无需额外 Python 包。
- 桌面与「开始菜单 - 程序」各创建一份快捷方式，目标均指向 run.bat
  （双击即启动服务，保留控制台窗口显示日志，关闭窗口即停止服务）。
- 放进开始菜单后，即可在「所有应用」中找到并右键「固定到"开始"屏幕」，
  出现在开始菜单顶部的「已固定」网格；脚本也会自动尝试固定（COM Verbs），
  但该自动固定在部分 Windows 版本可能无效，无效时请手动右键固定。
"""
import base64
import os
import subprocess
import sys

if sys.platform != "win32":
    sys.exit("仅支持 Windows")

PROJECT = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(PROJECT, "run.bat")
ICON = os.path.join(PROJECT, ".venv", "Scripts", "python.exe")
icon_loc = ICON if "," in ICON else f"{ICON},0"
if not os.path.exists(ICON):
    icon_loc = "shell32.dll,13"  # 回退到系统文档图标

LINK_NAME = "文档检索工具.lnk"
DESC = "本地文档全文检索工具  http://127.0.0.1:8765"

# PowerShell 脚本：桌面与开始菜单各创建一份快捷方式，并尝试「固定到开始屏幕」。
# 单引号字符串里反斜杠无需转义；f-string 内 PowerShell 的 {} 需写成 {{ }}。
ps = f"""
$ws = New-Object -ComObject WScript.Shell
$project = '{PROJECT}'
$target = '{TARGET}'
$iconLoc = '{icon_loc}'
$name = '{LINK_NAME}'
$desc = '{DESC}'

function New-Lnk($path) {{
  $l = $ws.CreateShortcut($path)
  $l.TargetPath = $target
  $l.WorkingDirectory = $project
  $l.IconLocation = $iconLoc
  $l.Description = $desc
  $l.WindowStyle = 1
  $l.Save()
}}

$desktop = [Environment]::GetFolderPath('Desktop')
$desktopLnk = Join-Path $desktop $name
New-Lnk $desktopLnk
Write-Output ('DESKTOP ' + $desktopLnk)

$startMenu = [Environment]::GetFolderPath('Programs')
$startLnk = Join-Path $startMenu $name
New-Lnk $startLnk
Write-Output ('START ' + $startLnk)

# 尝试自动「固定到开始屏幕」（COM Verbs），中文匹配"开始"、英文匹配 Start
$sh = New-Object -ComObject Shell.Application
$dir = $sh.Namespace($startMenu)
$item = $dir.ParseName($name)
$pinned = 'NO'
if ($item) {{
  foreach ($v in $item.Verbs()) {{
    if ($v.Name -match '开始|Start') {{
      try {{ $v.DoIt(); $pinned = 'TRIED' }} catch {{}}
    }}
  }}
}}
Write-Output ('PIN ' + $pinned)
"""

# 用 -EncodedCommand 传递 UTF-16LE base64，彻底避开命令行中文编码问题
encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
    capture_output=True,
    text=True,
)
print(result.stdout.strip())
if result.returncode != 0:
    print(result.stderr, file=sys.stderr)
    sys.exit(result.returncode)
