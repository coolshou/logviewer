#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Oct 19 22:30:31 2017

@author: coolshou
"""
import sys
import os
import csv
#import fcntl
import time
import asyncio
#import aiofiles
import subprocess
import shlex
from concurrent.futures import ThreadPoolExecutor
io_pool_exc = ThreadPoolExecutor(max_workers=1)

from PyQt5.QtCore import (QThread, pyqtSignal, pyqtSlot, QObject, QSettings)
from PyQt5.QtWidgets import(QApplication, QMainWindow, QWidget,
                            QVBoxLayout, QPushButton, QTextEdit, QFileDialog,
                            QTableWidgetItem)
from PyQt5.uic import loadUi
import signal

from threading import Thread
from xmlrpc.server import SimpleXMLRPCServer
from xmlrpc.client import ServerProxy
import xmlrpc

def trap_exc_during_debug(*args):
    # when app raises uncaught exception, print info
    print(args)
# install exception hook: without this, uncaught exception would cause application to exit
sys.excepthook = trap_exc_during_debug

def signal_term_handler(signal, frame): 
    print(signal_term_handler)
    sys.exit(0) 

#Ctrl+c
signal.signal(signal.SIGINT, signal.SIG_DFL)
#signal.signal(signal.SIGINT, signal_term_handler)

class myServiceServer(Thread):
    def __init__(self, ip, port):
        #super(myServiceServer, self).__init__()
        self.running = True
        self._Host = ip
        self._Port = port
        #self.server = SimpleXMLRPCServer((self._Host, self._Port),  allow_none = True)
        self.server = SimpleXMLRPCServer((self._Host, self._Port),  
                                         logRequests=False, 
                                         allow_none = True)
        self.server.register_introspection_functions()

        Thread.__init__(self)
        
    def register_function(self, function):
        self.server.register_function(function)

    def run(self):

        self.server.serve_forever()

    def stop_server(self):
        self.server.shutdown()
        #self.server.server_close()
        
class log_viewer(QObject):
    __PERIOD = 0.5
    sig_done = pyqtSignal(int, int)  # worker id: emitted at end of work(), errorcode
    sig_msg = pyqtSignal(str)  # message to be shown to user
    
    TAIL = '/usr/bin/tail'
    #SYSLOG_FILE = '/var/log/syslog'
    
    def __init__(self, idx: int, filename:str):
        QThread.__init__(self)
        self.__id = idx
        self.syslog_file = filename
        self.syslog = self._create_pipe()
        #self.syslog_file = open(filename, 'r')
        #fd = self.syslog_file.fileno()
        #flag = fcntl.fcntl(fd, fcntl.F_GETFL)
        #fcntl.fcntl(fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)
        #flag = fcntl.fcntl(fd, fcntl.F_GETFL)
        #if flag & os.O_NONBLOCK:
        #    print("O_NONBLOCK!!")
        self.__abort = False
        self.loop = asyncio.get_event_loop()

    def _create_pipe(self):
        """        create_pipe() -> subprocess.Popen        """
        cmd = shlex.split(self.TAIL + ' -f ' + self.syslog_file)
        return subprocess.Popen(cmd, stdout=subprocess.PIPE)
    
    def _dispatch(self, message):
        """        dispatch(str) -> (str, str)        """
        #if message.find("GPU hung".encode()) != -1:
        #    return self._gpu_hung_event(message)
        #print("_dispatch %s" % message)
        return ('message', message)
    
    def _handle(self, message):
        """        handle(str) -> None        """
        #now = datetime.datetime.now()
        (event, payload) = self._dispatch(message)
        if event is not None:
            #print("_handle %s %s (%s)" % (event, payload, type(payload) ))
            if type(payload) is bytes:
                m = payload.decode()
            else:
                m = payload
            self.sig_msg.emit(m)
 
    @pyqtSlot()
    def work(self):
        if self.__abort:
            self.__abort = False #clear abort
            
        line = self.syslog.stdout.readline()
        while line is not None and line != "" and (self.__abort is False):
            #TODO: still block at here, can not abort by self.__abort?
            line = self.syslog.stdout.readline()
            self._handle(line)
            # check if we need to abort the loop; need to process events to receive signals;
            QApplication.processEvents()  # this could cause change to self.__abort
            if self.__abort:
                # note that "step" value will not necessarily be same for every thread
                self.sig_done.emit(self.__id, 1)
                self.sig_msg.emit('Worker #{} aborting work '.format(self.__id))
                break

        self.sig_done.emit(self.__id, 0)
   
    def abort(self):
        self.sig_msg.emit('Worker #{} notified to abort'.format(self.__id))
        self.__abort = True
        
class MyThread(QThread):
    def run(self):
        self.exec_()

twDataCol ={'phase':0,
            'idx':1,
            'data':2 }

class main(QMainWindow):
    NUM_THREADS = 1
    sig_abort_workers = pyqtSignal()
    
    def __init__(self, Parent=None):
        super(main,self).__init__(Parent)
        self.settings = QSettings("logviewer.ini", QSettings.IniFormat)
        self.settings.setFallbacksEnabled(False)    # File only, not registry or or.
        
        self.syslogfile= os.path.join("/","var","log","syslog")
        loadUi("logviewer.ui", self)
        
        self.setWindowTitle("Thread logviewer")
        #
        self.myService = None
        self.pbBind.clicked.connect(self.startServer)
        self.pbTestPathbPhase.clicked.connect(self.testPathbPhase)
        self.pbTestRateCtl.clicked.connect(self.testRateCtl)
        #
        self.actionExit.triggered.connect(self.saveExit)
        self.pbSelectSource.clicked.connect(self.selectSource)
        self.pbStartThread.clicked.connect(self.start_threads)
        #self.pbStartThread.setText("Start {} threads".format(self.NUM_THREADS))
        self.pbStopThread.clicked.connect(self.abort_workers)
        #self.pbSetFilter.clicked.connect(self.setFilter)
        self.pbSaveData.clicked.connect(self.saveData)
        self.leFilter.editingFinished.connect(self.setFilter)
        self.leServerIP.editingFinished.connect(self.setServerIP)
        #

        
        QThread.currentThread().setObjectName('main')  # threads can be named, useful for log output
        self.__workers_done = None
        self.__threads = None
    
        self.__sFilter = ''
        self.__ServerIP = ''
        
        self.ifound = 0
        self.currentPhase = 0
        self.phase = []
        self._Proxy = None
        self.loadSetting()
        
    def closeEvent(self, e):
        print("close event %s" % type(e))
#        self.abort_workers()
        if self.myService:
            self.myService.stop_server()
            self.myService.join()
            
    def loadSetting(self):
        setting = self.settings
        self.syslogfile= setting.value("sourcefile", os.path.join("/","var","log","syslog"))
        self.__sFilter = setting.value("filter", "sumall")
        self.__ServerIP = setting.value("ServerIP", "127.0.0.1")

        self.setUI()
        
    def saveExit(self):
        self.saveSetting()
        self.close()
        
    def saveSetting(self):
        setting = self.settings
        setting.setValue("sourcefile", self.syslogfile)
        setting.setValue("filter", self.__sFilter)
        setting.setValue("ServerIP", self.__ServerIP)
        
    def setUI(self):
        self.leSourceFile.setText(self.syslogfile)
        self.leFilter.setText(self.__sFilter)
        self.leServerIP.setText(self.__ServerIP)
        
    @pyqtSlot()    
    def setFilter(self):
        t = self.leFilter.text()
        self.__sFilter = t
    
    @pyqtSlot()          
    def setServerIP(self):
        t = self.leServerIP.text()
        self.__ServerIP = t
        
    @pyqtSlot(str)
    def logHandle(self, msg):
        if msg:
            m = msg.strip()
            if self.__sFilter in m:
                self.ifound = self.ifound + 1
                if self.ifound == self.iIgnore:
                    #ignore this
                    pass
                elif self.ifound <= self.iTotalCount:
                    self.log.append("%s================> %s" % (self.ifound, m))
                    self.addData(self.ifound, m)
                else:
                    self.ifound = 0
                    self.currentPhase = self.currentPhase + 1
                    #TODO: issue command
                    if self.currentPhase < len(self.phase):
                        if self._Proxy:
                            self._Proxy.setPathb_phase(self.phase[self.currentPhase])
                    else:
                        self.abort_workers()
                        self.log.append("********************FINISH**************************")
            else:
                self.log.append(m)
                
    def saveData(self):
        filename , _filter = QFileDialog.getSaveFileName(
                self, 'Save File', '', 'CSV(*.csv)')
        print("path: %s" % filename)
        if filename is not "":
            self.log.append("#################save data###################")
            with open(filename, 'w') as stream:
                writer = csv.writer(stream, delimiter=',')
                for row in range(self.twData.rowCount()):
                    rowdata = []
                    for column in range(self.twData.columnCount()):
                        item = self.twData.item(row, column)
                        if item is not None:
                            rowdata.append("%s" % item.text())
                        else:
                            rowdata.append('')
                    self.log.append("%s = %s" %(row, rowdata))
                    writer.writerow(rowdata)
                self.log.append("rowdata: %s" % rowdata)
            
    def addData(self, idx, m):
        iRow = self.twData.rowCount()
        #print("row %s" % iRow)
        self.twData.setRowCount(iRow+1)

        newItem = QTableWidgetItem(self.phase[self.currentPhase])
        self.twData.setItem(iRow, twDataCol['phase'], newItem)
        #
        newItem = QTableWidgetItem(str(idx))
        self.twData.setItem(iRow, twDataCol['idx'], newItem)
        #
        newItem = QTableWidgetItem(m)
        self.twData.setItem(iRow, twDataCol['data'], newItem)
        
    @pyqtSlot()
    def selectSource(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,"select source", 
                                                  os.path.join("/","var", "log"),
                                                  "All Files (*)", options=options)
        if fileName:
            self.leSourceFile.setText(fileName)
            
    @pyqtSlot()
    def start_threads(self):
        self.log.append('starting {} threads'.format(self.NUM_THREADS))
        self.updateUIbtn(False)
        
        self.currentPhase = 0
        self.phase = self.lePhase.text().split(',')
        self.ifound = 0
        self.iIgnore = self.sbIgnore.value()
        self.iTotalCount = self.sbCount.value() + self.iIgnore
        
        self.__workers_done = 0
        self.__threads = []
        for idx in range(self.NUM_THREADS):
            print("self.syslogfile: %s" %self.syslogfile)
            worker = log_viewer(idx, self.syslogfile)
            thread = MyThread()
            thread.setObjectName('thread_' + str(idx))
            self.__threads.append((thread, worker))  # need to store worker too otherwise will be gc'd
            worker.moveToThread(thread)

            # get progress messages from worker:
            #worker.sig_step.connect(self.on_worker_step)
            worker.sig_done.connect(self.on_worker_done)
            worker.sig_msg.connect(self.logHandle)

            # control worker:
            self.sig_abort_workers.connect(worker.abort)

            # get read to start worker:
            # self.sig_start.connect(worker.work)  # needed due to PyCharm debugger bug (!); comment out next line
            thread.started.connect(worker.work)
            thread.start()  # this will emit 'started' and start thread's event loop
        
        #issue first command
        if self._Proxy:
            self._Proxy.setRate_ctl(self.leRateCtl.text())
            self._Proxy.setPathb_phase(self.phase[self.currentPhase])

    def startServer(self):
        #xmlrpcserver
        if not self.myService:
            self.myService = myServiceServer(self.leServerIP.text(), self.sbPort.value())
            self.myService.register_function(self.setPathb_phase)
            self.myService.register_function(self.setRate_ctl)
            self.myService.register_function(self.getPathb_phase)
            
        self.myService.start()
        self.pbBind.setEnabled(False)
        
    def connectServerProxy(self):
        try:
            print("connectServerProxy")
            self._Proxy = ServerProxy('http://%s:%s' %(self.__ServerIP, self.sbPort.value())) 
            print("connectServerProxy: %s" % self._Proxy)
        except Exception as err:
            print("A fault occurred: %s" % type(err))
            print(err)
            #print("Fault code: %d" % err.faultCode)
            #print("Fault string: %s" % err.faultString)
        if self._Proxy:
            self.log.append("%s" % self._Proxy.system.listMethods())
        
    def testPathbPhase(self):
        if not self._Proxy:
            self.connectServerProxy()
            
        phase = self.cbPhase.currentText()
        self.log.append("set PathbPhase = %s (%s)" % (phase, type(phase)))
        self._Proxy.setPathb_phase(phase)
        rs = self._Proxy.getPathb_phase()
        self.log.append("getPathb_phase = %s" % rs)
    
    def testRateCtl(self):
        if not self._Proxy:
            self.connectServerProxy()
        rate = self.cbRateCtl.currentText()
        rs = self._Proxy.setRate_ctl(rate)
        self.log.append("testRateCtl = %s" % rs)
        
    def setRate_ctl(self, rate):
        #echo 0x2C > /proc/net/rtl88x2bu/wlan3/rate_ctl ;cat /proc/net/rtl88x2bu/wlan3/rate_ctl
        path = self.leRateCtlPath.text()
        cmd = shlex.split('echo %s > %s; cat %s' % (rate, path, path ))
        #cmd = shlex.split('ls -l')
        try:
            rs = subprocess.check_output(cmd, 
                                         stderr=subprocess.STDOUT, shell=False)
        except subprocess.CalledProcessError:
            print('setRate_ctl Exception: %s' % cmd)
        return rs
        
    def setPathb_phase(self, phase):
        #echo 0 > /proc/net/rtl88x2bu/wlan3/pathb_phase; cat /proc/net/rtl88x2bu/wlan3/pathb_phase ;cat /proc/net/rtl88x2bu/wlan3/rate_ctl
        #rPath = self.leRateCtlPath.text()
        print("setPathb_phase - 1")
        pPath = self.lePathbPhasePath.text()
        #cmd = shlex.split('echo %s > %s; cat %s; cat %s' % (phase, pPath, pPath, rPath ))
        cmd = shlex.split('echo %s > %s' % (phase, pPath))
        #self.log.append("%s" % cmd)
        print("setPathb_phase - 2: %s" % cmd)
        try:
            rs = subprocess.check_output(cmd,
                                         stderr=subprocess.STDOUT, shell=False)
        except subprocess.CalledProcessError:
            print('setPathb_phase Exception: %s' % cmd)
        print("setPathb_phase - 3")
        return rs
    
    def getPathb_phase(self):
        #echo 0 > /proc/net/rtl88x2bu/wlan3/pathb_phase; cat /proc/net/rtl88x2bu/wlan3/pathb_phase ;cat /proc/net/rtl88x2bu/wlan3/rate_ctl
        #rPath = self.leRateCtlPath.text()
        pPath = self.lePathbPhasePath.text()
        #cmd = shlex.split('echo %s > %s; cat %s; cat %s' % (phase, pPath, pPath, rPath ))
        cmd = shlex.split('cat %s ' % (pPath))
        try:
            rs = subprocess.check_output(cmd,
                                         stderr=subprocess.STDOUT, shell=False)
        except subprocess.CalledProcessError:
            print('setPathb_phase Exception: %s' % cmd)
        return rs        
    
    def updateUIbtn(self, e):
        self.pbStartThread.setEnabled(e)
        self.pbStopThread.setEnabled(not e)
        self.setDisableUI(not e)
        
    @pyqtSlot(int, int)
    def on_worker_done(self, worker_id, errCode):
        print("worker_id: %s %s" % (worker_id, errCode))
        #self.__workers_done += 1
        #if self.__workers_done == self.NUM_THREADS:
        #    self.log.append('No more workers active')
        self.updateUIbtn(True)
        self.log.append('worker #{} done %s'.format(worker_id, errCode))
                        
    @pyqtSlot()
    def abort_workers(self):
        self.sig_abort_workers.emit()
        self.log.append('Asking each worker to abort')
        for thread, worker in self.__threads:  # note nice unpacking by Python, avoids indexing
            thread.quit()  # this will quit **as soon as thread event loop unblocks**
            thread.wait()  # <- so you need to wait for it to *actually* quit

        # even though threads have exited, there may still be messages on the main thread's
        # queue (messages that threads emitted before the abort):
        self.log.append('All threads exited')
        self.updateUIbtn(True)
        
        
    def setDisableUI(self, e):
        self.widgetFilter.setEnabled(not e)
        self.widgetCmd.setEnabled(not e)
        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    m = main()
    m.show()
    sys.exit(app.exec_())
