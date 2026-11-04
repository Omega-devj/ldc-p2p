"""Liste de messages performante : modèle Qt + délégué qui peint les bulles (aucun widget par message)."""
from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QAbstractTextDocumentLayout, QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QImageReader,
                           QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QStyleOptionViewItem

from .. import icons, theme

ITEM_ROLE = Qt.UserRole + 1
_URL = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
THUMB_MAX = (280, 200)


def _styled(piece: str, mentions: tuple[str, ...], highlight: str) -> str:
    """Échappe un morceau de texte puis met en forme @mentions et mot recherché (par découpage : jamais de HTML distant)."""
    alts = [re.escape("@" + m) for m in sorted(mentions, key=len, reverse=True)]
    if highlight:
        alts.append(re.escape(highlight))
    if not alts:
        return html.escape(piece)
    out = []
    names = {m.casefold() for m in mentions}
    for i, part in enumerate(re.split("(" + "|".join(alts) + ")", piece, flags=re.IGNORECASE)):
        if i % 2 == 0:
            out.append(html.escape(part))
        elif part.startswith("@") and part[1:].casefold() in names:
            out.append(f'<span style="color:#FCD34D;font-weight:600">{html.escape(part)}</span>')
        else:
            out.append(f'<span style="background-color:rgba(251,191,36,0.45);color:#fff">{html.escape(part)}</span>')
    return "".join(out)


