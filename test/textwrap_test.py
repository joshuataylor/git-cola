"""Test the textwrap module"""
import pytest

from cola import textwrap


class WordWrapDefaults:
    def __init__(self):
        self.tabwidth = 8
        self.limit = None

    def wrap(self, text, break_on_hyphens=True):
        return textwrap.word_wrap(
            text, self.tabwidth, self.limit, break_on_hyphens=break_on_hyphens
        )


@pytest.fixture
def wordwrap():
    """Provide default word wrap options for tests"""
    return WordWrapDefaults()


def test_word_wrap(wordwrap):
    wordwrap.limit = 16
    text = """
12345678901 3 56 8 01 3 5 7

1 3 5"""
    expect = """
12345678901 3 56
8 01 3 5 7

1 3 5"""
    assert expect == wordwrap.wrap(text)


def test_word_wrap_dashes(wordwrap):
    wordwrap.limit = 4
    text = '123-5'
    expect = '123-5'
    assert expect == wordwrap.wrap(text)


def test_word_wrap_leading_spaces(wordwrap):
    wordwrap.limit = 4
    expect = '1234\n5'

    assert expect == wordwrap.wrap('1234 5')
    assert expect == wordwrap.wrap('1234  5')
    assert expect == wordwrap.wrap('1234   5')
    assert expect == wordwrap.wrap('1234    5')
    assert expect == wordwrap.wrap('1234     5')

    expect = '123\n4'
    assert expect == wordwrap.wrap('123 4')
    assert expect == wordwrap.wrap('123  4')
    assert expect == wordwrap.wrap('123   4')
    assert expect == wordwrap.wrap('123    4')
    assert expect == wordwrap.wrap('123     4')


def test_word_wrap_double_dashes(wordwrap):
    wordwrap.limit = 4
    text = '12--5'
    expect = '12--\n5'
    actual = wordwrap.wrap(text, break_on_hyphens=True)
    assert expect == actual

    expect = '12--5'
    actual = wordwrap.wrap(text, break_on_hyphens=False)
    assert expect == actual


def test_word_wrap_many_lines(wordwrap):
    wordwrap.limit = 2
    text = """
aa


bb cc dd"""

    expect = """
aa


bb
cc
dd"""
    actual = wordwrap.wrap(text)
    assert expect == actual


def test_word_python_code(wordwrap):
    wordwrap.limit = 78
    text = """
if True:
    print "hello world"
else:
    print "hello world"

"""
    expect = text
    actual = wordwrap.wrap(text)
    assert expect == actual


def test_word_wrap_spaces(wordwrap):
    wordwrap.limit = 2
    text = ' ' * 6
    expect = ''
    actual = wordwrap.wrap(text)
    assert expect == actual


def test_word_wrap_special_tag(wordwrap):
    wordwrap.limit = 2
    text = """
This test is so meta, even this sentence

Cheered-on-by: Avoids word-wrap
C.f. This also avoids word-wrap
References: This also avoids word-wrap
See-also: This also avoids word-wrap
Related-to: This also avoids word-wrap
Link: This also avoids word-wrap
"""

    expect = """
This
test
is
so
meta,
even
this
sentence

Cheered-on-by: Avoids word-wrap
C.f. This also avoids word-wrap
References: This also avoids word-wrap
See-also: This also avoids word-wrap
Related-to: This also avoids word-wrap
Link: This also avoids word-wrap
"""
    actual = wordwrap.wrap(text)
    assert expect == actual


def test_word_wrap_space_at_start_of_wrap(wordwrap):
    inputs = """0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 """
    expect = """0 1 2 3 4 5 6 7 8 9\n0 1 2 3 4 5 6 7 8"""
    wordwrap.limit = 20

    actual = wordwrap.wrap(inputs)
    assert expect == actual


def test_word_wrap_keeps_tabs_at_start(wordwrap):
    inputs = """\tfirst line\n\n\tsecond line"""
    expect = """\tfirst line\n\n\tsecond line"""
    wordwrap.limit = 20

    actual = wordwrap.wrap(inputs)
    assert expect == actual


