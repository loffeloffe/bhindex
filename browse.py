#!/usr/bin/env python
# -*- coding: utf-8 -*-

from PySide import QtGui, QtCore, QtDeclarative
from PySide.QtCore import Qt

import os, os.path as path, sys, time, signal
from ConfigParser import ConfigParser
from optparse import OptionParser
from threading import Thread
from time import sleep
import subprocess

HERE = path.dirname(__file__)
sys.path.append(HERE)

import db, config, magnet
config = config.read()

import bithorde
class QueryThread(bithorde.Client, Thread):
    def run(self):
        bithorde.connectUNIX("/tmp/bithorde", self)
        bithorde.reactor.run(installSignalHandlers=0)

    def onConnected(self):
        self._querier = bithorde.Querier(self, self._callback)

    def _callback(self, asset, status, key):
        callback, key = key
        callback(key)

    def start(self):
        Thread.start(self)
        while not hasattr(self, '_querier'):
            sleep(0.05)

    def __call__(self, tiger_id, callback, key):
        self._querier.submit({bithorde.message.TREE_TIGER: bithorde.b32decode(tiger_id)}, (callback, key))

    def clear(self):
        self._querier.clear()

bithorde_querier = QueryThread()
bithorde_querier.daemon = True
bithorde_querier.start()

from presentation import default, movies, series

PRESENTATIONS = (series.Presentation, movies.Presentation, default.Presentation)

class FilterRule(QtGui.QWidget):
    onChanged = QtCore.Signal()

    def __init__(self, parent, db, keys):
        QtGui.QWidget.__init__(self, parent)
        self.db = db
        layout = self.layout = QtGui.QVBoxLayout(self)

        keybox = self.keybox = QtGui.QComboBox(self)
        keybox.addItem('- filter key -', userData=None)
        for key in keys:
            keybox.addItem(key, userData=key)
        keybox.currentIndexChanged.connect(self.onKeyChanged)
        layout.addWidget(keybox)

        valuebox = self.valuebox = QtGui.QComboBox(self)
        self.populateValuesForKey(None)
        valuebox.currentIndexChanged.connect(lambda _: self.onChanged.emit())
        layout.addWidget(valuebox)

    def populateValuesForKey(self, key):
        self.valuebox.clear()
        self.valuebox.addItem('- is present -', userData=self.db.ANY)
        if key:
            for value in self.db.list_values(key):
                self.valuebox.addItem(value, userData=value)

    @QtCore.Slot(unicode)
    def onKeyChanged(self, key):
        if isinstance(key, int):
            key = self.keybox.itemText(key)
        self.populateValuesForKey(key)
        self.onChanged.emit()

    def getRule(self):
        vb = self.valuebox
        value = vb.itemData(vb.currentIndex())
        return self.getKey(), value

    def getKey(self):
        kb = self.keybox
        return kb.itemData(kb.currentIndex())

class FilterList(QtGui.QToolBar):
    onChanged = QtCore.Signal()

    def __init__(self, parent, db):
        QtGui.QToolBar.__init__(self, "Filter", parent)
        self.db = db
        self.keys = [k for k,c in db.list_keys()]
        self.addFilter()

    def addFilter(self):
        rule = FilterRule(self, self.db, self.keys)
        rule.onChanged.connect(self._onRuleChanged)
        self.addWidget(rule)

    def criteria(self):
        res = {}
        for c in self.children():
            if isinstance(c, FilterRule):
                k,v = c.getRule()
                if k:
                    res[k] = v
        return res

    def _onRuleChanged(self):
        empty = 0
        for c in self.children():
            if isinstance(c, FilterRule):
                if not c.getKey():
                    empty += 1
        if not empty:
            self.addFilter()
        self.onChanged.emit()

BHFUSE_MOUNT = config.get('BITHORDE', 'fusedir')
def fuseForAsset(asset):
    magnetUrl = magnet.fromDbObject(asset)
    return os.path.join(BHFUSE_MOUNT, magnetUrl)

def mapItemToView(item):
    for x in PRESENTATIONS:
        if item.matches(x.CRITERIA):
            return x(item)
    assert False

class ResultList(QtCore.QAbstractListModel):
    ObjRole = Qt.UserRole
    TagsRole = Qt.UserRole + 1
    ImageURIRole = Qt.UserRole + 2

    def __init__(self, parent, results):
        self._unfiltered = iter(results)
        self._list = list()
        QtCore.QAbstractListModel.__init__(self, parent)
        self.setRoleNames({
            Qt.DisplayRole: "title",
            Qt.DecorationRole: "categoryIcon",
            self.TagsRole: "tags",
            self.ImageURIRole: "imageUri",
            self.ObjRole: "obj",
        })

    def canFetchMore(self, _):
        return bool(self._unfiltered)

    def fetchMore(self, _):
        i = 0
        while self._unfiltered and i < 5:
            asset = self._unfiltered.next()
            id = asset.get('xt', '')
            id = id and id.any()
            if not id.startswith('tree:tiger:'):
                continue
            bithorde_querier(id[len('tree:tiger:'):], self._queueAppend, asset)
            i += 1

    def _queueAppend(self, db_asset):
        event = QtCore.QEvent(QtCore.QEvent.User)
        event.asset = db_asset
        QtCore.QCoreApplication.postEvent(self, event)

    def event(self, event):
        if event.type()==QtCore.QEvent.User and hasattr(event, 'asset'):
            self._append(mapItemToView(event.asset))
            return True
        return QtDeclarative.QDeclarativeView.event(self, event)

    def _append(self, val):
        pos = len(self._list)
        self.beginInsertRows(QtCore.QModelIndex(), pos, pos)
        self._list.append(val)
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
    KEY_BLACKLIST = ('xt', 'path', 'filetype')
    def __init__(self, parent, db):
        QtDeclarative.QDeclarativeView.__init__(self, parent)
        self.db = db

        self.setResizeMode(self.SizeRootObjectToView)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background:transparent;");
        self.rootContext().setContextProperty("myModel", [])
        self.setSource(QtCore.QUrl("results.qml"))

        for vis in PRESENTATIONS:
            vis.loadComponents(self.engine())

        self.rootObject().runAsset.connect(self.runAsset)
        self.dragStart = None

    def refresh(self, criteria):
        if criteria:
            assets = self.db.query(criteria)
        else:
            assets = self.db.all()
        assets = sorted(assets, key=lambda x: x['name'].any())

        bithorde_querier.clear()
        self.model = model = ResultList(self, assets)
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
                        obj = item.property('itemData')
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

if __name__=='__main__':
    parser = OptionParser(usage="usage: %prog [options] <PATH>")

    (options, args) = parser.parse_args()
    if len(args)>1:
        parser.error("Only one path-argument supported")
    elif args:
        path=db.path_str2lst(args[0])
    else:
        path=[]

    thisdb = db.open(config)

    QtGui.QApplication.setGraphicsSystem('raster')
    app = QtGui.QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    mainwindow = QtGui.QMainWindow()
    mainwindow.resize(600,400)
    mainwindow.show()

    def onFilterChanged():
        f = filter.criteria()
        results.refresh(f)

    filter = FilterList(mainwindow, thisdb)
    filter.onChanged.connect(onFilterChanged)
    mainwindow.addToolBar(filter)

    results = ResultsView(mainwindow, thisdb)

    results.refresh(None)
    results.show()

    mainwindow.setCentralWidget(results)

    #vlayout.addWidget(preview)
    sys.exit(app.exec_())

