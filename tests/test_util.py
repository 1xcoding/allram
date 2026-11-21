from __future__ import annotations

import os

import pytest

from allram import util


@pytest.mark.parametrize(
    "text,expected",
    [
        ("32", 32 * util.GIB),
        ("32G", 32 * util.GIB),
        ("512M", 512 * util.MIB),
        ("1.5T", int(1.5 * util.TIB)),
        ("2048mb", 2 * util.GIB),
        ("1024K", util.MIB),
        ("4096b", 4096),
        ("16 гб", 16 * util.GIB),
    ],
)
def test_parse_size(text, expected):
    assert util.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "12X", "-5G", "0", "1.2.3G"])
def test_parse_size_invalid(text):
    with pytest.raises(ValueError):
        util.parse_size(text)


def test_human_bytes():
    assert util.human_bytes(1536) == "1.5 КиБ"
    assert util.human_bytes(2 * util.GIB) == "2.0 ГиБ"
    assert util.human_bytes(None) == "—"


def test_round_helpers():
    assert util.round_up_to_gib(util.GIB + 1) == 2 * util.GIB
    assert util.round_down_to_mib(util.MIB + 12345) == util.MIB
    assert util.round_down_to_mib(100) == util.MIB


def test_normalize_drive_letter():
    assert util.normalize_drive_letter("d") == "D"
    assert util.normalize_drive_letter("D:") == "D"
    assert util.normalize_drive_letter("E:\\") == "E"
    with pytest.raises(ValueError):
        util.normalize_drive_letter("DD")
    with pytest.raises(ValueError):
        util.normalize_drive_letter("1")


def test_drive_root():
    root = util.drive_root("/home/user/x")
    if os.name == "nt":
        assert root.endswith(":\\")
    else:
        assert root == "/"


def test_format_seconds():
    assert util.format_seconds(45) == "45 с"
    assert util.format_seconds(125) == "2 мин 5 с"
    assert util.format_seconds(3700) == "1 ч 1 мин"
