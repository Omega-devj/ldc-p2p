"""Liste des conversations : avatar, présence, aperçu, heure, non-lus, épinglé/muet."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from .. import icons, theme
from .glass import draw_avatar

ROLE = Qt.UserRole + 1


def short_time(ts: int) -> str:
    d = datetime.fromtimestamp(ts / 1000)
    today = date.today()
    if d.date() == today:
        return d.strftime("%H:%M")
    if (today - d.date()).days == 1:
        return "Hier"
    if (today - d.date()).days < 7:
        return ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."][d.weekday()]
    return d.strftime("%d/%m/%y")


def preview_of(last: dict | None, mine: str, name_of, kind: str) -> str:
    if not last:
        return ""
    if last["kind"] == "system":
        return last["body"] or ""
    body = last["body"] or ""
    if last["kind"] == "file":
        body = "Fichier : " + ((last.get("extra") or {}).get("name") or body)
    prefix = "Vous : " if last["direction"] == "out" else (f"{name_of(last['sender'])} : " if kind == "group" else "")
    return prefix + body.replace("\n", " ")


class ConvModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        return self.items[index.row()] if role == ROLE and 0 <= index.row() < len(self.items) else None

    def set_items(self, items: list[dict]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class ConvDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_f = theme.base_font(10.5)
        self.title_f.setWeight(QFont.DemiBold)
        self.sub_f = theme.base_font(9.5)
        self.small_f = theme.base_font(8.5)

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 70)

    def paint(self, p: QPainter, option, index) -> None:
        it = index.data(ROLE)
        if not it:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 3, -6, -3)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if selected:
            p.setPen(QPen(theme.accent(1, 90), 1))
            p.setBrush(theme.accent(0, 62))
            p.drawRoundedRect(r, 16, 16)
        elif hover:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.ov(16))
            p.drawRoundedRect(r, 16, 16)
        av = QRectF(r.left() + 10, r.center().y() - 22, 44, 44)
        draw_avatar(p, av, it["conv"], it["title"], group=it["kind"] == "group")
        if it["kind"] == "dm" and it["state"] == "accepted":
            col = QColor(theme.PRESENCE_COLORS.get(it["presence"], "#6B7280"))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#F3F4FB") if theme.is_light() else QColor("#0C0C14"))
            p.drawEllipse(QPointF(av.right() - 5, av.bottom() - 5), 8, 8)
            p.setBrush(col)
            p.drawEllipse(QPointF(av.right() - 5, av.bottom() - 5), 5.5, 5.5)
        tx = av.right() + 12
        right = r.right() - 12
        unread = it["unread"]
        # heure
        last = it.get("last")
        p.setFont(self.small_f)
        p.setPen(theme.accent(3) if (unread and not theme.is_light()) else (theme.accent(2) if unread else theme.TEXT_3))
        time_txt = short_time(last["ts"]) if last else ""
        tw = QFontMetrics(self.small_f).horizontalAdvance(time_txt)
        p.drawText(QRectF(right - tw, r.top() + 12, tw, 18), Qt.AlignRight | Qt.AlignVCenter, time_txt)
        # titre
        p.setFont(self.title_f)
        p.setPen(theme.ink())
        tfm = QFontMetrics(self.title_f)
        icon_w = (16 if it.get("pinned") else 0) + (16 if it.get("muted") else 0)
        title = tfm.elidedText(it["title"], Qt.ElideRight, int(right - tx - tw - 10 - icon_w))
        p.drawText(QPointF(tx, r.top() + 26), title)
        ix = tx + tfm.horizontalAdvance(title) + 6
        if it.get("pinned"):
            p.drawPixmap(int(ix), int(r.top() + 14), icons.pixmap("pin", theme.icon_hex(0), 12))
            ix += 16
        if it.get("muted"):
            p.drawPixmap(int(ix), int(r.top() + 14), icons.pixmap("bell-off", theme.icon_hex(0), 12))
        # aperçu
        badge_w = 0
        if unread:
            txt = str(min(unread, 99))
            badge_w = max(22, QFontMetrics(self.small_f).horizontalAdvance(txt) + 14)
            br = QRectF(right - badge_w, r.bottom() - 30, badge_w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.accent(0) if not it.get("muted") else theme.ov(60))
            p.drawRoundedRect(br, 10, 10)
            p.setPen(QColor("white"))
            f = QFont(self.small_f)
            f.setBold(True)
            p.setFont(f)
            p.drawText(br, Qt.AlignCenter, txt)
        p.setFont(self.sub_f)
        state = it["state"]
        if state == "pending_in":
            sub, col = "Demande de contact — répondre", QColor("#FBBF24")
        elif state == "pending_out":
            sub, col = "En attente d'acceptation…", theme.TEXT_3
        elif it.get("typing"):
            sub, col = "écrit…", theme.accent(1)
        else:
            sub, col = it.get("preview", ""), (theme.ink(210) if unread else theme.TEXT_2)
        if it.get("result"):
            sub, col = it["result"], theme.TEXT_2
        p.setPen(col)
        sfm = QFontMetrics(self.sub_f)
        p.drawText(QPointF(tx, r.bottom() - 17), sfm.elidedText(sub, Qt.ElideRight, int(right - tx - badge_w - 8)))
        p.restore()


class ConvList(QListView):
    selected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = ConvModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(ConvDelegate(self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.clicked.connect(lambda i: self.selected.emit(i.data(ROLE)))
        self.current_conv: str | None = None

    def set_items(self, items: list[dict]) -> None:
        sb = self.verticalScrollBar().value()
        self.model_.set_items(items)
        self.verticalScrollBar().setValue(sb)
        self.reselect()

    def reselect(self) -> None:
        for i, it in enumerate(self.model_.items):
            if it["conv"] == self.current_conv and not it.get("result"):
                self.setCurrentIndex(self.model_.index(i))
                return
        self.clearSelection()
