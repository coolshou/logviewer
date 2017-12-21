#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Oct 19 22:30:31 2017

@author: coolshou
"""
import sys
import os
import csv
import platform
import time
from datetime import datetime
import asyncio
#import aiofiles
import subprocess
import shlex
from concurrent.futures import ThreadPoolExecutor
io_pool_exc = ThreadPoolExecutor(max_workers=1)

from PyQt5.QtCore import (QThread, pyqtSignal, pyqtSlot, QObject, QSettings)
from PyQt5.QtWidgets import(QApplication, QMainWindow, QFileDialog,
                            QTableWidgetItem, QMessageBox, QHeaderView)
#QWidget, QVBoxLayout, QPushButton, QTextEdit, 
from PyQt5.uic import loadUi
import signal

from threading import Thread
from xmlrpc.server import SimpleXMLRPCServer
from xmlrpc.client import ServerProxy
#import xmlrpc
#for ssh, require paramiko
import spur
#att
sys.path.append(os.path.join("..","attenuator"))
#from attenuator import attenuator, quant
from attenuator import attenuator


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
        
        try:
            self.att = attenuator(testMode=False)
        except  Exception as e:
            print("qRVR:%s" % str(e))
        
        if self.att:
            self.devices = self.att.getDevInfo()
            
        #UI
        header = self.twData.horizontalHeader()
        header.setStretchLastSection(True)
        #pyqsetResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        #header.setSectionResizeMode(2, QHeaderView.Stretch)
        #
        self.myService = None
        #
        self.pbBind.clicked.connect(self.startServer)
        self.pbTestPathbPhase.clicked.connect(self.testPathbPhase)
        self.pbTestRateCtl.clicked.connect(self.testRateCtl)
        #
        self.actionExit.triggered.connect(self.saveExit)
        self.pbSelectSource.clicked.connect(self.selectSource)
        self.pbStartThread.clicked.connect(self.start_threads)
        self.pbStopThread.clicked.connect(self.abort_workers)
        self.pbSaveData.clicked.connect(self.saveData)
        self.pbClearData.clicked.connect(self.clearData)
        self.leFilter.editingFinished.connect(self.setFilter)
        self.leServerIP.editingFinished.connect(self.setServerIP)
        #
        self.pbDirectBF.clicked.connect(self.startDirectBF)
        self.pbEVPathSignal.clicked.connect(self.startEVPathSignal)
        self.pbSaveEVPathSignal.clicked.connect(self.saveEVPathSignal)
        
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
                    #
                    rs = m.split("kernel: ")
                    if len(rs)==2:
                        self.addData(self.ifound, rs[1])
                else:
                    self.ifound = 0
                    self.currentPhase = self.currentPhase + 1
                    # issue command to xmlrpcserver (_Proxy)
                    if self.currentPhase < len(self.phase):
                        print("current phase %s" % self.currentPhase)
                        if not self._Proxy:
                            self.connectServerProxy()
                        if self._Proxy:
                            print("send phase to proxy: %s" % self.phase[self.currentPhase])
                            self._Proxy.setPathb_phase(self.phase[self.currentPhase])
                    else:
                        self.abort_workers()
                        self.log.append("********************FINISH**************************")
            else:
                self.log.append(m)
                
    def clearData(self):
        btnReply = QMessageBox.question(self, 'Notice', "All data will be delete, continus?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btnReply == QMessageBox.Yes:
            self.twData.clearContents()
            self.twData.setRowCount(0)
            
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
    
    def startRsyslog(self, act):
        
        if act:
            #start
            cmd = shlex.split("sudo service rsyslog start")
        else:
            cmd = shlex.split("sudo service rsyslog stop")
        rs = ""
        try:
            rs = subprocess.check_output(cmd,
                                         stderr=subprocess.STDOUT, shell=False)
        except subprocess.CalledProcessError:
            print('startRsyslog Exception: %s' % cmd)
        return rs
        
    def reInitRsyslog(self):
        #stop rsyslog
        self.startRsyslog(False)
        #backup /var/log/syslog
        bakFile =datetime.now().strftime("%Y-%m-%d_%H%M%S")
        os.rename(self.syslogfile, "%s_%s" %(self.syslogfile, bakFile))
        #start rsyslog
        self.startRsyslog(True)
        
    def saveEVPathSignal(self):
        filename , _filter = QFileDialog.getSaveFileName(
                self, 'Save File', '', 'CSV(*.csv)')
        print("path: %s" % filename)
        if filename is not "":
            #self.log.append("#################save data###################")
            with open(filename, 'w') as stream:
                writer = csv.writer(stream, delimiter=',')
                #header
                hdata = []
                for column in range(self.twSignal.columnCount()):
                    itm = self.twSignal.horizontalHeaderItem(column)
                    if itm is not None:
                        hdata.append("%s" % itm.text())
                writer.writerow(hdata)
                for row in range(self.twSignal.rowCount()):
                    rowdata = []
                    for column in range(self.twSignal.columnCount()):
                        item = self.twSignal.item(row, column)
                        if item is not None:
                            rowdata.append("%s" % item.text())
                        else:
                            rowdata.append('')
                    #self.log.append("%s = %s" %(row, rowdata))
                    writer.writerow(rowdata)
                #self.log.append("rowdata: %s" % rowdata)
                
    @pyqtSlot()
    def startEVPathSignal(self):
        self.pbEVPathSignal.setEnabled(False)
        self.pbSaveEVPathSignal.setEnabled(False)
        #self.addSignalResult(0,0,"123")
        #return
        att1 = self.devices[0]
        att2 = self.devices[1]
        
        ip = self.leIP.text()
        username = self.leUserName.text()
        password = self.lePassword.text()
        shell = spur.SshShell(hostname=ip, 
                              username=username, password=password,
                              missing_host_key=spur.ssh.MissingHostKey.accept)
        rssi_path = self.leRSSI.text()
        
        iRow = self.twAtt.rowCount()
        if iRow == 2:
            itm1 = self.twAtt.item(0,0)
            if not itm1:
                itm1 = QTableWidgetItem(att1)
                itm1.setText(str(self.att.getSerialNumber(att1)))
            self.twAtt.setItem(0, 0, itm1)
            itm2 = self.twAtt.item(1,0)
            if not itm2:
                itm2 = QTableWidgetItem(att2)
                itm2.setText(str(self.att.getSerialNumber(att2)))
            self.twAtt.setItem(1, 0, itm2)
            
            att1Start = self.twAtt.item(0,1)
            att1Stop = self.twAtt.item(0,2)
            att1Step = self.twAtt.item(0,3)
            #att2Start = self.twAtt.item(1,1)
            att2Stop = self.twAtt.item(1,2)
            att2Step = self.twAtt.item(1,3)
            
            
            for vAtt1 in range(int(att1Start.text()), int(att1Stop.text()), int(att1Step.text())):
                #set patha,
                self.att.setValue(att1, "Attenuation", vAtt1)
                att2Start = vAtt1
                for vAtt2 in range(att2Start, int(att2Stop.text()), int(att2Step.text())):
                    #set pathb
                    self.att.setValue(att2, "Attenuation", vAtt2)
                    #get RSSI,SNR 10 times
                    for i in range(10):
                        result=""
                        #with shell:
                        print("cat %s" % rssi_path)
                        result = shell.run(["cat", rssi_path])
                        #print("%s - %s: %s" % (vAtt1, vAtt2, result))
                        curRow = self.twSignal.rowCount()
                        self.addSignalResult(curRow, 0, vAtt1)
                        self.addSignalResult(curRow, 1, vAtt2)
                        #RSSI1, RSSI2, SNR1, SNR2
                        if result.return_code == 0:
                            rs = result.output.decode()
                            r = rs.split(",")
                            self.addSignalResult(curRow, 2, r[0])
                            self.addSignalResult(curRow, 3, r[1])
                            self.addSignalResult(curRow, 4, r[2])
                            self.addSignalResult(curRow, 5, r[3].rsplit()[0])
                        else:
                            rs = result.stderr_output
                            self.addSignalResult(curRow, 2, rs)
                        
                        QApplication.processEvents()
                        time.sleep(1) #wait 1 sec
                    QApplication.processEvents()
                QApplication.processEvents()
        
        self.pbEVPathSignal.setEnabled(True)
        self.pbSaveEVPathSignal.setEnabled(True)
        
    def addSignalResult(self, row, col, val):
        iRow = self.twSignal.rowCount()
        if iRow <= row:
            self.twSignal.setRowCount(row+1)
            
        itm = self.twSignal.item(row,col)
        if not itm:
            itm = QTableWidgetItem(0)
            #print("addSignalResult: %s = %s" % (itm,val))
            itm.setText(str(val))
        self.twSignal.setItem(row, col, itm)
            
    @pyqtSlot()
    def startDirectBF(self):
        '''test direct BF'''
        shell = spur.SshShell(hostname="192.168.110.239", username="tld1", password="123456")
        
        #TODO: loop test?
        with shell:
            result = shell.run(["cat", "/proc/net/rtl88x2bu/wlan3/rate_search"])
            print(result)
            # 1. trigger rate search
            result = shell.run(["echo","1",">", "/proc/net/rtl88x2bu/wlan3/rate_search"])
            #start ping
            process = shell.spawn(["sudo", "alpha_ping", "-n 1300", "192.168.0.20"],
                               store_pid=True)
            #wait finish search (2min)
            '''
            waittime=0
            while proc.is_running():
                print(".")
                time.sleep(1)
                waittime=waittime+1
            '''
            result = process.wait_for_result()
            print(result.output)
            print("wait search finish")
            result = shell.run(["cat", "/proc/net/rtl88x2bu/wlan3/rate_search"])
            while "=1" in result.decode():
                print(".")
                time.sleep(5)
                result = shell.run(["cat", "/proc/net/rtl88x2bu/wlan3/rate_search"])
            # 2. record rate_search_result, pathb_phase
            result = shell.run(["cat", "/proc/net/rtl88x2bu/wlan3/rate_search_result"])
            print("search_result: %s" % result)
            result = shell.run(["cat", "/proc/net/rtl88x2bu/wlan3/pathb_phase"])
            print("pathb_phase: %s" % result)
        # 3. start throughput test (TODO: each att?)
        # 4. loop 1~3 for 5 times
        
    @pyqtSlot()
    def start_threads(self):
        self.log.append('starting {} threads'.format(self.NUM_THREADS))
        self.updateUIbtn(False)
        
        self.currentPhase = 0
        self.phase = self.lePhase.text().split(',')
        self.ifound = 0
        self.iIgnore = self.sbIgnore.value()
        self.iTotalCount = self.sbCount.value() + self.iIgnore
        #re-init rsyslog : clear previous result!
        self.reInitRsyslog()
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
        self._Proxy.setRate_ctl(rate)
        rs = self._Proxy.getRate_ctl()
        self.log.append("testRateCtl = %s" % rs)
        
    def setRate_ctl(self, rate):
        #echo 0x2C > /proc/net/rtl88x2bu/wlan3/rate_ctl ;cat /proc/net/rtl88x2bu/wlan3/rate_ctl
        path = self.leRateCtlPath.text()
        f = open(path,'w')
        f.write('%s' % rate)
        f.close()
        
    def getRate_ctl(self):
        path = self.leRateCtlPath.text()
        f = open(path,'r')
        r = f.readline()
        print("%s" % r)
        f.close()
        
    def setPathb_phase(self, phase):
        #echo 0 > /proc/net/rtl88x2bu/wlan3/pathb_phase; cat /proc/net/rtl88x2bu/wlan3/pathb_phase ;cat /proc/net/rtl88x2bu/wlan3/rate_ctl
        pPath = self.lePathbPhasePath.text()
        f = open(pPath,'w')
        print("write %s with %s " %(pPath, phase))
        f.write('%s' % phase)
        f.close()
    
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
    #check root
    if "Linux" in platform.system():
        if not os.geteuid() == 0:
            msg = "Please run as root or by sudo!!"
            QMessageBox.question(None, 'Error', msg, QMessageBox.Ok , QMessageBox.Ok)
            sys.exit(msg)

    m = main()
    m.show()
    sys.exit(app.exec_())