def linkify(text: str, mentions: tuple[str, ...] = (), highlight: str = "") -> str:
    """Seules des ancres http(s) sont créées ; tout le reste est échappé."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(_styled(text[last:m.start()], mentions, highlight))
        url = m.group(1).rstrip(".,;:!?)")
        tail = m.group(1)[len(url):]
        out.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>{html.escape(tail)}')
        last = m.end()
    out.append(_styled(text[last:], mentions, highlight))
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
                prev_ts: int | None = None, quote_of=None, prev_sender: str | None = None,
                reactions: dict[str, dict] | None = None, me: str = "", new_from: str | None = None) -> list[dict]:
    """Transforme des messages en éléments d'affichage (séparateurs de date, « nouveaux messages », système)."""
    items: list[dict] = []
    last_day = date.fromtimestamp(prev_ts / 1000) if prev_ts else None
    for m in messages:
        d = date.fromtimestamp(m["ts"] / 1000)
        if d != last_day:
            items.append({"type": "date", "text": day_label(d), "mid": f"date-{d.isoformat()}"})
            last_day, prev_sender = d, None
        if new_from and m["mid"] == new_from:
            items.append({"type": "new", "text": "Nouveaux messages", "mid": "new-marker"})
            prev_sender = None
        if m["kind"] == "system":
            items.append({"type": "sys", "text": m["body"] or "", "mid": m["mid"]})
            prev_sender = None
            continue
        extra = m.get("extra") if isinstance(m.get("extra"), dict) else {}
        men = extra.get("men") or []
        it = {"type": "msg", "msg": m, "out": m["direction"] == "out", "group": group,
              "name": name_of(m["sender"]) if (group and m["direction"] == "in") else "",
              "first": m["sender"] != prev_sender, "mid": m["mid"], "sender": m["sender"],
              "reactions": (reactions or {}).get(m["mid"], {}), "me": me,
              "mentions_me": bool(me and me in men),
              "mention_names": tuple(name_of(i) for i in men) if men else ()}
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
    SIDE = 22
    PAD_X, PAD_Y = 13, 9
    RADIUS = 18
    REACT_H = 26

    def __init__(self, view: "ChatView"):
        super().__init__(view)
        self.view = view
        self.font = theme.base_font(10.5)
        self.small = theme.base_font(8.5)
        self.bold = QFont(self.font)
        self.bold.setWeight(QFont.DemiBold)
        self._cache: dict[tuple, dict] = {}
        self._hot: dict[int, list[tuple[QRect, str, str]]] = {}
        self._docs: dict[int, tuple[QTextDocument, QRectF, str]] = {}
        self._thumbs: dict[str, QPixmap | None] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- miniatures d'images (formats courants uniquement, taille bornée) ----------------------------
    def _thumb(self, path: str | None) -> QPixmap | None:
        if not path or Path(path).suffix.lower() not in IMG_EXT:
            return None
        if path in self._thumbs:
            return self._thumbs[path]
        pix = None
        try:
            if Path(path).is_file() and Path(path).stat().st_size <= 25 * 1024 * 1024:
                rd = QImageReader(path)
                rd.setAutoTransform(True)
                rd.setAllocationLimit(128)  # Mo : protège contre les images piégées
                sz = rd.size()
                if sz.isValid() and sz.width() * sz.height() <= 40_000_000:
                    dpr = self.view.devicePixelRatioF()
                    rd.setScaledSize(sz.scaled(int(THUMB_MAX[0] * dpr), int(THUMB_MAX[1] * dpr), Qt.KeepAspectRatio))
                    img = rd.read()
                    if not img.isNull():
                        pix = QPixmap.fromImage(img)
                        pix.setDevicePixelRatio(dpr)
        except Exception:
            pix = None
        self._thumbs[path] = pix
        return pix

    # -- mise en page ---------------------------------------------------------------------------------
    def _max_bubble(self, width: int) -> int:
        return int(min(max(width * 0.72, 220), 600))

    def _thumb_for(self, it: dict) -> QPixmap | None:
        tr = it.get("transfer")
        if it["msg"]["kind"] != "file" or not tr or not tr.get("path"):
            return None
        if tr["direction"] == "in" and tr["state"] != "done":
            return None
        return self._thumb(tr["path"])

    def _layout(self, it: dict, width: int) -> dict:
        m = it["msg"]
        tr = it.get("transfer")
        extra = m.get("extra") if isinstance(m.get("extra"), dict) else {}
        key = (m["mid"], width, m["body"], extra.get("edited"), bool(it.get("name")), bool(it.get("quote")), it.get("first"),
               tr["state"] if tr else None, m["kind"], tuple(it.get("mention_names", ())), self.view.highlight,
               tr.get("path") if tr else None, theme.current())
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
        foot_txt = ("modifié · " if extra.get("edited") else "") + time_txt
        foot_w = sfm.horizontalAdvance(foot_txt) + (24 if out else 0) + (16 if m.get("expires_at") else 0) + 4
        L["foot_txt"], L["foot_w"] = foot_txt, foot_w
        if m["kind"] == "file":
            thumb = self._thumb_for(it)
            if thumb is not None:
                L["thumb"] = thumb
                tw, th = int(thumb.width() / thumb.devicePixelRatio()), int(thumb.height() / thumb.devicePixelRatio())
                L["thumb_y"], L["thumb_size"] = y, (tw, th)
                y += th + 8
            L["card_y"] = y
            card_h = 74 if (tr and tr["state"] in ("receiving", "sending", "paused")) else 62
            if tr and tr["state"] == "offered" and not out:
                card_h = 98
            if thumb is not None and card_h == 62:
                card_h = 50
            L["card_h"] = card_h
            y += card_h
            w = max(min(inner_max, 300), (L["thumb_size"][0] if thumb is not None else 0))
            L["text_w"], L["foot_inline"] = w, False
            y += 4
            L["foot_y"] = y
            y += sfm.height()
            L["w"] = w + 2 * self.PAD_X
        else:
            doc = QTextDocument()
            doc.setDefaultFont(self.font)
            doc.setDocumentMargin(0)
            doc.setDefaultStyleSheet("a { color: %s; text-decoration: underline; }" % ("#FFFFFF" if out else theme.accent_hex(2 if theme.is_light() else 3)))
            doc.setHtml(f"<div>{linkify(m['body'] or '', tuple(it.get('mention_names', ())), self.view.highlight)}</div>")
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
        if t in ("sys", "new"):
            return QSize(w, 40)
        L = self._layout(it, w)
        return QSize(w, L["h"] + (14 if it.get("first") else 3) + (self.REACT_H if it.get("reactions") else 0))

    # -- dessin ----------------------------------------------------------------------------------------
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
        elif t == "new":
            self._paint_new(p, r, it)
        else:
            self._paint_msg(p, r, it, index.row())
        p.restore()

    def _paint_new(self, p: QPainter, r: QRect, it: dict) -> None:
        p.setFont(self.small)
        fm = QFontMetrics(self.small)
        w = fm.horizontalAdvance(it["text"]) + 28
        cy = r.center().y()
        p.setPen(QPen(theme.accent(1, 120), 1))
        p.drawLine(r.left() + 20, cy, int(r.center().x() - w / 2 - 8), cy)
        p.drawLine(int(r.center().x() + w / 2 + 8), cy, r.right() - 20, cy)
        rect = QRectF(r.center().x() - w / 2, cy - 11, w, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.accent(0, 230))
        p.drawRoundedRect(rect, 11, 11)
        p.setPen(QColor("white"))
        p.drawText(rect, Qt.AlignCenter, it["text"])

    def _paint_pill(self, p: QPainter, r: QRect, it: dict) -> None:
        p.setFont(self.small)
        fm = QFontMetrics(self.small)
        text = it["text"]
        is_sys = it["type"] == "sys"
        w = fm.horizontalAdvance(text) + 28 + (18 if is_sys else 0)
        rect = QRectF(r.center().x() - w / 2, r.center().y() - 12, w, 24)
        p.setPen(QPen(theme.ov(22), 1))
        p.setBrush(theme.ov(12))
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(theme.TEXT_2)
        x = rect.left() + 14
        if is_sys:
            p.drawPixmap(int(x), int(rect.center().y() - 6), icons.pixmap("lock", theme.accent_hex(1), 12))
            x += 18
        p.drawText(QRectF(x, rect.top(), w, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_msg(self, p: QPainter, r: QRect, it: dict, row: int) -> None:
        m, out = it["msg"], it["out"]
        light = theme.is_light()
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
            g.setColorAt(0, theme.accent(0 if light else 1, 245))
            g.setColorAt(1, theme.accent(2, 245))
            p.fillPath(path, g)
            hi = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            hi.setColorAt(0, theme.on_accent(46))
            hi.setColorAt(0.45, theme.on_accent(0))
            p.fillPath(path, hi)
            p.setPen(QPen(QColor(248, 113, 113, 230) if failed else theme.accent(3, 90), 1))
        else:
            if light:
                p.fillPath(path, QColor(255, 255, 255, 215))
            else:
                p.fillPath(path, QColor(255, 255, 255, 22))
                hi = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                hi.setColorAt(0, QColor(255, 255, 255, 22))
                hi.setColorAt(0.5, QColor(255, 255, 255, 0))
                p.fillPath(path, hi)
            if it.get("mentions_me"):
                p.setPen(QPen(QColor(252, 211, 77, 220), 1.6))
            else:
                p.setPen(QPen(theme.ov(36), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        hot: list[tuple[QRect, str, str]] = []
        cx = rect.left() + self.PAD_X
        sfm = QFontMetrics(self.small)
        if "name_y" in L:
            c1, _ = theme.avatar_colors(it["sender"])
            c1.setHsv(c1.hue(), max(90, c1.saturation() - 60), 190 if light else 255)
            f = QFont(self.small)
            f.setBold(True)
            p.setFont(f)
            p.setPen(c1)
            p.drawText(QPointF(cx, rect.top() + L["name_y"] + sfm.ascent()), it["name"])
        if "quote_y" in L and it.get("quote"):
            q = it["quote"]
            qr = QRectF(cx, rect.top() + L["quote_y"], rect.width() - 2 * self.PAD_X, 36)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 55) if out else theme.ov(20))
            p.drawRoundedRect(qr, 9, 9)
            p.setBrush(theme.accent(0 if (light and not out) else 3))
            p.drawRoundedRect(QRectF(qr.left(), qr.top() + 5, 3, qr.height() - 10), 1.5, 1.5)
            f = QFont(self.small)
            f.setBold(True)
            p.setFont(f)
            p.setPen(theme.on_accent(235) if out else theme.accent(0 if light else 3))
            p.drawText(QPointF(qr.left() + 10, qr.top() + 15), q["name"])
            p.setFont(self.small)
            p.setPen(theme.on_accent(200) if out else theme.ink(190))
            txt = sfm.elidedText(q["text"].replace("\n", " "), Qt.ElideRight, int(qr.width() - 18))
            p.drawText(QPointF(qr.left() + 10, qr.top() + 29), txt)
        if m["kind"] == "file":
            self._paint_file(p, it, L, rect, hot, row)
        elif "doc" in L:
            doc: QTextDocument = L["doc"]
            p.save()
            p.translate(cx, rect.top() + L["text_y"])
            pc = QAbstractTextDocumentLayout.PaintContext()
            pc.palette.setColor(pc.palette.ColorRole.Text, QColor("#FFFFFF") if out else theme.ink())
            if self.view.sel_mid == it["mid"] and self.view.sel_a != self.view.sel_b:
                cur = QTextCursor(doc)
                cur.setPosition(min(self.view.sel_a, self.view.sel_b))
                cur.setPosition(max(self.view.sel_a, self.view.sel_b), QTextCursor.KeepAnchor)
                sel = QAbstractTextDocumentLayout.Selection()
                sel.cursor = cur
                sel.format.setBackground(QColor(255, 255, 255, 90) if out else theme.accent(0, 120))
                sel.format.setForeground(QColor("#FFFFFF"))
                pc.selections = [sel]
            doc.documentLayout().draw(p, pc)
            p.restore()
            self._docs[row] = (doc, QRectF(cx, rect.top() + L["text_y"], doc.textWidth(), doc.size().height()), it["mid"])
        # pied : heure, état
        fy = rect.top() + L["foot_y"]
        p.setFont(self.small)
        p.setPen(theme.on_accent(175) if out else theme.TEXT_3)
        fx = rect.right() - self.PAD_X
        if out:
            self._paint_status(p, m["status"], fx - 16, fy + sfm.height() / 2)
            fx -= 22
        p.drawText(QRectF(fx - L["foot_w"] + (24 if out else 0), fy, L["foot_w"] - (24 if out else 0), sfm.height()),
                   Qt.AlignRight | Qt.AlignVCenter, L["foot_txt"])
        if m.get("expires_at"):
            tx = fx - sfm.horizontalAdvance(L["foot_txt"]) - 16
            p.drawPixmap(int(tx), int(fy + 1), icons.pixmap("timer", "#E9E3FF" if out else theme.accent_hex(1), 12))
        if failed:
            rr = QRect(int(rect.left() - 84) if out else int(rect.right() + 8), int(rect.center().y() - 12), 76, 24)
            p.setPen(QPen(QColor(248, 113, 113, 180), 1))
            p.setBrush(QColor(248, 113, 113, 36))
            p.drawRoundedRect(rr, 12, 12)
            p.setPen(QColor("#B91C1C") if light else QColor("#FCA5A5"))
            p.drawText(rr, Qt.AlignCenter, "Réessayer")
            hot.append((rr, "retry", m["mid"]))
        # réactions : pastilles sous la bulle
        if it.get("reactions"):
            rx = rect.right() - 4 if out else rect.left() + 4
            ry = rect.bottom() + 3
            chips = []
            ef = QFont(self.small)
            ef.setPointSizeF(9.5)
            efm = QFontMetrics(ef)
            for emoji, who in it["reactions"].items():
                lab = emoji + (f" {len(who)}" if len(who) > 1 else "")
                chips.append((emoji, lab, efm.horizontalAdvance(lab) + 18, it["me"] in who))
            total = sum(c[2] + 5 for c in chips) - 5
            cxr = rx - total if out else rx
            p.setFont(ef)
            for emoji, lab, cw, mine in chips:
                cr = QRectF(cxr, ry, cw, 22)
                p.setPen(QPen(theme.accent(1, 200) if mine else theme.ov(40), 1))
                p.setBrush(theme.accent(0, 70) if mine else (QColor(255, 255, 255, 200) if light else theme.ov(22)))
                p.drawRoundedRect(cr, 11, 11)
                p.setPen(theme.ink())
                p.drawText(cr, Qt.AlignCenter, lab)
                hot.append((cr.toRect(), "react:" + emoji, m["mid"]))
                cxr += cw + 5
        self._hot[row] = hot

    def _paint_status(self, p: QPainter, status: str, x: float, cy: float) -> None:
        col = {"read": QColor("#67E8F9"), "failed": QColor("#FCA5A5")}.get(status, theme.on_accent(175))
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
            else:
                tick(-1)
                tick(4)

    def _paint_file(self, p: QPainter, it: dict, L: dict, rect: QRectF, hot: list, row: int) -> None:
        m, out, tr = it["msg"], it["out"], it.get("transfer")
        extra = m.get("extra") if isinstance(m.get("extra"), dict) else {}
        cx = rect.left() + self.PAD_X
        if "thumb" in L:
            tw, th = L["thumb_size"]
            tr_ = QRectF(cx, rect.top() + L["thumb_y"], tw, th)
            clip = QPainterPath()
            clip.addRoundedRect(tr_, 12, 12)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(tr_.toRect().topLeft(), L["thumb"])
            p.restore()
            hot.append((tr_.toRect(), "open", m["mid"]))
        cy = rect.top() + L["card_y"]
        card = QRectF(cx, cy, rect.width() - 2 * self.PAD_X, L["card_h"])
        compact = "thumb" in L
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 55) if out else theme.ov(18))
        p.drawRoundedRect(card, 12, 12)
        ink_main = QColor("#FFFFFF") if out else theme.ink()
        ink_sub = theme.on_accent(175) if out else theme.TEXT_2
        if not compact:
            ic = QRectF(card.left() + 10, card.top() + 10, 40, 40)
            p.setBrush(theme.on_accent(30) if out else theme.ov(26))
            p.drawRoundedRect(ic, 10, 10)
            p.drawPixmap(int(ic.left() + 10), int(ic.top() + 10), icons.pixmap("file", "#FFFFFF" if out else theme.icon_hex(1), 20))
            tx = ic.right() + 10
        else:
            tx = card.left() + 12
        sfm = QFontMetrics(self.small)
        fm = QFontMetrics(self.font)
        tw = int(card.right() - tx - 10)
        p.setFont(self.bold)
        p.setPen(ink_main)
        top_off = 20 if compact else 25
        p.drawText(QPointF(tx, card.top() + top_off), fm.elidedText(extra.get("name", m["body"] or "fichier"), Qt.ElideMiddle, tw))
        state = tr["state"] if tr else ("done" if out else "failed")
        size = human_size(int(extra.get("size", 0)))
        label = {"offered": "Proposé" if not out else "En attente d'acceptation", "receiving": "Réception…", "sending": "Envoi…",
                 "paused": "En pause", "done": "Terminé" if not out else "Envoyé", "declined": "Refusé", "failed": "Échec",
                 "cancelled": "Annulé"}.get(state, "")
        p.setFont(self.small)
        p.setPen(ink_sub)
        p.drawText(QPointF(tx, card.top() + top_off + 18), f"{size} · {label}")
        if tr and state in ("receiving", "sending", "paused"):
            pb = QRectF(card.left() + 10, card.bottom() - 14, card.width() - 96, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.on_accent(40) if out else theme.ov(35))
            p.drawRoundedRect(pb, 2.5, 2.5)
            frac = it.get("progress", 0.0) if tr["direction"] == "out" else min(1.0, tr["next_idx"] / max(1, tr["nchunks"]))
            p.setBrush(QColor("#FFFFFF") if out else theme.accent(1))
            p.drawRoundedRect(QRectF(pb.left(), pb.top(), pb.width() * frac, pb.height()), 2.5, 2.5)
            p.setPen(ink_sub)
            p.drawText(QRectF(pb.right() + 6, pb.top() - 7, 40, 18), Qt.AlignLeft | Qt.AlignVCenter, f"{int(frac * 100)} %")
            cr = QRect(int(card.right() - 54), int(card.bottom() - 21), 46, 18)
            p.setPen(QPen(theme.on_accent(150) if out else theme.ov(60), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cr, 9, 9)
            p.setPen(ink_main)
            p.drawText(cr, Qt.AlignCenter, "Annuler")
            hot.append((cr, "cancel", m["mid"]))
        if tr and not out and state == "offered":
            for i, (txt, name, primary) in enumerate((("Accepter", "accept", True), ("Refuser", "decline", False))):
                br = QRect(int(card.left() + 10 + i * 96), int(card.bottom() - 36), 88, 28)
                p.setPen(QPen(theme.accent(3, 120), 1))
                p.setBrush(theme.accent(0, 200) if primary else theme.ov(18))
                p.drawRoundedRect(br, 10, 10)
                p.setPen(QColor("white") if primary else theme.ink())
                p.setFont(self.bold)
                p.drawText(br, Qt.AlignCenter, txt)
                hot.append((br, name, m["mid"]))
        if tr and out and state == "offered":
            cr = QRect(int(card.right() - 78), int(card.bottom() - 30), 68, 22)
            p.setPen(QPen(theme.on_accent(150), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(cr, 11, 11)
            p.setPen(ink_main)
            p.setFont(self.small)
            p.drawText(cr, Qt.AlignCenter, "Annuler")
            hot.append((cr, "cancel", m["mid"]))
        if tr and state == "done" and "thumb" not in L:
            hot.append((card.toRect(), "open", m["mid"]))
        if tr and not out and state in ("failed", "paused"):
            hot.append((card.toRect(), "accept", m["mid"]))

    # -- interaction ---------------------------------------------------------------------------------------
    def hotspot_at(self, row: int, pos) -> tuple[str, str] | None:
        for rect, name, mid in self._hot.get(row, ()):
            if rect.contains(pos):
                return name, mid
        return None

    def doc_at(self, row: int, pos):
        entry = self._docs.get(row)
        if entry and entry[1].adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
            return entry
        return None

    def anchor_at(self, row: int, pos, item_rect: QRect) -> str | None:
        entry = self.doc_at(row, pos)
        if not entry:
            return None
        doc, r, _ = entry
        a = doc.documentLayout().anchorAt(QPointF(pos) - r.topLeft())
        return a or None

    def text_pos(self, row: int, pos) -> tuple[str, int] | None:
        entry = self.doc_at(row, pos)
        if not entry:
            return None
        doc, r, mid = entry
        return mid, doc.documentLayout().hitTest(QPointF(pos) - r.topLeft(), Qt.FuzzyHit)


class ChatView(QListView):
    action = Signal(str, str)
    link = Signal(str)
    context = Signal(str, object)
    load_more = Signal()
    scrolled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = ChatModel(self)
        self.setModel(self.model_)
        self.highlight = ""
        self.sel_mid: str | None = None
        self.sel_a = self.sel_b = 0
        self._dragging = False
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
        self.scrolled.emit()
        if v <= 40 and self.model_.rowCount() and not self._loading:
            self.load_more.emit()

    def at_bottom(self) -> bool:
        sb = self.verticalScrollBar()
        return sb.value() >= sb.maximum() - 60

    def scroll_bottom(self) -> None:
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def scroll_to_mid(self, mid: str, center: bool = True) -> None:
        row = self.model_.row_of(mid)
        if row >= 0:
            hint = QAbstractItemView.ScrollHint.PositionAtCenter if center else QAbstractItemView.ScrollHint.PositionAtTop
            QTimer.singleShot(60, lambda: self.scrollTo(self.model_.index(row), hint))

    def resizeEvent(self, e) -> None:
        self.delegate.clear_cache()
        super().resizeEvent(e)
        self.scheduleDelayedItemsLayout()

    # -- sélection de texte -----------------------------------------------------------------------
    def clear_selection(self) -> None:
        if self.sel_mid:
            self.sel_mid, self.sel_a, self.sel_b = None, 0, 0
            self.viewport().update()

    def selected_text(self) -> str:
        if not self.sel_mid or self.sel_a == self.sel_b:
            return ""
        row = self.model_.row_of(self.sel_mid)
        entry = self.delegate._docs.get(row)
        if not entry:
            return ""
        a, b = sorted((self.sel_a, self.sel_b))
        return entry[0].toPlainText()[a:b]

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.Copy) and self.selected_text():
            QGuiApplication.clipboard().setText(self.selected_text())
            return
        super().keyPressEvent(e)

    def _index_item(self, pos):
        idx = self.indexAt(pos)
        return idx, (idx.data(ITEM_ROLE) if idx.isValid() else None)

    def mouseMoveEvent(self, e) -> None:
        pt = e.position().toPoint()
        idx, it = self._index_item(pt)
        cursor = Qt.ArrowCursor
        if it and it["type"] == "msg":
            if self.delegate.hotspot_at(idx.row(), pt) or self.delegate.anchor_at(idx.row(), pt, self.visualRect(idx)):
                cursor = Qt.PointingHandCursor
            elif self.delegate.doc_at(idx.row(), pt):
                cursor = Qt.IBeamCursor
        self.viewport().setCursor(cursor)
        if self._dragging and self.sel_mid and it and it.get("mid") == self.sel_mid:
            tp = self.delegate.text_pos(idx.row(), pt)
            if tp and tp[0] == self.sel_mid:
                self.sel_b = tp[1]
                self.viewport().update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        pt = e.position().toPoint()
        self._press = pt
        if e.button() == Qt.LeftButton:
            idx, it = self._index_item(pt)
            tp = self.delegate.text_pos(idx.row(), pt) if it and it["type"] == "msg" else None
            if tp:
                self.sel_mid, self.sel_a, self.sel_b = tp[0], tp[1], tp[1]
                self._dragging = True
            else:
                self.clear_selection()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        pt = e.position().toPoint()
        idx, it = self._index_item(pt)
        entry = self.delegate.doc_at(idx.row(), pt) if it and it["type"] == "msg" else None
        if entry:
            doc, r, mid = entry
            pos = doc.documentLayout().hitTest(QPointF(pt) - r.topLeft(), Qt.FuzzyHit)
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.select(QTextCursor.WordUnderCursor)
            self.sel_mid, self.sel_a, self.sel_b = mid, cur.selectionStart(), cur.selectionEnd()
            self._dragging = False
            self.viewport().update()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        pt = e.position().toPoint()
        self._dragging = False
        if e.button() == Qt.LeftButton and self._press and (pt - self._press).manhattanLength() < 6:
            self.clear_selection()
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
        self.clear_selection()
        self.delegate.clear_cache()
        self.model_.reset(items)
        QTimer.singleShot(150, lambda: setattr(self, "_loading", False))
