"""Parse a subset of ANSI SGR escape sequences into styled text spans.

This supports rendering the coloured output of external diff tools (e.g.
difftastic) in a Qt text widget. Only SGR (``ESC [ ... m``) sequences are
interpreted; other escape sequences are stripped. The parser is intentionally
small -- it covers the codes such tools emit: reset, bold, dim, italic,
underline, the 8 standard and 8 bright foreground/background colours, the
256-colour (``38;5;n`` / ``48;5;n``) and 24-bit (``38;2;r;g;b``) forms, and
default-colour resets.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# Matches a CSI sequence: ESC [ <params> <final-byte>. We act on 'm' (SGR) and
# drop the rest.
_CSI_RE = re.compile(r'\x1b\[([0-9;:]*)([A-Za-z])')

# The 8 standard ANSI colours as RGB, plus their bright variants. Chosen to read
# well on typical light and dark backgrounds.
_BASE_COLORS = [
    (0, 0, 0),  # 0 black
    (194, 54, 33),  # 1 red
    (37, 188, 36),  # 2 green
    (173, 173, 39),  # 3 yellow
    (73, 46, 225),  # 4 blue
    (211, 56, 211),  # 5 magenta
    (51, 187, 200),  # 6 cyan
    (203, 204, 205),  # 7 white
]
_BRIGHT_COLORS = [
    (129, 131, 131),  # 8 bright black (grey)
    (252, 57, 31),  # 9 bright red
    (49, 231, 34),  # 10 bright green
    (234, 236, 35),  # 11 bright yellow
    (88, 51, 255),  # 12 bright blue
    (249, 53, 248),  # 13 bright magenta
    (20, 240, 240),  # 14 bright cyan
    (233, 235, 235),  # 15 bright white
]


class Style(NamedTuple):
    """A resolved text style. Colours are (r, g, b) tuples or None for default."""

    foreground: tuple[int, int, int] | None
    background: tuple[int, int, int] | None
    bold: bool
    italic: bool
    underline: bool


class Span(NamedTuple):
    """A run of text sharing a single Style."""

    text: str
    style: Style


_DEFAULT_STYLE = Style(None, None, False, False, False)


def _xterm_256_color(index: int) -> tuple[int, int, int]:
    """Return the RGB for an xterm 256-colour palette index."""
    if index < 8:
        return _BASE_COLORS[index]
    if index < 16:
        return _BRIGHT_COLORS[index - 8]
    if index < 232:
        # 6x6x6 colour cube.
        index -= 16
        red = index // 36
        green = (index % 36) // 6
        blue = index % 6
        steps = [0, 95, 135, 175, 215, 255]
        return (steps[red], steps[green], steps[blue])
    # Greyscale ramp.
    value = 8 + (index - 232) * 10
    return (value, value, value)


def _apply_sgr(style: Style, params: list[int]) -> Style:
    """Return a new Style after applying one SGR parameter list."""
    fg = style.foreground
    bg = style.background
    bold = style.bold
    italic = style.italic
    underline = style.underline

    i = 0
    if not params:
        params = [0]
    while i < len(params):
        code = params[i]
        if code == 0:
            fg = bg = None
            bold = italic = underline = False
        elif code == 1:
            bold = True
        elif code in (2, 22):
            # 2 = dim, 22 = normal intensity. We do not dim; just clear bold.
            bold = False
        elif code == 3:
            italic = True
        elif code == 23:
            italic = False
        elif code == 4:
            underline = True
        elif code == 24:
            underline = False
        elif 30 <= code <= 37:
            fg = _BASE_COLORS[code - 30]
        elif code == 39:
            fg = None
        elif 40 <= code <= 47:
            bg = _BASE_COLORS[code - 40]
        elif code == 49:
            bg = None
        elif 90 <= code <= 97:
            fg = _BRIGHT_COLORS[code - 90]
        elif 100 <= code <= 107:
            bg = _BRIGHT_COLORS[code - 100]
        elif code in (38, 48):
            # Extended colour: 38;5;n / 48;5;n (256) or 38;2;r;g;b / 48;2 (rgb).
            target_is_fg = code == 38
            if i + 1 < len(params) and params[i + 1] == 5:
                if i + 2 < len(params):
                    color = _xterm_256_color(params[i + 2])
                    if target_is_fg:
                        fg = color
                    else:
                        bg = color
                i += 2
            elif i + 1 < len(params) and params[i + 1] == 2:
                if i + 4 < len(params):
                    color = (params[i + 2], params[i + 3], params[i + 4])
                    if target_is_fg:
                        fg = color
                    else:
                        bg = color
                i += 4
        i += 1

    return Style(fg, bg, bold, italic, underline)


def _parse_params(raw: str) -> list[int]:
    """Parse the numeric parameters of an SGR sequence."""
    params = []
    # Some tools use ':' as a sub-parameter separator (e.g. 38:5:n); treat it
    # like ';' for our purposes.
    for part in raw.replace(':', ';').split(';'):
        if part == '':
            params.append(0)
        else:
            try:
                params.append(int(part))
            except ValueError:
                params.append(0)
    return params


def parse_ansi(text: str) -> list[Span]:
    """Split ANSI-coloured text into a list of styled Spans.

    Non-SGR escape sequences are discarded. Adjacent text sharing a style is
    coalesced into a single span.
    """
    spans: list[Span] = []
    style = _DEFAULT_STYLE
    pos = 0
    pending = []

    def flush():
        if pending:
            chunk = ''.join(pending)
            if spans and spans[-1].style == style:
                spans[-1] = Span(spans[-1].text + chunk, style)
            else:
                spans.append(Span(chunk, style))
            pending.clear()

    for match in _CSI_RE.finditer(text):
        if match.start() > pos:
            pending.append(text[pos : match.start()])
        final = match.group(2)
        if final == 'm':
            flush()
            style = _apply_sgr(style, _parse_params(match.group(1)))
        # Non-SGR CSI sequences (cursor moves, etc.) are dropped.
        pos = match.end()

    if pos < len(text):
        pending.append(text[pos:])
    flush()
    return spans


def has_ansi(text: str) -> bool:
    """Return True if the text contains an ANSI escape sequence."""
    return '\x1b[' in text


def strip_ansi(text: str) -> str:
    """Return the text with all ANSI/CSI escape sequences removed."""
    return _CSI_RE.sub('', text)
