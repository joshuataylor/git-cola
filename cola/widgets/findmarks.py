"""Mark the DAG find bar's matches in the views that show a commit

The commit list, the commit message pane and the file list all mark the
same needle, so the matching and the marks live here, below the widgets
that import each other.
"""
from __future__ import annotations

from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets
from qtpy.QtCore import Qt

# Translucent, so it reads as a highlighter mark over the text in light and
# dark themes, and over the selection colour.
HIGHLIGHT = QtGui.QColor(255, 200, 0, 110)


def match_spans(text, needle, case_sensitive=False):
    """(start, length) of each non-overlapping occurrence of needle in text"""
    if not text or not needle:
        return []
    if not case_sensitive:
        text = text.lower()
        needle = needle.lower()
    spans = []
    start = text.find(needle)
    while start >= 0:
        spans.append((start, len(needle)))
        start = text.find(needle, start + len(needle))
    return spans


def paint_spans(painter, text, spans, text_x, rect, font_metrics):
    """Mark each (start, length) span of text, drawn from text_x, within rect"""
    for start, length in spans:
        left = text_x + font_metrics.horizontalAdvance(text[:start])
        if left >= rect.right():
            break
        width = font_metrics.horizontalAdvance(text[start : start + length])
        mark = QtCore.QRect(int(left), rect.top() + 1, int(width), rect.height() - 2)
        painter.fillRect(mark.intersected(rect), HIGHLIGHT)


def paint_item_spans(delegate, painter, option, index, view, spans):
    """Mark spans of an item view cell's text, after the cell has been painted"""
    text = index.data(Qt.DisplayRole)
    if not text or not spans:
        return
    opt = QtWidgets.QStyleOptionViewItem(option)
    delegate.initStyleOption(opt, index)
    style = view.style() if view is not None else QtWidgets.QApplication.style()
    text_rect = style.subElementRect(QtWidgets.QStyle.SE_ItemViewItemText, opt, view)
    margin = style.pixelMetric(QtWidgets.QStyle.PM_FocusFrameHMargin, None, view) + 1
    painter.save()
    painter.setClipRect(text_rect)
    paint_spans(
        painter, text, spans, text_rect.left() + margin, text_rect, option.fontMetrics
    )
    painter.restore()


class FindMarkDelegate(QtWidgets.QStyledItemDelegate):
    """Paint a cell as usual, then mark the view's find_needle in column 0

    The view sets find_needle (None for no marks) and find_case_sensitive.
    """

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        view = self.parent()
        needle = getattr(view, 'find_needle', None)
        if not needle or index.column() != 0:
            return
        case_sensitive = getattr(view, 'find_case_sensitive', False)
        spans = match_spans(index.data(Qt.DisplayRole), needle, case_sensitive)
        paint_item_spans(self, painter, option, index, view, spans)


def text_selections(document, text, spans):
    """Extra selections marking (start, length) spans of a document's text

    Python indexes code points, QTextDocument UTF-16 code units, so the
    positions are converted for text outside the Basic Multilingual Plane.
    """
    selections = []
    if not spans:
        return selections
    fmt = QtGui.QTextCharFormat()
    fmt.setBackground(HIGHLIGHT)
    for start, length in spans:
        begin = _utf16_length(text[:start])
        end = begin + _utf16_length(text[start : start + length])
        cursor = QtGui.QTextCursor(document)
        cursor.setPosition(begin)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        selection = QtWidgets.QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        selections.append(selection)
    return selections


def _utf16_length(text):
    return len(text.encode('utf-16-le')) // 2
