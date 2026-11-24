"""四家网盘适配器的请求/解析测试（httpx.MockTransport，全程不联网）。

这些用例覆盖的是真正容易出错的地方：参数怎么拼、返回怎么解析、错误怎么翻译成中文提示。
"""

from __future__ import annotations

import json

import httpx

from app.drives.base import DriveError
from app.drives.baidu import BaiduDrive
from app.drives.pan115 import Pan115Drive
import pytest

from app.drives.quark import QuarkDrive
from app.drives.xunlei import XunleiDrive

# --------------------------------------------------------------------------- 夸克


async def test_quark_save_share_success(stub_http):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/share/sharepage/token"):
            seen["token_body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"stoken": "ST-1"}})
        if path.endswith("/share/sharepage/detail"):
            seen["detail_query"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "code": 0,
                    "data": {"list": [{"fid": "f1", "share_fid_token": "t1"}, {"fid": "f2", "share_fid_token": "t2"}]},
                },
            )
        if path.endswith("/share/sharepage/save"):
            seen["save_body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"task_id": "TASK-1"}})
        if path.endswith("/task"):
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"status": 2}})
        return httpx.Response(404)

    stub_http(handler)
    drive = QuarkDrive(cookie={"__puus": "puus-token"})
    result = await drive.save_share("https://pan.quark.cn/s/abc123", "k9m2", "dir-9")

    assert result.ok is True
    assert result.files == 2
    assert seen["token_body"] == {"pwd_id": "abc123", "passcode": "k9m2"}
    assert seen["save_body"]["to_pdir_fid"] == "dir-9"
    assert seen["save_body"]["fid_list"] == ["f1", "f2"]
    assert seen["save_body"]["fid_token_list"] == ["t1", "t2"]


async def test_quark_save_share_reports_api_error(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 400, "code": 41004, "message": "分享已失效"})

    stub_http(handler)
    drive = QuarkDrive(cookie={"__puus": "puus-token"})
    result = await drive.save_share("https://pan.quark.cn/s/abc123", "", "0")

    assert result.ok is False
    assert "分享已失效" in result.message


async def test_quark_task_failure_is_not_reported_as_success(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/share/sharepage/token"):
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"stoken": "ST"}})
        if path.endswith("/share/sharepage/detail"):
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"list": [{"fid": "f1", "share_fid_token": "t1"}]}})
        if path.endswith("/share/sharepage/save"):
            return httpx.Response(200, json={"status": 200, "code": 0, "data": {"task_id": "T"}})
        return httpx.Response(200, json={"status": 200, "code": 0, "data": {"status": 3, "message": "空间不足"}})

    stub_http(handler)
    result = await QuarkDrive(cookie={"__puus": "x"}).save_share("https://pan.quark.cn/s/a", "", "0")
    assert result.ok is False
    assert "空间不足" in result.message


async def test_quark_list_dirs_filters_files(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["pdir_fid"] == "0"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "code": 0,
                "data": {
                    "list": [
                        {"fid": "d1", "file_name": "电影", "file_type": 0},
                        {"fid": "f1", "file_name": "a.mkv", "file_type": 1},
                    ]
                },
            },
        )

    stub_http(handler)
    dirs = await QuarkDrive(cookie={"__puus": "x"}).list_dirs("0")
    assert [(d.id, d.name) for d in dirs] == [("d1", "电影")]


async def test_quark_requires_cookie():
    try:
        await QuarkDrive(cookie={}).list_dirs("0")
    except DriveError as exc:
        assert "__puus" in exc.message
    else:  # pragma: no cover
        raise AssertionError("缺少 Cookie 时应当报错")


# --------------------------------------------------------------------------- 115


async def test_115_list_dirs_distinguishes_folder_and_file(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            assert request.url.params["cid"] == "0"
            return httpx.Response(
                200,
                json={
                    "state": True,
                    "data": [
                        {"cid": 111, "n": "电影"},                      # 目录：无 fid
                        {"cid": 0, "fid": 222, "n": "a.mkv"},            # 文件：有 fid
                    ],
                },
            )
        return httpx.Response(404)

    stub_http(handler)
    dirs = await Pan115Drive(cookie={"UID": "u", "CID": "c", "SEID": "s"}).list_dirs("0")
    assert [(d.id, d.name) for d in dirs] == [("111", "电影")]


async def test_115_save_share_success(stub_http):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/share/snap"):
            seen["snap"] = dict(request.url.params) if request.url.params else None
            seen["snap_body"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"state": True, "data": {"share_id": "SID", "count": 5}})
        if path.endswith("/share/receive"):
            seen["receive_body"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"state": True})
        return httpx.Response(404)

    stub_http(handler)
    drive = Pan115Drive(cookie={"UID": "u", "CID": "c", "SEID": "s"})
    result = await drive.save_share("https://115.com/s/sw123abcd?password=x9y8", "x9y8", "999")

    assert result.ok is True
    assert result.files == 5
    assert seen["snap_body"]["share_code"] == "sw123abcd"
    assert seen["snap_body"]["receive_code"] == "x9y8"
    assert seen["snap_body"]["cid"] == "999"
    assert seen["receive_body"]["share_id"] == "SID"


