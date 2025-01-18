# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'form.ui'
##
## Created by: Qt User Interface Compiler version 6.8.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSizePolicy,
    QSpacerItem, QTextEdit, QVBoxLayout, QWidget)
import gaia.interface.rc_resource as rc_resource

class Ui_Widget(object):
    def setupUi(self, Widget):
        if not Widget.objectName():
            Widget.setObjectName(u"Widget")
        Widget.resize(902, 782)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(Widget.sizePolicy().hasHeightForWidth())
        Widget.setSizePolicy(sizePolicy)
        Widget.setMinimumSize(QSize(750, 750))
        Widget.setAutoFillBackground(True)
        Widget.setStyleSheet(u"color: rgb(0, 0, 0);")
        self.mainLayout = QVBoxLayout(Widget)
        self.mainLayout.setObjectName(u"mainLayout")
        self.mainLayout.setContentsMargins(0, 0, 0, 0)
        self.banner = QFrame(Widget)
        self.banner.setObjectName(u"banner")
        self.banner.setMinimumSize(QSize(0, 60))
        self.banner.setStyleSheet(u"background-color: rgb(20, 20, 20);")
        self.banner.setFrameShape(QFrame.StyledPanel)
        self.banner.setFrameShadow(QFrame.Raised)
        self.horizontalLayout = QHBoxLayout(self.banner)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.horizontalLayout.setContentsMargins(9, 9, 0, 9)
        self.bannerImg = QLabel(self.banner)
        self.bannerImg.setObjectName(u"bannerImg")
        self.bannerImg.setMinimumSize(QSize(0, 0))
        self.bannerImg.setMaximumSize(QSize(164, 40))
        self.bannerImg.setStyleSheet(u"")
        self.bannerImg.setPixmap(QPixmap(u":/img/banner.PNG"))
        self.bannerImg.setScaledContents(True)

        self.horizontalLayout.addWidget(self.bannerImg)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout.addItem(self.horizontalSpacer_2)


        self.mainLayout.addWidget(self.banner)

        self.welcomeSpacerTop = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.mainLayout.addItem(self.welcomeSpacerTop)

        self.welcome = QFrame(Widget)
        self.welcome.setObjectName(u"welcome")
        self.welcome.setFrameShape(QFrame.StyledPanel)
        self.welcome.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_3 = QHBoxLayout(self.welcome)
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.welcomeImg = QLabel(self.welcome)
        self.welcomeImg.setObjectName(u"welcomeImg")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.welcomeImg.sizePolicy().hasHeightForWidth())
        self.welcomeImg.setSizePolicy(sizePolicy1)
        self.welcomeImg.setMaximumSize(QSize(417, 150))
        self.welcomeImg.setPixmap(QPixmap(u":/img/welcome.png"))
        self.welcomeImg.setScaledContents(True)
        self.welcomeImg.setAlignment(Qt.AlignCenter)

        self.horizontalLayout_3.addWidget(self.welcomeImg)


        self.mainLayout.addWidget(self.welcome)

        self.loading = QFrame(Widget)
        self.loading.setObjectName(u"loading")
        self.loading.setStyleSheet(u"color: rgb(255, 255, 255);")
        self.loading.setFrameShape(QFrame.StyledPanel)
        self.loading.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_6 = QHBoxLayout(self.loading)
        self.horizontalLayout_6.setObjectName(u"horizontalLayout_6")
        self.horizontalSpacer_5 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_6.addItem(self.horizontalSpacer_5)

        self.frame_4 = QFrame(self.loading)
        self.frame_4.setObjectName(u"frame_4")
        self.frame_4.setFrameShape(QFrame.StyledPanel)
        self.frame_4.setFrameShadow(QFrame.Raised)
        self.verticalLayout_3 = QVBoxLayout(self.frame_4)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.loadingGif = QLabel(self.frame_4)
        self.loadingGif.setObjectName(u"loadingGif")

        self.verticalLayout_3.addWidget(self.loadingGif)

        self.loadingLabel = QLabel(self.frame_4)
        self.loadingLabel.setObjectName(u"loadingLabel")
        self.loadingLabel.setAlignment(Qt.AlignCenter)

        self.verticalLayout_3.addWidget(self.loadingLabel)

        self.cancel = QPushButton(self.frame_4)
        self.cancel.setObjectName(u"cancel")
        self.cancel.setMinimumSize(QSize(0, 24))
        self.cancel.setStyleSheet(u"QPushButton {\n"
"    border-radius: 3px;\n"
"}\n"
"\n"
"QPushButton:hover {\n"
"    border-radius: 3px;\n"
"    font-weight: bold;\n"
"}\n"
"")

        self.verticalLayout_3.addWidget(self.cancel)


        self.horizontalLayout_6.addWidget(self.frame_4)

        self.horizontalSpacer_6 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_6.addItem(self.horizontalSpacer_6)


        self.mainLayout.addWidget(self.loading)

        self.welcomeSpacerBottom = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.mainLayout.addItem(self.welcomeSpacerBottom)

        self.chat = QFrame(Widget)
        self.chat.setObjectName(u"chat")
        self.chat.setMinimumSize(QSize(0, 0))
        self.chat.setFrameShape(QFrame.StyledPanel)
        self.chat.setFrameShadow(QFrame.Raised)
        self.verticalLayout_5 = QVBoxLayout(self.chat)
        self.verticalLayout_5.setSpacing(0)
        self.verticalLayout_5.setObjectName(u"verticalLayout_5")
        self.verticalLayout_5.setContentsMargins(-1, -1, -1, 0)
        self.scrollArea = QScrollArea(self.chat)
        self.scrollArea.setObjectName(u"scrollArea")
        self.scrollArea.setStyleSheet(u"QScrollBar:vertical {\n"
"            border: none;\n"
"            background: #000000;\n"
"            width: 5px;\n"
"            margin: 0px 0px 0px 0px;\n"
"        }\n"
"        QScrollBar::handle:vertical {\n"
"            background: #555;\n"
"            min-height: 20px;\n"
"            border-radius: 2px;\n"
"        }\n"
"        QScrollBar::handle:vertical:hover {\n"
"            background: #0A819A;\n"
"        }\n"
"        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n"
"            border: none;\n"
"            background: none;\n"
"        }\n"
"        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {\n"
"            background: none;\n"
"        }")
        self.scrollArea.setFrameShape(QFrame.NoFrame)
        self.scrollArea.setWidgetResizable(True)
        self.scrollAreaWidgetContents = QWidget()
        self.scrollAreaWidgetContents.setObjectName(u"scrollAreaWidgetContents")
        self.scrollAreaWidgetContents.setGeometry(QRect(0, 0, 882, 230))
        self.verticalLayout_6 = QVBoxLayout(self.scrollAreaWidgetContents)
        self.verticalLayout_6.setObjectName(u"verticalLayout_6")
        self.verticalLayout_6.setContentsMargins(-1, 0, -1, 0)
        self.board = QFrame(self.scrollAreaWidgetContents)
        self.board.setObjectName(u"board")
        self.board.setEnabled(True)
        self.board.setStyleSheet(u"background-color: rgb(20, 20, 20);")
        self.board.setFrameShape(QFrame.StyledPanel)
        self.board.setFrameShadow(QFrame.Raised)
        self.boardLayout = QVBoxLayout(self.board)
        self.boardLayout.setObjectName(u"boardLayout")
        self.boardLayout.setContentsMargins(-1, -1, -1, 9)
        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.boardLayout.addItem(self.verticalSpacer)

        self.sampleCard_1 = QFrame(self.board)
        self.sampleCard_1.setObjectName(u"sampleCard_1")
        self.sampleCard_1.setFrameShape(QFrame.StyledPanel)
        self.sampleCard_1.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_4 = QHBoxLayout(self.sampleCard_1)
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_4.addItem(self.horizontalSpacer_3)

        self.frame_6 = QFrame(self.sampleCard_1)
        self.frame_6.setObjectName(u"frame_6")
        self.frame_6.setFrameShape(QFrame.StyledPanel)
        self.frame_6.setFrameShadow(QFrame.Raised)
        self.verticalLayout_7 = QVBoxLayout(self.frame_6)
        self.verticalLayout_7.setObjectName(u"verticalLayout_7")
        self.pushButton_2 = QPushButton(self.frame_6)
        self.pushButton_2.setObjectName(u"pushButton_2")
        self.pushButton_2.setMinimumSize(QSize(0, 0))
        font = QFont()
        font.setPointSize(12)
        self.pushButton_2.setFont(font)
        self.pushButton_2.setStyleSheet(u"border-radius: 6px;\n"
"border: 1px solid #0A819A;\n"
"background-color: #0A819A;\n"
"color: rgb(255, 255, 255);\n"
"padding: 8px 8px; ")

        self.verticalLayout_7.addWidget(self.pushButton_2)

        self.label_3 = QLabel(self.frame_6)
        self.label_3.setObjectName(u"label_3")
        self.label_3.setStyleSheet(u"color: rgb(255, 255, 255);")
        self.label_3.setAlignment(Qt.AlignRight|Qt.AlignTrailing|Qt.AlignVCenter)

        self.verticalLayout_7.addWidget(self.label_3)


        self.horizontalLayout_4.addWidget(self.frame_6)


        self.boardLayout.addWidget(self.sampleCard_1)

        self.sampleCard_2 = QFrame(self.board)
        self.sampleCard_2.setObjectName(u"sampleCard_2")
        self.sampleCard_2.setFrameShape(QFrame.StyledPanel)
        self.sampleCard_2.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_5 = QHBoxLayout(self.sampleCard_2)
        self.horizontalLayout_5.setObjectName(u"horizontalLayout_5")
        self.horizontalLayout_5.setContentsMargins(-1, -1, -1, 0)
        self.frame_7 = QFrame(self.sampleCard_2)
        self.frame_7.setObjectName(u"frame_7")
        self.frame_7.setFrameShape(QFrame.StyledPanel)
        self.frame_7.setFrameShadow(QFrame.Raised)
        self.verticalLayout_8 = QVBoxLayout(self.frame_7)
        self.verticalLayout_8.setObjectName(u"verticalLayout_8")
        self.pushButton_4 = QPushButton(self.frame_7)
        self.pushButton_4.setObjectName(u"pushButton_4")
        self.pushButton_4.setMinimumSize(QSize(0, 0))
        self.pushButton_4.setFont(font)
        self.pushButton_4.setStyleSheet(u"border-radius: 6px;\n"
"border: 1px solid rgb(0, 0, 0);\n"
"background-color:rgb(77, 77, 77);\n"
"color: rgb(255, 255, 255);\n"
"padding: 8px 8px; ")

        self.verticalLayout_8.addWidget(self.pushButton_4)

        self.label_4 = QLabel(self.frame_7)
        self.label_4.setObjectName(u"label_4")
        self.label_4.setStyleSheet(u"color: rgb(255, 255, 255);")
        self.label_4.setAlignment(Qt.AlignLeading|Qt.AlignLeft|Qt.AlignVCenter)

        self.verticalLayout_8.addWidget(self.label_4)


        self.horizontalLayout_5.addWidget(self.frame_7)

        self.horizontalSpacer_4 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_5.addItem(self.horizontalSpacer_4)


        self.boardLayout.addWidget(self.sampleCard_2)


        self.verticalLayout_6.addWidget(self.board)

        self.scrollArea.setWidget(self.scrollAreaWidgetContents)

        self.verticalLayout_5.addWidget(self.scrollArea)

        self.frame = QFrame(self.chat)
        self.frame.setObjectName(u"frame")
        self.frame.setStyleSheet(u"")
        self.frame.setFrameShape(QFrame.StyledPanel)
        self.frame.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_7 = QHBoxLayout(self.frame)
        self.horizontalLayout_7.setObjectName(u"horizontalLayout_7")
        self.horizontalLayout_7.setContentsMargins(-1, 0, -1, 0)
        self.horizontalSpacer_7 = QSpacerItem(40, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_7.addItem(self.horizontalSpacer_7)

        self.stop = QPushButton(self.frame)
        self.stop.setObjectName(u"stop")
        self.stop.setEnabled(True)
        self.stop.setMaximumSize(QSize(16777215, 17))
        self.stop.setStyleSheet(u"QPushButton {\n"
"    border-radius: 3px;\n"
"    color: rgb(77, 77, 77);\n"
"    border-color: rgb(0, 0, 0); \n"
"    background-color: rgb(0, 0, 0); \n"
"    border-color: rgb(0, 0, 0); \n"
"    qproperty-icon: url(\":/img/stop.png\");\n"
"}\n"
"\n"
"QPushButton:hover {\n"
"    border-radius: 3px;\n"
"    color: rgb(255, 255, 255); \n"
"    background-color: rgb(0, 0, 0);\n"
"    border-color: rgb(0, 0, 0); \n"
"}\n"
"")
        icon = QIcon()
        icon.addFile(u":/img/stop.png", QSize(), QIcon.Mode.Normal, QIcon.State.Off)
        self.stop.setIcon(icon)
        self.stop.setIconSize(QSize(20, 11))

        self.horizontalLayout_7.addWidget(self.stop)

        self.restart = QPushButton(self.frame)
        self.restart.setObjectName(u"restart")
        self.restart.setMaximumSize(QSize(16777215, 17))
        font1 = QFont()
        font1.setPointSize(9)
        self.restart.setFont(font1)
        self.restart.setStyleSheet(u"QPushButton {\n"
"    border-radius: 3px;\n"
"    color: rgb(77, 77, 77);\n"
"    border-color: rgb(0, 0, 0); \n"
"    background-color: rgb(0, 0, 0); \n"
"    border-color: rgb(0, 0, 0); \n"
"    qproperty-icon: url(\":/img/refresh.png\");\n"
"}\n"
"\n"
"QPushButton:hover {\n"
"    border-radius: 3px;\n"
"    color: rgb(255, 255, 255); \n"
"    background-color: rgb(0, 0, 0);\n"
"    border-color: rgb(0, 0, 0); \n"
"}\n"
"")
        icon1 = QIcon()
        icon1.addFile(u":/img/refresh.png", QSize(), QIcon.Mode.Normal, QIcon.State.Off)
        self.restart.setIcon(icon1)
        self.restart.setIconSize(QSize(16, 11))
        self.restart.setAutoRepeatInterval(98)

        self.horizontalLayout_7.addWidget(self.restart)


        self.verticalLayout_5.addWidget(self.frame)


        self.mainLayout.addWidget(self.chat)

        self.messaging = QFrame(Widget)
        self.messaging.setObjectName(u"messaging")
        self.messaging.setEnabled(True)
        self.messaging.setStyleSheet(u"QPushButton {\n"
"    border-radius: 3px;\n"
"    border: 1px solid #0A819A;\n"
"    background-color: #0A819A;\n"
"}\n"
"\n"
"QPushButton:hover {\n"
"    background-color: #4AA9BD;\n"
"}\n"
"\n"
"QPushButton:disabled {\n"
"    background-color: gray;\n"
"    border-color: gray;\n"
"}")
        self.messaging.setFrameShape(QFrame.StyledPanel)
        self.messaging.setFrameShadow(QFrame.Raised)
        self.verticalLayout = QVBoxLayout(self.messaging)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(-1, 9, -1, -1)
        self.frame_1 = QFrame(self.messaging)
        self.frame_1.setObjectName(u"frame_1")
        self.frame_1.setFont(font)
        self.frame_1.setLayoutDirection(Qt.LeftToRight)
        self.frame_1.setStyleSheet(u"border-radius: 10px;\n"
"border: 1px solid #0A819A;;\n"
"color: rgb(255, 255, 255);")
        self.frame_1.setFrameShape(QFrame.StyledPanel)
        self.frame_1.setFrameShadow(QFrame.Raised)
        self.verticalLayout_2 = QVBoxLayout(self.frame_1)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(-1, 0, -1, -1)
        self.frame_2 = QFrame(self.frame_1)
        self.frame_2.setObjectName(u"frame_2")
        self.frame_2.setStyleSheet(u"border-radius: 0px;\n"
"border: 0px solid #0A819A;;\n"
"")
        self.frame_2.setFrameShape(QFrame.StyledPanel)
        self.frame_2.setFrameShadow(QFrame.Raised)
        self.verticalLayout_4 = QVBoxLayout(self.frame_2)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.verticalLayout_4.setContentsMargins(0, -1, 0, 0)
        self.frame_3 = QFrame(self.frame_2)
        self.frame_3.setObjectName(u"frame_3")
        self.frame_3.setStyleSheet(u"border-radius: 1px;\n"
"border: 0px solid #0A819A;;\n"
"")
        self.frame_3.setFrameShape(QFrame.StyledPanel)
        self.frame_3.setFrameShadow(QFrame.Raised)
        self.horizontalLayout_2 = QHBoxLayout(self.frame_3)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.agent = QComboBox(self.frame_3)
        self.agent.setObjectName(u"agent")
        self.agent.setMinimumSize(QSize(100, 25))
        self.agent.setFont(font)
        self.agent.setStyleSheet(u"QComboBox {\n"
"                   border-radius: 3px;\n"
"                   border: 1px solid #0A819A;\n"
"                   color: white;\n"
"                  }\n"
"                  QComboBox:disabled {\n"
"                   color: gray;\n"
"                  }")

        self.horizontalLayout_2.addWidget(self.agent)

        self.model = QComboBox(self.frame_3)
        self.model.setObjectName(u"model")
        self.model.setMinimumSize(QSize(300, 25))
        self.model.setFont(font)
        self.model.setStyleSheet(u"QComboBox {\n"
"                   border-radius: 3px;\n"
"                   border: 1px solid #0A819A;\n"
"                   color: white;\n"
"                  }\n"
"                  QComboBox:disabled {\n"
"                   color: gray;\n"
"                  }")

        self.horizontalLayout_2.addWidget(self.model)

        self.device = QComboBox(self.frame_3)
        self.device.setObjectName(u"device")
        self.device.setEnabled(False)
        self.device.setMinimumSize(QSize(150, 25))
        self.device.setFont(font)
        self.device.setStyleSheet(u"QComboBox {\n"
"                   border-radius: 3px;\n"
"                   border: 1px solid #0A819A;\n"
"                   color: white;\n"
"                  }\n"
"                  QComboBox:disabled {\n"
"                   color: gray;\n"
"                  }")
        self.device.setFrame(True)

        self.horizontalLayout_2.addWidget(self.device)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.horizontalLayout_2.addItem(self.horizontalSpacer)

        self.ask = QPushButton(self.frame_3)
        self.ask.setObjectName(u"ask")
        self.ask.setEnabled(True)
        self.ask.setMinimumSize(QSize(50, 25))
        self.ask.setFont(font)
        self.ask.setStyleSheet(u"border-radius: 6px;")
        icon2 = QIcon()
        icon2.addFile(u":/img/send.png", QSize(), QIcon.Mode.Normal, QIcon.State.Off)
        self.ask.setIcon(icon2)

        self.horizontalLayout_2.addWidget(self.ask)


        self.verticalLayout_4.addWidget(self.frame_3)

        self.prompt = QTextEdit(self.frame_2)
        self.prompt.setObjectName(u"prompt")
        self.prompt.setEnabled(True)
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.prompt.sizePolicy().hasHeightForWidth())
        self.prompt.setSizePolicy(sizePolicy2)
        self.prompt.setMaximumSize(QSize(16777215, 50))
        self.prompt.setFont(font)

        self.verticalLayout_4.addWidget(self.prompt)


        self.verticalLayout_2.addWidget(self.frame_2)


        self.verticalLayout.addWidget(self.frame_1)


        self.mainLayout.addWidget(self.messaging)


        self.retranslateUi(Widget)

        QMetaObject.connectSlotsByName(Widget)
    # setupUi

    def retranslateUi(self, Widget):
        Widget.setWindowTitle(QCoreApplication.translate("Widget", u"Widget", None))
        self.bannerImg.setText("")
        self.welcomeImg.setText("")
        self.loadingGif.setText(QCoreApplication.translate("Widget", u"LOADING ANIMATION", None))
        self.loadingLabel.setText(QCoreApplication.translate("Widget", u"LOADING MESSAGE", None))
        self.cancel.setText(QCoreApplication.translate("Widget", u"CANCEL", None))
        self.pushButton_2.setText(QCoreApplication.translate("Widget", u"Hi there! How are you today? Can you please help me with something?", None))
        self.label_3.setText(QCoreApplication.translate("Widget", u"5:53:01", None))
        self.pushButton_4.setText(QCoreApplication.translate("Widget", u"Absolutely! How can I help you today?", None))
        self.label_4.setText(QCoreApplication.translate("Widget", u"5:53:02", None))
        self.stop.setText(QCoreApplication.translate("Widget", u"Stop", None))
        self.restart.setText(QCoreApplication.translate("Widget", u"Restart", None))
#if QT_CONFIG(shortcut)
        self.restart.setShortcut("")
#endif // QT_CONFIG(shortcut)
        self.model.setPlaceholderText(QCoreApplication.translate("Widget", u"As", None))
        self.ask.setText("")
        self.prompt.setHtml(QCoreApplication.translate("Widget", u"<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0//EN\" \"http://www.w3.org/TR/REC-html40/strict.dtd\">\n"
"<html><head><meta name=\"qrichtext\" content=\"1\" /><meta charset=\"utf-8\" /><style type=\"text/css\">\n"
"p, li { white-space: pre-wrap; }\n"
"hr { height: 1px; border-width: 0; }\n"
"li.unchecked::marker { content: \"\\2610\"; }\n"
"li.checked::marker { content: \"\\2612\"; }\n"
"</style></head><body style=\" font-family:'Segoe UI'; font-size:12pt; font-weight:400; font-style:normal;\">\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><br /></p></body></html>", None))
        self.prompt.setPlaceholderText(QCoreApplication.translate("Widget", u"Ask anything, anytime...", None))
    # retranslateUi

