# -*- coding: utf-8 -*-
"""打开文件所在目录 / 用 WPS（找不到则系统默认应用）打开文件。"""
import glob
import os
import platform
import subprocess

# Windows：不同文件类型对应 WPS 不同主程序
_WPS_WIN_EXE = {"wps": "wps.exe", "et": "et.exe", "wpp": "wpp.exe"}
_WPS_EXTS = {".xls": "et", ".xlsx": "et", ".csv": "et", ".ppt": "wpp", ".pptx": "wpp"}


def _find_wps_mac():
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        if os.path.isdir(os.path.join(d, "wpsoffice.app")):
            return "wpsoffice"
    return None


def _find_wps_win():
    """返回 WPS 的 office6 目录（内含 wps/et/wpp.exe），找不到返回 None。"""
    roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\Kingsoft\WPS Office"),
        os.path.expandvars(r"%ProgramFiles%\Kingsoft\WPS Office"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Kingsoft\WPS Office"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d in sorted(glob.glob(os.path.join(root, "*", "office6"))):
            if os.path.isfile(os.path.join(d, "wps.exe")):
                return d
    return None


def find_wps():
    if platform.system() == "Windows":
        return _find_wps_win()
    return _find_wps_mac()


def _wps_exe_for(path):
    d = _find_wps_win()
    if not d:
        return None
    kind = _WPS_EXTS.get(os.path.splitext(path)[1].lower(), "wps")
    exe = os.path.join(d, _WPS_WIN_EXE[kind])
    # Windows 路径规范化为反斜杠
    if os.path.isfile(exe):
        return exe.replace("/", "\\")
    return os.path.join(d, "wps.exe").replace("/", "\\")


def open_folder(path):
    """在文件管理器中定位该文件。返回 {ok} 或 {ok:False, error}。"""
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            subprocess.run(["open", "-R", path], timeout=10)
        elif sys_name == "Windows":
            # explorer 成功时也常返回 1，不判返回码
            subprocess.run(["explorer", "/select,", path], timeout=10)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path) or "."], timeout=10)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


def open_with_wps(path):
    """用 WPS 打开；没装 WPS 退回系统默认应用。返回 {ok, method}。"""
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            if find_wps():
                subprocess.run(["open", "-a", "wpsoffice", path], timeout=10)
                return {"ok": True, "method": "wps"}
            subprocess.run(["open", path], timeout=10)
            return {"ok": True, "method": "default"}
        if sys_name == "Windows":
            exe = _wps_exe_for(path)
            if exe:
                subprocess.run([exe, path], timeout=10)
                return {"ok": True, "method": "wps"}
            getattr(os, "startfile", lambda x: subprocess.run(["cmd", "/c", "start", "", x], timeout=10))(path)
            return {"ok": True, "method": "default"}
        subprocess.run(["xdg-open", path], timeout=10)
        return {"ok": True, "method": "default"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}