async def test_115_save_share_wrong_passcode(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": False, "error": "提取码错误"})

    stub_http(handler)
    result = await Pan115Drive(cookie={"UID": "u", "CID": "c", "SEID": "s"}).save_share(
        "https://115.com/s/sw123abcd", "bad", "0"
    )
    assert result.ok is False
    assert "提取码错误" in result.message


async def test_115_rejects_bad_share_url(stub_http):
    stub_http(lambda request: httpx.Response(500))
    drive = Pan115Drive(cookie={"UID": "u", "CID": "c", "SEID": "s"})
    try:
        await drive.save_share("https://example.com/s/abc", "", "0")
    except DriveError as exc:
        assert "分享码" in exc.message
    else:  # pragma: no cover
        raise AssertionError("应当拒绝非 115 链接")


# --------------------------------------------------------------------------- 百度

YUNDATA_HTML = """
<html><script>window.yunData = {}; yunData.setData({"shareid":123456,"share_uk":"98765",
"file_list":[{"fs_id":111,"server_filename":"a.mkv"},{"fs_id":222,"server_filename":"b.mkv"}]});</script></html>
"""


async def test_baidu_save_share_success(stub_http):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/gettemplatevariable"):
            return httpx.Response(200, json={"result": {"bdstoken": "BDT", "uk": "98765"}})
        if path.endswith("/share/verify"):
            seen["verify_body"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"errno": 0, "randsk": "SEKEY+/="})
        if path.startswith("/s/"):
            return httpx.Response(200, text=YUNDATA_HTML)
        if path.endswith("/share/transfer"):
            seen["transfer_query"] = dict(request.url.params)
            seen["transfer_body"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"errno": 0})
        return httpx.Response(404)

    stub_http(handler)
    drive = BaiduDrive(cookie={"BDUSS": "bduss", "STOKEN": "stoken"})
    result = await drive.save_share("https://pan.baidu.com/s/1AbCd_-xyz", "8h2k", "电影")

    assert result.ok is True
    assert result.files == 2
    assert seen["verify_body"]["pwd"] == "8h2k"
    assert seen["transfer_query"]["shareid"] == "123456"
    assert seen["transfer_query"]["from"] == "98765"
    assert json.loads(seen["transfer_body"]["fsidlist"]) == ["111", "222"]
    assert seen["transfer_query"]["sekey"] == "SEKEY+/="  # 只编码一次，不能被二次转义
    assert seen["transfer_body"]["path"] == "/电影"  # 目录 ID 映射成绝对路径


async def test_baidu_wrong_passcode_message(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/gettemplatevariable"):
            return httpx.Response(200, json={"result": {"bdstoken": "BDT", "uk": "1"}})
        return httpx.Response(200, json={"errno": -9})

    stub_http(handler)
    result = await BaiduDrive(cookie={"BDUSS": "b"}).save_share("https://pan.baidu.com/s/1xyz", "bad", "0")
    assert result.ok is False
    assert "提取码错误" in result.message


async def test_baidu_list_dirs_returns_paths(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/gettemplatevariable"):
            return httpx.Response(200, json={"result": {"bdstoken": "BDT", "uk": "1"}})
        assert request.url.params["dir"] == "/电影"
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "list": [
                    {"isdir": 1, "path": "/电影/动作", "server_filename": "动作"},
                    {"isdir": 0, "server_filename": "x.mkv"},
                ],
            },
        )

    stub_http(handler)
    dirs = await BaiduDrive(cookie={"BDUSS": "b"}).list_dirs("电影")
    assert [(d.id, d.name) for d in dirs] == [("/电影/动作", "动作")]


async def test_baidu_root_dir_mapping():
    assert BaiduDrive._to_path("0") == "/"
    assert BaiduDrive._to_path("/电影") == "/电影"
    assert BaiduDrive._to_path("电影") == "/电影"


