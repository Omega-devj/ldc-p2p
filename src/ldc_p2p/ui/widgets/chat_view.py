"""Liste de messages performante : modèle Qt + délégué qui peint les bulles (aucun widget par message)."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAbstractTextDocumentLayout, QBrush, QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
                           QTextDocument)
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QStyleOptionViewItem

from .. import icons, theme

ITEM_ROLE = Qt.UserRole + 1
_URL = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)


def linkify(text: str) -> str:
    """Échappe TOUT le texte distant puis ne crée que des ancres http(s) : aucun HTML distant n'est interprété."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        url = m.group(1).rstrip(".,;:!?)")
        tail = m.group(1)[len(url):]
        out.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>{html.escape(tail)}')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out).replace("\n", "<br/>")


def human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def day_label(d: date) -> str:
    today = date.today()
    if d == today:
        return "Aujourd'hui"
    if (today - d).days == 1:
        return "Hier"
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    return f"{d.day} {mois[d.month - 1]}" + (f" {d.year}" if d.year != today.year else "")


def build_items(messages: list[dict], name_of, group: bool, transfers: dict[str, dict] | None = None,
                prev_ts: int | None = None, quote_of=None, prev_sender: str | None = None) -> list[dict]:
    """Transforme des messages en éléments d'affichage (séparateurs de date, messages système)."""
    items: list[dict] = []
    last_day = date.fromtimestamp(prev_ts / 1000) if prev_ts else None
    for m in messages:
        d = date.fromtimestamp(m["ts"] / 1000)
        if d != last_day:
            items.append({"type": "date", "text": day_label(d), "mid": f"date-{d.isoformat()}"})
            last_day, prev_sender = d, None
        if m["kind"] == "system":
            items.append({"type": "sys", "text": m["body"] or "", "mid": m["mid"]})
            prev_sender = None
            continue
        it = {"type": "msg", "msg": m, "out": m["direction"] == "out", "group": group,
              "name": name_of(m["sender"]) if (group and m["direction"] == "in") else "",
              "first": m["sender"] != prev_sender, "mid": m["mid"], "sender": m["sender"]}
        if transfers and m["kind"] == "file":
            it["transfer"] = transfers.get(m["mid"])
        if m.get("reply_to") and quote_of:
            it["quote"] = quote_of(m["reply_to"])
        items.append(it)
        prev_sender = m["sender"]
    return items


class ChatModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if role == ITEM_ROLE and 0 <= index.row() < len(self.items):
            return self.items[index.row()]
        return None

    def reset(self, items: list[dict]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def append(self, items: list[dict]) -> None:
        if items:
            self.beginInsertRows(QModelIndex(), len(self.items), len(self.items) + len(items) - 1)
            self.items.extend(items)
            self.endInsertRows()

    def prepend(self, items: list[dict]) -> None:
        if items:
            self.beginInsertRows(QModelIndex(), 0, len(items) - 1)
            self.items[0:0] = items
            self.endInsertRows()

    def row_of(self, mid: str) -> int:
        for i in range(len(self.items) - 1, -1, -1):
            if self.items[i].get("mid") == mid:
                return i
        return -1

    def replace(self, mid: str, **changes) -> bool:
        r = self.row_of(mid)
        if r < 0:
            return False
        self.items[r].update(changes)
        i = self.index(r)
        self.dataChanged.emit(i, i)
        return True

    def remove(self, mid: str) -> bool:
        r = self.row_of(mid)
        if r < 0:
            return False
        self.beginRemoveRows(QModelIndex(), r, r)
        del self.items[r]
        self.endRemoveRows()
        return True


def _path(rect: QRectF, tl: float, tr: float, br: float, bl: float) -> QPainterPath:
    p = QPainterPath()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    p.moveTo(x + tl, y)
    p.lineTo(x + w - tr, y)
    p.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
    p.lineTo(x + w, y + h - br)
    p.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
    p.lineTo(x + bl, y + h)
    p.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, 270, -90)
    p.lineTo(x, y + tl)
    p.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
    p.closeSubpath()
    return p


