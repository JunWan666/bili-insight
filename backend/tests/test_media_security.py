from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from app.media.security import (
    MediaURLValidator,
    UnsafeMediaURLError,
    render_filename_template,
    safe_child_path,
    sanitize_filename,
)


async def public_resolver(host: str, port: int) -> Iterable[str]:
    assert host
    assert port in {443, 4483, 8082}
    return ("93.184.216.34", "2001:4860:4860::8888")


async def test_media_validator_pins_public_addresses_and_preserves_sni() -> None:
    validator = MediaURLValidator(("bilivideo.com", "bilivideo.cn"), resolver=public_resolver)
    target = await validator.resolve("https://cdn.bilivideo.com/video.m4s?token=redacted")

    assert target.host == "cdn.bilivideo.com"
    assert target.port == 443
    assert target.host_header == "cdn.bilivideo.com"
    assert target.addresses == ("93.184.216.34", "2001:4860:4860::8888")
    assert target.pinned_url("93.184.216.34").startswith("https://93.184.216.34/")
    assert target.pinned_url("2001:4860:4860::8888").startswith("https://[2001:4860:4860::8888]/")
    assert await validator.validate(target.url) == target.url


async def test_mcdn_8082_is_narrowly_allowed_and_uses_actual_port() -> None:
    ports: list[int] = []

    async def resolver(_host: str, port: int) -> Iterable[str]:
        ports.append(port)
        return ("93.184.216.34",)

    validator = MediaURLValidator(("bilivideo.cn",), resolver=resolver)
    target = await validator.resolve("https://mcdn.bilivideo.cn:8082/a.m4s")
    assert target.host_header == "mcdn.bilivideo.cn:8082"
    assert target.pinned_url(target.addresses[0]) == "https://93.184.216.34:8082/a.m4s"
    assert ports == [8082]

    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://cdn.bilivideo.cn:8082/a.m4s")
    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://mcdn.bilivideo.cn.evil.example:8082/a.m4s")


async def test_pgc_cdn_4483_is_narrowly_allowed_and_uses_actual_port() -> None:
    ports: list[int] = []

    async def resolver(_host: str, port: int) -> Iterable[str]:
        ports.append(port)
        return ("93.184.216.34",)

    validator = MediaURLValidator(("edge.mountaintoys.cn",), resolver=resolver)
    target = await validator.resolve("https://vu5bt87a.edge.mountaintoys.cn:4483/a.m4s")
    assert target.host_header == "vu5bt87a.edge.mountaintoys.cn:4483"
    assert target.pinned_url(target.addresses[0]) == "https://93.184.216.34:4483/a.m4s"
    assert ports == [4483]

    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://unrelated.mountaintoys.cn:4483/a.m4s")
    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://edge.mountaintoys.cn.evil.example:4483/a.m4s")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://cdn.bilivideo.com/a",
        "file:///etc/passwd",
        "https://cdn.bilivideo.com/a#fragment",
        "https://user:secret@cdn.bilivideo.com/a",
        "https://evil.example/a",
        "https://cdn.bilivideo.com:0/a",
        "https://cdn.bilivideo.com:444/a",
        "https://cdn.bilivideo.com:65536/a",
        "https://cdn.bilivideo.com:invalid/a",
        "https://[::1",
        "https://cdn.bilivideo.com//%2fetc/passwd",
        "https://cdn.bilivideo.com/a\x00b",
    ],
)
async def test_media_validator_rejects_malformed_targets(url: str) -> None:
    validator = MediaURLValidator(("bilivideo.com",), resolver=public_resolver)
    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve(url)


@pytest.mark.parametrize(
    "answers",
    [
        (),
        ("127.0.0.1",),
        ("93.184.216.34", "10.0.0.1"),
        ("fec0::1",),
        ("ff02::1",),
        ("64:ff9b::c0a8:101",),
        ("::ffff:192.168.1.1",),
        ("2002:c0a8:0101::",),
        ("not-an-ip",),
    ],
)
async def test_media_validator_rejects_unsafe_dns_answers(
    answers: tuple[str, ...],
) -> None:
    async def resolver(_host: str, _port: int) -> Iterable[str]:
        return answers

    validator = MediaURLValidator(("bilivideo.com",), resolver=resolver)
    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://cdn.bilivideo.com/a")


async def test_media_validator_maps_dns_errors_and_timeouts() -> None:
    async def failed(_host: str, _port: int) -> Iterable[str]:
        raise OSError("fixed DNS failure")

    validator = MediaURLValidator(("bilivideo.com",), resolver=failed)
    with pytest.raises(UnsafeMediaURLError):
        await validator.resolve("https://cdn.bilivideo.com/a")


@pytest.mark.parametrize("suffix", ["", ".", "localhost", "bad suffix", "-bad.example"])
def test_media_validator_rejects_invalid_configuration(suffix: str) -> None:
    with pytest.raises(ValueError):
        MediaURLValidator((suffix,))


def test_filename_sanitization_is_portable_and_byte_bounded() -> None:
    traversal = sanitize_filename("../../CON.txt")
    assert "/" not in traversal and "\\" not in traversal and traversal.endswith("CON.txt")
    assert sanitize_filename("aux") == "_aux"
    assert sanitize_filename("  a\t b?.mp4  ") == "a_ b_.mp4"
    assert sanitize_filename("..", fallback="fallback") == "fallback"
    assert "\u202e" not in sanitize_filename("safe\u202ecod.exe")

    chinese = sanitize_filename("测" * 180 + ".mp4")
    emoji = sanitize_filename("😀" * 180 + ".mkv")
    assert len(chinese.encode("utf-8")) <= 240
    assert len(emoji.encode("utf-8")) <= 240
    assert chinese.endswith(".mp4")
    assert emoji.endswith(".mkv")


def test_filename_template_expands_only_supported_fields() -> None:
    rendered = render_filename_template(
        "{title} - {bvid} - P{page} - {part} - {quality}",
        {
            "title": "标题/含路径",
            "bvid": "BV123",
            "page": 2,
            "part": "CON",
            "quality": "1080P+",
        },
        extension="mp4",
    )
    assert rendered.endswith(".mp4")
    assert "/" not in rendered
    assert "BV123" in rendered
    assert render_filename_template("{{title}}-{title}", {"title": "ok"}, extension="mkv") == (
        "{title}-ok.mkv"
    )


@pytest.mark.parametrize(
    ("template", "extension"),
    [
        ("", "mp4"),
        ("{unknown}", "mp4"),
        ("{title!r}", "mp4"),
        ("{title:20}", "mp4"),
        ("{title", "mp4"),
        ("../{title}", "mp4"),
        ("{title}", "m.p4"),
    ],
)
def test_filename_template_rejects_unsafe_forms(template: str, extension: str) -> None:
    with pytest.raises(ValueError):
        render_filename_template(template, {"title": "safe"}, extension=extension)


def test_safe_child_path_enforces_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    assert safe_child_path(root, "job", "file.mp4") == (root / "job" / "file.mp4").resolve()
    with pytest.raises(ValueError):
        safe_child_path(root, "..", "escape")
