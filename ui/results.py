import os, os.path, subprocess

from PySide import QtCore, QtDeclarative, QtGui
from PySide.QtCore import Qt

import config, magnet
config = config.read()

from bithorde import QueryThread, message
bithorde_querier = QueryThread()

from presentation import default, movies, series
from editor import ItemEditor

PRESENTATIONS = (series.Presentation, movies.Presentation, default.Presentation)

BHFUSE_MOUNT = config.get('BITHORDE', 'fusedir')
BITHORDE_PRESSURE = int(config.get('BITHORDE', 'pressure'))
HERE = os.path.dirname(__file__)

def fuseForAsset(asset):
    magnetUrl = magnet.fromDbObject(asset)
    return os.path.join(BHFUSE_MOUNT, magnetUrl)

class ResultList(QtCore.QAbstractListModel):
    ObjRole = Qt.UserRole
    TagsRole = Qt.UserRole + 1
    ImageURIRole = Qt.UserRole + 2

    def __init__(self, parent, results, db):
        self._unfiltered = iter(results)
        self._list = list()
        self._db = db
        self._desiredRequests = 0
        QtCore.QAbstractListModel.__init__(self, parent)
        self.setRoleNames({
            Qt.DisplayRole: "title",
            Qt.DecorationRole: "categoryIcon",
            self.TagsRole: "tags",
            self.ImageURIRole: "imageUri",
            self.ObjRole: "obj",
        })

    def mapObjToView(self, objid):
        obj = self._db[objid]
        for x in PRESENTATIONS:
            if obj.matches(x.CRITERIA):
                return x(obj)
        assert False

    def signalChanged(self, item):
        for i,x in enumerate(self._list):
            if x == item:
                self.dataChanged.emit(self.createIndex(i,0), self.createIndex(i,0))

    def canFetchMore(self, _):
        return self._desiredRequests < BITHORDE_PRESSURE

    def fetchMore(self, _):
        self._desiredRequests = BITHORDE_PRESSURE
        self._tryFetch()

    def _tryFetch(self):
        try:
            while self._desiredRequests > 0:
                id = self._unfiltered.next()
                if not id.startswith('tree:tiger:'):
                    continue
                bithorde_querier(id[len('tree:tiger:'):], self._queueAppend, id)

                self._desiredRequests -= 1
        except StopIteration:
            pass

    def _queueAppend(self, asset, status, assetid):
        if status.status == message.SUCCESS:
            event = QtCore.QEvent(QtCore.QEvent.User)
            event.assetid = assetid
            QtCore.QCoreApplication.postEvent(self, event)
        else:
            self._desiredRequests += 1
        self._tryFetch()

    def event(self, event):
        if event.type()==QtCore.QEvent.User and hasattr(event, 'assetid'):
            self._append(event.assetid)
            return True
        return QtCore.QAbstractListModel.event(self, event)

    def _append(self, assetid):
        pos = len(self._list)
        viewItem = self.mapObjToView(assetid)
        self.beginInsertRows(QtCore.QModelIndex(), pos, pos)
        self._list.append(viewItem)
        self.endInsertRows()

    def rowCount(self, _):
        return len(self._list)

    def data(self, idx, role):
        obj = self._list[idx.row()]
        if role == Qt.DisplayRole:
            return obj.title
        if role == Qt.DecorationRole:
            return obj.categoryIcon
        if role == self.TagsRole:
            return obj.tags
        if role == self.ImageURIRole:
            return obj.imageUri
        if role == self.ObjRole:
            return obj
        return obj.title

class ResultsView(QtDeclarative.QDeclarativeView):
    SORT_TIME, SORT_TITLE = range(2)
    def __init__(self, parent, db):
        QtDeclarative.QDeclarativeView.__init__(self, parent)
        self.db = db

        self.setResizeMode(self.SizeRootObjectToView)
        self.setStyleSheet("background:transparent;")
        self.rootContext().setContextProperty("myModel", [])
        self.setSource(QtCore.QUrl(os.path.join(HERE, "qml", "Results.qml")))

        for vis in PRESENTATIONS:
            vis.loadComponents(self.engine())

        self.rootObject().runAsset.connect(self.runAsset)
        self.rootObject().editAsset.connect(self.editAsset)
        self.dragStart = None

    def _fetch_title(self, x):
        title = self.db.get_attr(x, 'title')
        title = title and title.any()
        return title or self.db.get_attr(x, 'name').any()

    def setSortKey(self, key):
        if key == self.SORT_TIME:
            self._sort_key = dict(key=self.db.get_mtime, reverse=True)
        elif key == self.SORT_TITLE:
            self._sort_key = dict(key=self._fetch_title, reverse=False)
        else:
            assert False, "Unknown sort-key"

        if hasattr(self, '_criteria'):
            self.refresh(self._criteria)

    def refresh(self, criteria):
        if criteria:
            assets = self.db.query_ids(criteria)
        else:
            assets = self.db.all_ids()
        self._criteria = criteria

        assets = sorted(assets, **self._sort_key)

        bithorde_querier.clear()
        self.model = model = ResultList(self, assets, self.db)
        self.rootContext().setContextProperty("myModel", model)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragStart = event.pos()
        QtDeclarative.QDeclarativeView.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        if self.dragStart and event.buttons() & Qt.LeftButton:
            distance = (event.pos() - self.dragStart).manhattanLength()
            if distance >= QtGui.QApplication.startDragDistance():
                item = self.itemAt(self.dragStart)
                self.dragStart = None
                obj = None

                while item:
                    if hasattr(item, 'property'):
                        obj = item.property('itemObj')
                        if obj:
                            break
                    item = item.parentItem()

                if obj:
                    drag = QtGui.QDrag(self);
                    mimeData = QtCore.QMimeData()
                    mimeData.setUrls([QtCore.QUrl(fuseForAsset(obj.asset))])
                    drag.setMimeData(mimeData)
                    dropAction = drag.exec_(Qt.CopyAction | Qt.LinkAction)
        QtDeclarative.QDeclarativeView.mouseMoveEvent(self, event)

    def runAsset(self, guiitem):
        asset = guiitem.asset
        subprocess.Popen(['xdg-open', fuseForAsset(asset)])

    def editAsset(self, guiitem):
        asset = guiitem.asset
        edit = ItemEditor(self.parent(), self.db, self.model, guiitem)
