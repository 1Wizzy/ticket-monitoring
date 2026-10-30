# -*- coding: utf-8 -*-
"""App 登录态快照：导出 / 恢复 / 校验。

与浏览器渠道的 `user_data` 目录同一思路——登录凭证落在本地，不传云。
实测（2026-10-29，智行 145MB 数据目录）只打包
`shared_prefs + databases + files` 三个目录 **3.1 MB**，
`app_ctripwebapp6`（128MB）是网页资源缓存，与登录态无关，不打包。

安全边界：
  * 快照等同 cookies，权限按用户目录策略，不做任何上传；
  * 文件名固定 `<plat>.tar.gz`，落 `user_data/<profile>/android_snapshots/`；
  * 恢复前先停 App，避免边写边读造成数据库损坏。
"""
from __future__ import annotations

import os
import time

SNAP_DIRNAME = "android_snapshots"
# 只打包与登录态相关的目录；缓存目录明确排除
SNAP_PATHS = ("shared_prefs", "databases", "files")
DEVICE_TMP = "/sdcard/tm_snapshot.tgz"


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


class SnapshotStore:
    """按渠道管理快照文件。"""

    def __init__(self, base_dir: str):
        self.base_dir = _ensure_dir(os.path.abspath(base_dir))

    def path_for(self, plat: str) -> str:
        safe = "".join(c for c in str(plat) if c.isalnum() or c in "_-")
        return os.path.join(self.base_dir, f"{safe}.tar.gz")

    def exists(self, plat: str) -> bool:
        p = self.path_for(plat)
        return os.path.isfile(p) and os.path.getsize(p) > 0

    def info(self, plat: str) -> dict:
        p = self.path_for(plat)
        if not self.exists(plat):
            return {"exists": False}
        st = os.stat(p)
        return {
            "exists": True,
            "size": st.st_size,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(st.st_mtime)),
            "path": p,
        }

    def save_bytes(self, plat: str, data: bytes) -> dict:
        if not data:
            raise ValueError("empty snapshot")
        p = self.path_for(plat)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
        return self.info(plat)

    def load_bytes(self, plat: str) -> bytes:
        p = self.path_for(plat)
        if not self.exists(plat):
            return b""
        with open(p, "rb") as f:
            return f.read()

    def drop(self, plat: str):
        p = self.path_for(plat)
        if os.path.isfile(p):
            os.remove(p)


class SnapshotManager:
    """在真实设备上导出/恢复快照。"""

    def __init__(self, device, store: SnapshotStore, package: str,
                 paths=SNAP_PATHS, logger=None):
        self.device = device
        self.store = store
        self.package = package
        self.paths = tuple(paths)
        self.logger = logger
        self.last_error = ""

    # ---------- 导出 ----------
    def export(self, plat: str) -> tuple:
        """返回 (ok, detail)。成功时 detail 带文件大小。"""
        dev = self.device
        if dev is None:
            return False, "设备不可用"
        if not dev.root():
            # 没有 root 就读不到 /data/data；明确报出来而不是静默失败
            return False, "无法获取 root（快照需要读取 App 私有目录）"

        base = f"/data/data/{self.package}"
        members = " ".join(self.paths)
        cmd = (f"cd {base} && rm -f {DEVICE_TMP} && "
               f"tar czf {DEVICE_TMP} {members} 2>&1")
        out = dev._sh("shell", cmd, timeout=180) or ""
        if os.path.basename(DEVICE_TMP) not in str(
                dev._sh("shell", f"ls {DEVICE_TMP} 2>&1", timeout=30) or ""):
            self.last_error = (out or "tar 失败").strip()[:200]
            return False, self.last_error

        proc = None
        try:
            from .device import _run
            proc = _run([dev.adb_path, "-s", dev.serial,
                         "exec-out", f"cat {DEVICE_TMP}"], 180)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False, self.last_error
        data = (proc.stdout if proc else b"") or b""
        if not data.startswith(b"\x1f\x8b"):
            self.last_error = "取回的数据不是 gzip"
            return False, self.last_error

        info = self.store.save_bytes(plat, data)
        dev._sh("shell", f"rm -f {DEVICE_TMP}", timeout=30)
        return True, f"快照 {info['size'] / 1048576:.1f} MB"

    # ---------- 恢复 ----------
    def device_login_mtime(self) -> int:
        """设备上**登录态**的修改时间（epoch 秒）；不存在或读不到返回 0。

        只认 `databases/zt_userInfo.db`（智行的账号信息库）。刻意不用
        shared_prefs：App 一跑起来就会写它，拿它比较会让「设备永远更新」
        从而永不恢复——恢复功能等于关掉。
        """
        dev = self.device
        if dev is None:
            return 0
        out = dev._sh(
            "shell",
            f"stat -c '%Y' /data/data/{self.package}/databases/zt_userInfo.db"
            " 2>/dev/null", timeout=30) or ""
        try:
            return int(out.strip().splitlines()[0])
        except (ValueError, IndexError):
            return 0

    def restore(self, plat: str) -> tuple:
        """返回 (ok, detail)。没有快照时如实返回 False，不假装成功。

        设备侧已有**比快照更新**的登录态时不覆盖：那会把用户刚登的账号
        回滚掉。设备上没有登录库（未登录/新装）时正常恢复。
        """
        dev = self.device
        if dev is None:
            return False, "设备不可用"
        if not self.store.exists(plat):
            return False, "没有可恢复的快照"
        if not dev.root():
            return False, "无法获取 root"

        snap_at = int(os.path.getmtime(self.store.path_for(plat)))
        dev_at = self.device_login_mtime()
        if dev_at and dev_at > snap_at:
            return False, "设备上的登录态比快照新，已保留设备数据"

        data = self.store.load_bytes(plat)
        if not data:
            return False, "快照文件为空"

        # 停 App，避免边写边读
        dev.app_stop(self.package)

        push_ok = False
        try:
            from .device import _run
            import subprocess
            kwargs = {"capture_output": True, "timeout": 180}
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000
            proc = subprocess.run(
                [dev.adb_path, "-s", dev.serial, "push", "-"],
                input=data, **kwargs)
            push_ok = proc.returncode == 0
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"

        if not push_ok:
            # push 不支持 stdin 时退化为本地文件 + adb push
            import tempfile
            tmp = os.path.join(tempfile.gettempdir(), "tm_restore.tgz")
            with open(tmp, "wb") as f:
                f.write(data)
            try:
                from .device import _run
                p2 = _run([dev.adb_path, "-s", dev.serial, "push",
                           tmp, DEVICE_TMP], 180)
                push_ok = bool(p2 and p2.returncode == 0)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        if not push_ok:
            return False, self.last_error or "推送快照失败"

        base = f"/data/data/{self.package}"
        members = " ".join(self.paths)
        out = dev._sh(
            "shell",
            f"cd {base} && tar xzf {DEVICE_TMP} 2>&1; "
            f"rm -f {DEVICE_TMP}; echo DONE",
            timeout=180) or ""
        if "DONE" not in out:
            self.last_error = out.strip()[:200]
            return False, self.last_error or "解包失败"
        dev.app_start(self.package)
        return True, "已恢复快照"