# --------------------------------------------------------------------------- 迅雷


async def test_xunlei_qr_reports_unsupported(stub_http):
    """device_code 接口已 404，扫码不再假装能用，而是明确提示改用 token 导入。"""
    from app.drives.base import NotSupported

    stub_http(lambda request: httpx.Response(404, json={"error": "not_found"}))
    with pytest.raises(NotSupported) as exc:
        await XunleiDrive().qr_start()
    assert "access_token" in str(exc.value)


async def test_quark_qr_reports_unsupported(stub_http):
    from app.drives.base import NotSupported

    stub_http(lambda request: httpx.Response(404))
    with pytest.raises(NotSupported) as exc:
        await QuarkDrive(cookie={"__puus": "x"}).qr_start()
    assert "Cookie" in str(exc.value)


async def test_xunlei_save_share_success(stub_http):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/share"):
            seen["share_params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "share": {"share_id": "S1", "pass_code_token": "PCT"},
                    "files": [{"id": "f1"}, {"id": "f2"}],
                },
            )
        if path.endswith("/share/restore"):
            seen["restore_body"] = json.loads(request.content)
            return httpx.Response(200, json={"restore_task_id": "RT1"})
        return httpx.Response(404)

    stub_http(handler)
    drive = XunleiDrive(extra={"access_token": "AT", "expires_at": 9999999999, "device_id": "did"})
    result = await drive.save_share("https://pan.xunlei.com/s/VN_abc", "abcd", "parent-1")

    assert result.ok is True
    assert result.files == 2
    assert seen["share_params"]["pass_code"] == "abcd"
    assert seen["restore_body"]["parent_id"] == "parent-1"
    assert seen["restore_body"]["pass_code_token"] == "PCT"
    assert seen["restore_body"]["file_ids"] == ["f1", "f2"]


async def test_xunlei_refreshes_expired_token(stub_http):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/credential/token"):
            calls.append("refresh")
            return httpx.Response(200, json={"access_token": "NEW-AT", "refresh_token": "NEW-RT", "expires_in": 3600})
        calls.append("api")
        assert request.headers["authorization"] == "Bearer NEW-AT"  # 已用新 token
        return httpx.Response(200, json={"username": "雷友", "files": []})

    stub_http(handler)
    drive = XunleiDrive(
        extra={"access_token": "OLD-AT", "refresh_token": "RT", "expires_at": 1, "device_id": "did"}
    )
    info = await drive.account_info()

    assert "雷友" in info["name"]
    assert calls[0] == "refresh"
    assert drive.extra["access_token"] == "NEW-AT"


async def test_xunlei_list_dirs_only_folders(stub_http):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {"id": "d1", "name": "电影", "kind": {"#type": "folder"}},
                    {"id": "f1", "name": "a.mkv", "kind": {"#type": "file"}},
                ]
            },
        )

    stub_http(handler)
    dirs = await XunleiDrive(extra={"access_token": "AT", "expires_at": 9999999999}).list_dirs("0")
    assert [(d.id, d.name) for d in dirs] == [("d1", "电影")]


async def test_xunlei_without_token_raises():
    try:
        await XunleiDrive().list_dirs("0")
    except DriveError as exc:
        assert "扫码" in exc.message
    else:  # pragma: no cover
        raise AssertionError("未授权时应报错")


async def test_115_qr_start_returns_scan_content(stub_http):
    """115 的 qrcode 字段是扫码内容（https://115.com/scan/...），不是图片地址。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "state": 1,
                "code": 0,
                "data": {"uid": "UID", "time": 1795535650, "sign": "SIGN", "qrcode": "https://115.com/scan/dg-abc"},
            },
        )

    stub_http(handler)
    start = await Pan115Drive().qr_start()
    assert start.mode == "content"
    assert start.content == "https://115.com/scan/dg-abc"
    assert start.image_url == ""
    assert start.state["uid"] == "UID"


async def test_baidu_qr_start_adds_scheme(stub_http):
    """百度返回的 imgurl 没有 scheme，必须补 https://，否则 <img> 是相对路径。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errno": 0, "sign": "SIGN", "imgurl": "passport.baidu.com/v2/api/qrcode?sign=SIGN"},
        )

    stub_http(handler)
    start = await BaiduDrive().qr_start()
    assert start.mode == "image"
    assert start.image_url == "https://passport.baidu.com/v2/api/qrcode?sign=SIGN"