def test_word_wrap_keeps_twospace_indents(wordwrap):
    inputs = """first line\n\n* branch:\n  line1\n  line2\n"""
    expect = """first line\n\n* branch:\n  line1\n  line2\n"""
    wordwrap.limit = 20

    actual = wordwrap.wrap(inputs)
    assert expect == actual


def test_word_wrap_ranges():
    text = 'a bb ccc dddd\neeeee'
    expect = 'a\nbb\nccc\ndddd\neeeee'
    actual = textwrap.word_wrap(text, 8, 2)
    assert expect == actual

    expect = 'a bb\nccc\ndddd\neeeee'
    actual = textwrap.word_wrap(text, 8, 4)
    assert expect == actual

    text = 'a bb ccc dddd\n\teeeee'
    expect = 'a bb\nccc\ndddd\n\t\neeeee'
    actual = textwrap.word_wrap(text, 8, 4)
    assert expect == actual


def test_triplets():
    text = 'xx0 xx1 xx2 xx3 xx4 xx5 xx6 xx7 xx8 xx9 xxa xxb'

    expect = 'xx0 xx1 xx2 xx3 xx4 xx5 xx6\nxx7 xx8 xx9 xxa xxb'
    actual = textwrap.word_wrap(text, 8, 27)
    assert expect == actual

    expect = 'xx0 xx1 xx2 xx3 xx4 xx5\nxx6 xx7 xx8 xx9 xxa xxb'
    actual = textwrap.word_wrap(text, 8, 26)
    assert expect == actual

    actual = textwrap.word_wrap(text, 8, 25)
    assert expect == actual

    actual = textwrap.word_wrap(text, 8, 24)
    assert expect == actual

    actual = textwrap.word_wrap(text, 8, 23)
    assert expect == actual

    expect = 'xx0 xx1 xx2 xx3 xx4\nxx5 xx6 xx7 xx8 xx9\nxxa xxb'
    actual = textwrap.word_wrap(text, 8, 22)
    assert expect == actual


def test_unwrap_joins_wrapped_paragraph():
    text = 'This is a long paragraph that\nwas hard-wrapped at some\nnarrow width.'
    expect = 'This is a long paragraph that was hard-wrapped at some narrow width.'
    assert expect == textwrap.unwrap(text)


def test_unwrap_keeps_blank_lines_as_paragraph_breaks():
    text = 'First paragraph\nline two.\n\nSecond paragraph\nline two.\n'
    expect = 'First paragraph line two.\n\nSecond paragraph line two.\n'
    assert expect == textwrap.unwrap(text)


def test_unwrap_preserves_trailers_and_indented_lines():
    text = 'Intro prose\nthat wraps.\n\n' '    indented code\n    more code\n\n' 'Signed-off-by: A Person <a@example.com>\n' 'Reviewed-by: B Person <b@example.com>'
    expect = 'Intro prose that wraps.\n\n' '    indented code\n    more code\n\n' 'Signed-off-by: A Person <a@example.com>\n' 'Reviewed-by: B Person <b@example.com>'
    assert expect == textwrap.unwrap(text)


def test_unwrap_preserves_lists_quotes_and_fences():
    text = '- first item\n- second item\n' '1. numbered\n2) also numbered\n' '> quoted\n> more quoted\n' '```\ncode here\n```\n' '# heading\n' 'prose after\nthe structure'
    expect = '- first item\n- second item\n' '1. numbered\n2) also numbered\n' '> quoted\n> more quoted\n' '```\ncode here\n```\n' '# heading\n' 'prose after the structure'
    assert expect == textwrap.unwrap(text)


def test_unwrap_strips_trailing_whitespace_when_joining():
    assert 'one two' == textwrap.unwrap('one  \ntwo')


def test_unwrap_empty_text():
    assert '' == textwrap.unwrap('')


def test_rewrap_round_trips_through_word_wrap(wordwrap):
    wordwrap.limit = 16
    text = '12345678901 3\n56 8 01 3 5 7\n\n1 3 5'
    expect = '12345678901 3 56\n8 01 3 5 7\n\n1 3 5'
    assert expect == textwrap.rewrap(text, wordwrap.tabwidth, wordwrap.limit)
