"""Tests for parsing ANSI SGR sequences into styled spans."""
from cola import ansi


def test_plain_text_is_a_single_default_span():
    spans = ansi.parse_ansi('hello world')
    assert len(spans) == 1
    assert spans[0].text == 'hello world'
    assert spans[0].style == ansi._DEFAULT_STYLE


def test_basic_foreground_color():
    # ESC[31m red ESC[0m
    spans = ansi.parse_ansi('\x1b[31mred\x1b[0m')
    assert len(spans) == 1
    assert spans[0].text == 'red'
    assert spans[0].style.foreground == ansi._BASE_COLORS[1]


def test_bold_and_reset():
    spans = ansi.parse_ansi('\x1b[1mbold\x1b[0mplain')
    assert [s.text for s in spans] == ['bold', 'plain']
    assert spans[0].style.bold is True
    assert spans[1].style.bold is False


def test_bright_foreground():
    spans = ansi.parse_ansi('\x1b[92mbright green\x1b[0m')
    assert spans[0].style.foreground == ansi._BRIGHT_COLORS[2]


def test_background_color():
    spans = ansi.parse_ansi('\x1b[41mon red\x1b[0m')
    assert spans[0].style.background == ansi._BASE_COLORS[1]


def test_256_color():
    # 38;5;46 is a bright green in the colour cube.
    spans = ansi.parse_ansi('\x1b[38;5;46mx\x1b[0m')
    assert spans[0].style.foreground == ansi._xterm_256_color(46)


def test_24bit_truecolor():
    spans = ansi.parse_ansi('\x1b[38;2;10;20;30mx\x1b[0m')
    assert spans[0].style.foreground == (10, 20, 30)


def test_italic_and_underline():
    spans = ansi.parse_ansi('\x1b[3;4mfancy\x1b[0m')
    assert spans[0].style.italic is True
    assert spans[0].style.underline is True


def test_colon_subparameter_separator():
    # Some tools emit 38:5:n instead of 38;5;n.
    spans = ansi.parse_ansi('\x1b[38:5:46mx\x1b[0m')
    assert spans[0].style.foreground == ansi._xterm_256_color(46)


def test_non_sgr_csi_is_stripped():
    # A cursor-move sequence (ESC[2J) is discarded but text is kept.
    spans = ansi.parse_ansi('a\x1b[2Jb')
    assert ''.join(s.text for s in spans) == 'ab'


def test_adjacent_same_style_spans_are_coalesced():
    spans = ansi.parse_ansi('\x1b[31mfoo\x1b[31mbar\x1b[0m')
    assert len(spans) == 1
    assert spans[0].text == 'foobar'


def test_strip_ansi_matches_reconstruction():
    text = '\x1b[1m\x1b[31mred bold\x1b[0m normal \x1b[32mgreen\x1b[0m'
    spans = ansi.parse_ansi(text)
    reconstruction = ''.join(s.text for s in spans)
    assert reconstruction == ansi.strip_ansi(text)
    assert reconstruction == 'red bold normal green'


def test_has_ansi():
    assert ansi.has_ansi('\x1b[31mx\x1b[0m') is True
    assert ansi.has_ansi('plain text') is False
