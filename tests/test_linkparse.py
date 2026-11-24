"""链接识别测试。"""

from __future__ import annotations

from app.services.linkparse import extract_passcode, looks_like_share_text, parse_links


def test_quark_link_with_pwd():
    links = parse_links("看看这个 https://pan.quark.cn/s/abc123DEF?pwd=k9m2 提取码:k9m2")
    assert len(links) == 1
    assert links[0].drive == "quark"
    assert links[0].passcode == "k9m2"
    assert links[0].share_id == "abc123DEF"


def test_baidu_link_chinese_passcode():
    text = "【课程】链接：https://pan.baidu.com/s/1AbCd_-xyz 提取码：8h2k"
    links = parse_links(text)
    assert [l.drive for l in links] == ["baidu"]
    assert links[0].passcode == "8h2k"
    assert links[0].share_id == "1AbCd_-xyz"


def test_115_and_xunlei():
    text = "https://115.com/s/sw123abcd?password=x9y8 和 https://pan.xunlei.com/s/VN_abc-123"
    links = parse_links(text)
    assert {l.drive for l in links} == {"115", "xunlei"}


def test_multi_links_order_and_limit():
    text = (
        "https://pan.quark.cn/s/first 然后 https://pan.baidu.com/s/1second "
        "再 https://115.com/s/third 最后 https://pan.xunlei.com/s/fourth"
    )
    links = parse_links(text, limit=3)
    assert len(links) == 3
    assert links[0].drive == "quark"
    assert links[1].drive == "baidu"
    assert links[2].drive == "115"


def test_no_duplicate_links():
    text = "https://pan.quark.cn/s/same https://pan.quark.cn/s/same"
    assert len(parse_links(text)) == 1


def test_trailing_punctuation_trimmed():
    links = parse_links("分享给你：https://pan.quark.cn/s/abc，快看")
    assert links[0].url == "https://pan.quark.cn/s/abc"


def test_plain_text_ignored():
    assert parse_links("今天天气不错") == []
    assert looks_like_share_text("https://example.com/s/abc") is False
    assert extract_passcode("随便说说") == ""
