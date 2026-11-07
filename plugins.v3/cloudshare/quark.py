# SPDX-License-Identifier: GPL-3.0-only
"""Authenticated Quark operations for the CloudShare worker."""

import json
from pathlib import PurePosixPath
from http.cookies import CookieError, SimpleCookie
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.4-b478491100 "
    "Safari/537.36 Channel/pckk_other_ch"
)


class QuarkError(Exception):
    """Provider error without credentials or request URLs."""

    def __init__(self, operation, status=None):
        super().__init__(f"Quark {operation} failed" + (f" ({status})" if status else ""))
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Quark:
    def __init__(self, cookie, user_agent=DESKTOP_USER_AGENT):
        self._cookies = SimpleCookie()
        try:
            if any(c in cookie + user_agent for c in "\r\n"):
                raise CookieError()
            self._cookies.load(cookie)
        except CookieError:
            raise QuarkError("credentials") from None
        if not self._cookies:
            raise QuarkError("credentials")
        self._user_agent = user_agent
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def _open(self, url, *, body=None, headers=None):
        request = Request(url, data=body, headers={
            "Cookie": "; ".join(f"{key}={value.coded_value}" for key, value in self._cookies.items()),
            "Referer": "https://pan.quark.cn/",
            "User-Agent": self._user_agent,
            **(headers or {}),
        })
        try:
            response = self._opener.open(request, timeout=20)
        except HTTPError as error:
            status = error.code
            error.close()
            raise QuarkError("request", status) from None
        except (OSError, ValueError):
            raise QuarkError("connection") from None
        for header in response.headers.get_all("Set-Cookie", []):
            try:
                self._cookies.load(header)
            except CookieError:
                continue
        return response

    def _api(self, path, *, params=None, body=None, mobile=False):
        host = "drive-m.quark.cn" if mobile else "drive-pc.quark.cn"
        query = urlencode({"pr": "ucpro", "fr": "pc", "uc_param_str": "", **(params or {})})
        try:
            with self._open(f"https://{host}/1/clouddrive/{path}?{query}",
                            body=json.dumps(body).encode() if body is not None else None,
                            headers={"Content-Type": "application/json"}) as response:
                result = json.load(response)
        except (ValueError, OSError):
            raise QuarkError(path) from None
        if not isinstance(result, dict) or result.get("code") != 0:
            raise QuarkError(path, result.get("status") if isinstance(result, dict)
                             and type(result.get("status")) is int else None)
        return result.get("data")

    def available_bytes(self):
        data = self._api("capacity/growth/info", mobile=True)
        for row in (data, data.get("capacity_info") if isinstance(data, dict) else None):
            if isinstance(row, dict):
                total = row.get("total_capacity")
                used = row.get("used_capacity", row.get("use_capacity"))
                if type(total) is int and type(used) is int and 0 <= used <= total:
                    return total - used
        return None

    def create_folder(self, path):
        data = self._api("file", body={"pdir_fid": "0", "file_name": "",
                                      "dir_path": path, "dir_init_lock": False})
        return data["fid"]

    def children(self, folder_id):
        data = self._api("file/sort", params={"pdir_fid": folder_id, "_page": 1, "_size": 100})
        return data["list"]

    def prepare_save(self, candidate, file, folder_id):
        access = self._api("share/sharepage/token", body={
            "pwd_id": candidate.share_id, "passcode": candidate.passcode,
        })
        stoken = access["stoken"]
        token = self._file_token(candidate.share_id, stoken, file)
        return {
            "fid_list": [file.file_id], "fid_token_list": [token],
            "to_pdir_fid": folder_id, "pwd_id": candidate.share_id, "stoken": stoken,
            "pdir_fid": file.parent_id, "scene": "link",
        }

    def save(self, request):
        data = self._api("share/sharepage/save", params={"app": "clouddrive"}, body=request)
        return data.get("task_id")

    def _file_token(self, share_id, stoken, file):
        for page in range(1, 21):
            data = self._api("share/sharepage/detail", params={
                "pwd_id": share_id, "stoken": stoken, "pdir_fid": file.parent_id,
                "_page": page, "_size": 100, "_fetch_total": 1,
            })
            rows = data["list"]
            for row in rows:
                if row.get("fid") != file.file_id:
                    continue
                token = row.get("share_fid_token")
                if (row.get("dir") or row.get("size") != file.size
                        or row.get("file_name") != PurePosixPath(file.path).name
                        or not isinstance(token, str) or not token):
                    raise QuarkError("share file changed")
                return token
            if len(rows) < 100:
                break
        raise QuarkError("share file missing")

    def wait(self, task_id):
        deadline = time.monotonic() + 60
        index = 0
        while time.monotonic() < deadline:
            data = self._api("task", params={"task_id": task_id, "retry_index": index})
            if data.get("status") == 2:
                return
            if data.get("status") in (-1, 3):
                break
            index += 1
            time.sleep(2)
        raise QuarkError("task completion")

    def stream(self, file_id, offset):
        rows = self._api("file/download", body={"fids": [file_id]})
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise QuarkError("download link")
        url = rows[0].get("download_url")
        try:
            parts = urlsplit(url)
            allowed = (parts.scheme == "https" and (parts.hostname or "").endswith(".quark.cn")
                       and not parts.username and not parts.password and not parts.fragment
                       and parts.port in (443, None))
        except (ValueError, TypeError):
            allowed = False
        if not allowed:
            raise QuarkError("download destination")
        return self._open(url, headers={"Range": f"bytes={offset}-", "Accept-Encoding": "identity"})

    def recycle(self, file_id):
        data = self._api("file/delete", body={
            "action_type": 2, "filelist": [file_id], "exclude_fids": [],
        })
        if data and data.get("task_id"):
            self.wait(data["task_id"])