class ChatDelegate(QStyledItemDelegate):
    action = Signal(str, str)   # mid, nom d'action
    link = Signal(str)

    SIDE = 22
    PAD_X, PAD_Y = 13, 9
    RADIUS = 18

    def __init__(self, view: "ChatView"):
        super().__init__(view)
        self.view = view
        self.font = theme.base_font(10.5)
        self.small = theme.base_font(8.5)
        self.bold = QFont(self.font)
        self.bold.setWeight(QFont.DemiBold)
        self._cache: dict[tuple, dict] = {}
        self._hot: dict[int, list[tuple[QRect, str, str]]] = {}
        self._docs: dict[int, tuple[QTextDocument, QRectF]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- mise en page ---------------------------------------------------------------------
    def _max_bubble(self, width: int) -> int:
        return int(min(max(width * 0.72, 220), 600))

    def _layout(self, it: dict, width: int) -> dict:
        m = it["msg"]
        tr = it.get("transfer")
        key = (m["mid"], width, m["body"], (m.get("extra") or {}).get("edited") if isinstance(m.get("extra"), dict) else None,
               bool(it.get("name")), bool(it.get("quote")), it.get("first"), tr["state"] if tr else None, m["kind"])
        if key in self._cache:
            return self._cache[key]
        out = it["out"]
        fm = QFontMetrics(self.font)
        sfm = QFontMetrics(self.small)
        max_w = self._max_bubble(width)
        inner_max = max_w - 2 * self.PAD_X
        L: dict = {"max_w": max_w}
        y = self.PAD_Y
        if it.get("name") and it.get("first"):
            L["name_y"] = y
            y += sfm.height() + 3
        if it.get("quote"):
            L["quote_y"] = y
            y += 40
        time_txt = datetime.fromtimestamp(m["ts"] / 1000).strftime("%H:%M")
        edited = isinstance(m.get("extra"), dict) and m["extra"].get("edited")
        foot_txt = ("modifié · " if edited else "") + time_txt
        foot_w = sfm.horizontalAdvance(foot_txt) + (24 if out else 0) + (16 if m.get("expires_at") else 0) + 4
        L["foot_txt"], L["foot_w"] = foot_txt, foot_w
        if m["kind"] == "file":
            L["card_y"] = y
            card_h = 74 if (tr and tr["state"] in ("receiving", "sending")) else 62
            if tr and tr["state"] == "offered" and not out:
                card_h = 98
            L["card_h"] = card_h
            y += card_h
            w = min(inner_max, 300)
            L["text_w"], L["foot_inline"] = w, False
            y += 4
            L["foot_y"] = y
            y += sfm.height()
            L["w"] = w + 2 * self.PAD_X
        else:
            doc = QTextDocument()
            doc.setDefaultFont(self.font)
            doc.setDocumentMargin(0)
            doc.setDefaultStyleSheet("a { color: %s; text-decoration: underline; }" % ("#FFFFFF" if out else "#C4B5FD"))
            doc.setHtml(f"<div>{linkify(m['body'] or '')}</div>")
            doc.setTextWidth(inner_max)
            ideal = int(doc.idealWidth()) + 1
            doc.setTextWidth(max(ideal, 10))
            line_h = fm.lineSpacing()
            one_line = doc.size().height() <= line_h * 1.6
            inline = one_line and ideal + foot_w + 10 <= inner_max
            text_w = ideal + (foot_w + 10 if inline else 0)
            text_w = max(text_w, foot_w, 40 if not it.get("name") else sfm.horizontalAdvance(it["name"]) + 4)
            if it.get("quote"):
                text_w = max(text_w, 150)
            L["doc"], L["text_y"] = doc, y
            y += int(doc.size().height())
            if inline:
                L["foot_y"] = y - sfm.height() - 1
            else:
                y += 2
                L["foot_y"] = y
                y += sfm.height()
            L["foot_inline"], L["w"] = inline, int(min(text_w, inner_max)) + 2 * self.PAD_X
        y += self.PAD_Y - 2
        L["h"] = y
        self._cache[key] = L
        if len(self._cache) > 4000:
            self._cache.clear()
        return L

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        it = index.data(ITEM_ROLE)
        w = self.view.viewport().width()
        if it is None:
            return QSize(w, 10)
        t = it["type"]
        if t == "date":
            return QSize(w, 44)
        if t == "sys":
            return QSize(w, 40)
        L = self._layout(it, w)
        return QSize(w, L["h"] + (14 if it.get("first") else 3))

    # -- dessin -----------------------------------------------------------------------------
    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        it = index.data(ITEM_ROLE)
        if it is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = option.rect
        t = it["type"]
        if t in ("date", "sys"):
            self._paint_pill(p, r, it)
        else:
            self._paint_msg(p, r, it, index.row())
        p.restore()

    def _paint_pill(self, p: QPainter, r: QRect, it: dict) -> None:
        p.setFont(self.small)
        fm = QFontMetrics(self.small)
        text = it["text"]
        is_sys = it["type"] == "sys"
        w = fm.horizontalAdvance(text) + 28 + (18 if is_sys else 0)
        rect = QRectF(r.center().x() - w / 2, r.center().y() - 12, w, 24)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(theme.TEXT_2)
        x = rect.left() + 14
        if is_sys:
            p.drawPixmap(int(x), int(rect.center().y() - 6), icons.pixmap("lock", "#A78BFA", 12))
            x += 18
        p.drawText(QRectF(x, rect.top(), w, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_msg(self, p: QPainter, r: QRect, it: dict, row: int) -> None:
        m, out = it["msg"], it["out"]
        L = self._layout(it, r.width())
        top = r.top() + (11 if it.get("first") else 1)
        w, h = L["w"], L["h"]
        x = r.right() - self.SIDE - w if out else r.left() + self.SIDE
        rect = QRectF(x, top, w, h)
        failed = m["status"] == "failed"
        cut = 5
        rad = min(self.RADIUS, h / 2 - 1)
        path = _path(rect, rad, rad, cut if out else rad, rad if out else cut)
        if out:
            g = QLinearGradient(rect.topLeft(), rect.bottomRight())
            g.setColorAt(0, QColor(147, 100, 250, 245))
            g.setColorAt(1, QColor(109, 40, 217, 245))
            p.fillPath(path, g)
            hi = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            hi.setColorAt(0, QColor(255, 255, 255, 46))
            hi.setColorAt(0.45, QColor(255, 255, 255, 0))
            p.fillPath(path, hi)
            p.setPen(QPen(QColor(248, 113, 113, 230) if failed else QColor(216, 200, 255, 90), 1))
        else:
            p.fillPath(path, QColor(255, 255, 255, 22))
            hi = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            hi.setColorAt(0, QColor(255, 255, 255, 22))
            hi.setColorAt(0.5, QColor(255, 255, 255, 0))
            p.fillPath(path, hi)
            p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        hot: list[tuple[QRect, str, str]] = []
        cx = rect.left() + self.PAD_X
        sfm = QFontMetrics(self.small)
        if "name_y" in L:
            c1, _ = theme.avatar_colors(it["sender"])
            c1.setHsv(c1.hue(), max(90, c1.saturation() - 60), 255)
            p.setFont(self.small)
            f = QFont(self.small)
            f.setBold(True)
            p.setFont(f)
            p.setPen(c1)
            p.drawText(QPointF(cx, rect.top() + L["name_y"] + sfm.ascent()), it["name"])
        if "quote_y" in L and it.get("quote"):
            q = it["quote"]
            qr = QRectF(cx, rect.top() + L["quote_y"], rect.width() - 2 * self.PAD_X, 36)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 55) if out else QColor(255, 255, 255, 20))
            p.drawRoundedRect(qr, 9, 9)
            p.setBrush(QColor("#C4B5FD"))
            p.drawRoundedRect(QRectF(qr.left(), qr.top() + 5, 3, qr.height() - 10), 1.5, 1.5)
            f = QFont(self.small)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#E9E3FF") if out else QColor("#C4B5FD"))
            p.drawText(QPointF(qr.left() + 10, qr.top() + 15), q["name"])
            p.setFont(self.small)
            p.setPen(QColor(255, 255, 255, 190))
            txt = sfm.elidedText(q["text"].replace("\n", " "), Qt.ElideRight, int(qr.width() - 18))
            p.drawText(QPointF(qr.left() + 10, qr.top() + 29), txt)
        if m["kind"] == "file":
            self._paint_file(p, it, L, rect, hot, row)
        elif "doc" in L:
            doc: QTextDocument = L["doc"]
            p.save()
            p.translate(cx, rect.top() + L["text_y"])
            pc = QAbstractTextDocumentLayout.PaintContext()
            pc.palette.setColor(pc.palette.ColorRole.Text, QColor("#FFFFFF") if out else QColor("#F3F3F8"))
            doc.documentLayout().draw(p, pc)
            p.restore()
            self._docs[row] = (doc, QRectF(cx, rect.top() + L["text_y"], doc.textWidth(), doc.size().height()))
        # pied : heure, état
        fy = rect.top() + L["foot_y"]
        p.setFont(self.small)
        p.setPen(QColor(255, 255, 255, 170) if out else theme.TEXT_3)
        fx = rect.right() - self.PAD_X
        if out:
            self._paint_status(p, m["status"], fx - 16, fy + sfm.height() / 2)
            fx -= 22
        p.drawText(QRectF(fx - L["foot_w"] + (24 if out else 0), fy, L["foot_w"] - (24 if out else 0), sfm.height()),
                   Qt.AlignRight | Qt.AlignVCenter, L["foot_txt"])
        if m.get("expires_at"):
            tx = fx - sfm.horizontalAdvance(L["foot_txt"]) - 16
            p.drawPixmap(int(tx), int(fy + 1), icons.pixmap("timer", "#E9E3FF" if out else "#A78BFA", 12))
        if failed:
            rr = QRect(int(rect.left() - 84) if out else int(rect.right() + 8), int(rect.center().y() - 12), 76, 24)
            p.setPen(QPen(QColor(248, 113, 113, 180), 1))
            p.setBrush(QColor(248, 113, 113, 36))
            p.drawRoundedRect(rr, 12, 12)
            p.setPen(QColor("#FCA5A5"))
            p.drawText(rr, Qt.AlignCenter, "Réessayer")
            hot.append((rr, "retry", m["mid"]))
        self._hot[row] = hot

    def _paint_status(self, p: QPainter, status: str, x: float, cy: float) -> None:
        col = {"read": QColor("#67E8F9"), "failed": QColor("#FCA5A5")}.get(status, QColor(255, 255, 255, 175))
        pen = QPen(col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if status == "pending":
            p.drawEllipse(QPointF(x + 6, cy), 4.2, 4.2)
            p.drawLine(QPointF(x + 6, cy), QPointF(x + 6, cy - 2.6))
            p.drawLine(QPointF(x + 6, cy), QPointF(x + 8, cy + 1))
        elif status == "failed":
            p.drawLine(QPointF(x + 6, cy - 4), QPointF(x + 6, cy + 1))
            p.drawPoint(QPointF(x + 6, cy + 4))
        else:
            def tick(dx: float) -> None:
                path = QPainterPath(QPointF(x + dx, cy))
                path.lineTo(x + dx + 2.6, cy + 3)
                path.lineTo(x + dx + 8, cy - 3.6)
                p.drawPath(path)
            if status == "sent":
                tick(2)
            else:  # delivered, read
                tick(-1)
                tick(4)

    def _paint_file(self, p: QPainter, it: dict, L: dict, rect: QRectF, hot: list, row: int) -> None:
        m, out, tr = it["msg"], it["out"], it.get("transfer")
        extra = m.get("extra") if isinstance(m.get("extra"), dict) else {}
        cx = rect.left() + self.PAD_X
        cy = rect.top() + L["card_y"]
        card = QRectF(cx, cy, rect.width() - 2 * self.PAD_X, L["card_h"])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 55) if out else QColor(255, 255, 255, 18))
        p.drawRoundedRect(card, 12, 12)
        ic = QRectF(card.left() + 10, card.top() + 10, 40, 40)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(ic, 10, 10)
        p.drawPixmap(int(ic.left() + 10), int(ic.top() + 10), icons.pixmap("file", "#FFFFFF", 20))
        sfm = QFontMetrics(self.small)
        fm = QFontMetrics(self.font)
        tx = ic.right() + 10
        tw = int(card.right() - tx - 10)
        p.setFont(self.bold)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QPointF(tx, card.top() + 25), fm.elidedText(extra.get("name", m["body"] or "fichier"), Qt.ElideMiddle, tw))
        state = tr["state"] if tr else ("done" if out else "failed")
        size = human_size(int(extra.get("size", 0)))
        label = {"offered": "Proposé" if not out else "En attente d'acceptation", "receiving": "Réception…", "sending": "Envoi…",
                 "paused": "En pause", "done": "Terminé" if not out else "Envoyé", "declined": "Refusé", "failed": "Échec"}.get(state, "")
        p.setFont(self.small)
        p.setPen(QColor(255, 255, 255, 175))
        p.drawText(QPointF(tx, card.top() + 43), f"{size} · {label}")
        if tr and state in ("receiving", "sending"):
            pb = QRectF(card.left() + 10, card.bottom() - 14, card.width() - 20, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 35))
            p.drawRoundedRect(pb, 2.5, 2.5)
            frac = min(1.0, tr["next_idx"] / max(1, tr["nchunks"])) if state == "receiving" else it.get("progress", 0.0)
            p.setBrush(QColor("#FFFFFF") if out else QColor("#A78BFA"))
            p.drawRoundedRect(QRectF(pb.left(), pb.top(), pb.width() * frac, pb.height()), 2.5, 2.5)
        if tr and not out and state == "offered":
            for i, (txt, name, primary) in enumerate((("Accepter", "accept", True), ("Refuser", "decline", False))):
                br = QRect(int(card.left() + 10 + i * 96), int(card.bottom() - 36), 88, 28)
                p.setPen(QPen(QColor(196, 181, 253, 120), 1))
                p.setBrush(QColor(139, 92, 246, 190) if primary else QColor(255, 255, 255, 18))
                p.drawRoundedRect(br, 10, 10)
                p.setPen(QColor("white"))
                p.setFont(self.bold)
                p.drawText(br, Qt.AlignCenter, txt)
                hot.append((br, name, m["mid"]))
        if tr and not out and state == "done":
            hot.append((card.toRect(), "open", m["mid"]))
        if tr and not out and state in ("failed", "paused"):
            hot.append((card.toRect(), "accept", m["mid"]))

    # -- interaction -------------------------------------------------------------------------
    def hotspot_at(self, row: int, pos) -> tuple[str, str] | None:
        for rect, name, mid in self._hot.get(row, ()):
            if rect.contains(pos):
                return name, mid
        return None

    def anchor_at(self, row: int, pos, item_rect: QRect) -> str | None:
        entry = self._docs.get(row)
        if not entry:
            return None
        doc, r = entry
        if r.contains(QPointF(pos)):
            a = doc.documentLayout().anchorAt(QPointF(pos) - r.topLeft())
            return a or None
        return None


