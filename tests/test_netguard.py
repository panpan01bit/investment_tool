"""netguard：出站 URL 安全校验。"""

from __future__ import annotations

import pytest

from investlab.netguard import UnsafeURLError, is_safe_url, validate_url


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1/x",
    "https://localhost/api",
    "file:///etc/passwd",
    "ftp://example.com",
    "http://192.168.1.10/admin",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://169.254.169.254/latest/meta-data",  # 云元数据
    "http://[::1]/",
    "http://0.0.0.0/",
])
def test_rejects_unsafe(bad):
    with pytest.raises(UnsafeURLError):
        validate_url(bad)


@pytest.mark.parametrize("good", [
    "https://api.tushare.pro",
    "https://push2.eastmoney.com/api/qt/stock/get",
    "http://api.moonshot.cn/v1/chat/completions",
])
def test_allows_public_https_http(good):
    assert validate_url(good) == good


def test_is_safe_url_bool():
    assert is_safe_url("https://tushare.pro")
    assert not is_safe_url("gopher://x")


def test_empty_and_scheme():
    with pytest.raises(UnsafeURLError):
        validate_url("")
    with pytest.raises(UnsafeURLError):
        validate_url("not-a-url")
