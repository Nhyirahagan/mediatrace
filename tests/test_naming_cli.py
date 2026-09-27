from __future__ import annotations

import json

import pytest

from mediatrace import render_template, sanitize_filename
from mediatrace.cli import main
from mediatrace.exceptions import TemplateError
from mediatrace.naming import parse_size, split_name, unique_path

from .conftest import PDF, PNG


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('a<b>c:"d"/e\\f|g?h*i', "a_b_c_d_e_f_g_h_i"),
        ("  lots   of   space  ", "lots of space"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("...", "untitled"),
        ("trailing dots...", "trailing dots"),
        ("zero​width‮flip", "zerowidthflip"),
        ("", "untitled"),
    ],
)
def test_sanitize(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_ascii_and_length():
    assert sanitize_filename("Café Déjà Vu", ascii_only=True) == "Cafe Deja Vu"
    long = sanitize_filename("x" * 300 + ".jpeg", max_length=50)
    assert len(long) == 50 and long.endswith(".jpeg")


def test_render_template():
    values = {"a": "one/two", "b": None, "n": 3, "t": "Title"}
    assert render_template("{a}/{b}/{n:03d}_{t:.2}", values) == "one-two/unknown/003_Ti"
    with pytest.raises(TemplateError):
        render_template("{zzz}", values)
    with pytest.raises(TemplateError):
        render_template("../{a}", values)
    assert render_template("{a}", {"a": ".."}) == "unknown"


def test_split_and_unique(tmp_path):
    assert split_name("a.tar.gz") == ("a", ".tar.gz")
    assert split_name(".bashrc") == (".bashrc", "")
    (tmp_path / "a.tar.gz").write_bytes(b"")
    assert unique_path(tmp_path / "a.tar.gz").name == "a (1).tar.gz"


@pytest.mark.parametrize(("raw", "expected"), [("10", 10), ("1k", 1024), ("1.5MB", 1572864), ("2GiB", 2 * 1024**3)])
def test_parse_size(raw, expected):
    assert parse_size(raw) == expected


# ----------------------------------------------------------------------- cli


def test_cli_detect(tmp_path, make_file, capsys):
    make_file("a.jpg", PNG)
    assert main(["detect", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["mime_type"] == "image/png" and data[0]["extension_mismatch"] is True


def test_cli_organize_and_undo(tmp_path, make_file, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    make_file("b.pdf", PDF, directory=src)
    assert main(["organize", str(src), "-t", str(out), "--dry-run"]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert (src / "a.png").exists()

    assert main(["organize", str(src), "-t", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (out / "Images" / "a.png").exists()
    assert main(["undo", data["summary"]["journal"]]) == 0
    assert (src / "a.png").exists()


def test_cli_clean_url(capsys):
    assert main(["clean-url", "https://youtu.be/x?si=abc"]) == 0
    assert capsys.readouterr().out.strip() == "https://youtu.be/x\tyoutube"
    assert main(["clean-url", "ftp://nope"]) == 1