class ChatView(QListView):
    action = Signal(str, str)
    link = Signal(str)
    context = Signal(str, object)
    load_more = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = ChatModel(self)
        self.setModel(self.model_)
        self.delegate = ChatDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.NoFrame)
        self.setUniformItemSizes(False)
        self.setResizeMode(QListView.Adjust)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(200)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._press = None
        self._loading = False

    def _on_scroll(self, v: int) -> None:
        if v <= 40 and self.model_.rowCount() and not self._loading:
            self.load_more.emit()

    def at_bottom(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - 60

    def scroll_bottom(self) -> None:
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def resizeEvent(self, e) -> None:
        self.delegate.clear_cache()
        super().resizeEvent(e)
        self.scheduleDelayedItemsLayout()

    def _index_item(self, pos):
        idx = self.indexAt(pos)
        return idx, (idx.data(ITEM_ROLE) if idx.isValid() else None)

    def mouseMoveEvent(self, e) -> None:
        idx, it = self._index_item(e.position().toPoint())
        cursor = Qt.ArrowCursor
        if it and it["type"] == "msg":
            pt = e.position().toPoint()
            if self.delegate.hotspot_at(idx.row(), pt) or self.delegate.anchor_at(idx.row(), pt, self.visualRect(idx)):
                cursor = Qt.PointingHandCursor
        self.viewport().setCursor(cursor)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        pt = e.position().toPoint()
        if e.button() == Qt.LeftButton and self._press and (pt - self._press).manhattanLength() < 6:
            idx, it = self._index_item(pt)
            if it and it["type"] == "msg":
                hs = self.delegate.hotspot_at(idx.row(), pt)
                if hs:
                    self.action.emit(hs[0], hs[1])
                else:
                    a = self.delegate.anchor_at(idx.row(), pt, self.visualRect(idx))
                    if a:
                        self.link.emit(a)
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:
        idx, it = self._index_item(e.pos())
        if it and it["type"] == "msg":
            self.context.emit(it["mid"], e.globalPos())

    def set_items(self, items: list[dict]) -> None:
        self._loading = True
        self.delegate.clear_cache()
        self.model_.reset(items)
        self.scroll_bottom()
        QTimer.singleShot(150, lambda: setattr(self, "_loading", False))
