# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
root = Path(SPECPATH)
datas = [(str(root/'qft/ui'), 'qft/ui'), (str(root/'vendor'), 'vendor'),
         (str(root/'qualcomm_icon.icns'), '.'), (str(root/'THIRD_PARTY_NOTICES.md'), '.'), (str(root/'resources/runtime-manifest.json'),'resources'),
         (str(root/'resources/ADB-NOTICE.txt'), 'resources'), (str(root/'resources/LIBUSB-LICENSE.txt'), 'resources')]
datas += [(str(root/'resources/python-licenses'), 'resources/python-licenses')]
datas += collect_data_files('PySide6', includes=['Qt/translations/qtbase_zh_CN.qm'])
a = Analysis(['main.py'], pathex=[str(root),str(root/'vendor')],
    binaries=[(str(root/'resources/bin/adb'), 'resources/bin'), (str(root/'resources/lib/libusb-1.0.dylib'), 'resources/lib')],
    datas=datas,
    hiddenimports=['usb.backend.libusb1','usb.backend.libusb0', 'edlclient.Library.Connection.usblib',
                   'edlclient.Library.sahara','edlclient.Library.firehose','edlclient.Library.xmlparser'],
    excludes=['tkinter','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick','PySide6.QtMultimedia','PySide6.Qt3DCore','pytest','PIL'], noarchive=False)
# PyUSB's ctypes hook may discover a Homebrew dylib while analyzing. Always
# substitute our macOS-13 build, including any auto-discovered root-level alias.
a.binaries = [(dest, str(root/'resources/lib/libusb-1.0.dylib') if Path(src).name.startswith('libusb-1.0') else src, kind)
              for dest, src, kind in a.binaries]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='QualcommFlashTool',debug=False,strip=False,upx=False,
        console=False,target_arch='arm64',icon='qualcomm_icon.icns',codesign_identity=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='QualcommFlashTool')
app=BUNDLE(coll,name='QualcommFlashTool.app',icon='qualcomm_icon.icns',bundle_identifier='com.patrickxu.qualcommflashtool',
    info_plist={'CFBundleShortVersionString':'2.0.0','CFBundleVersion':'20000','LSMinimumSystemVersion':'13.0',
                'NSHighResolutionCapable':True,'NSHumanReadableCopyright':'Patrick Xu; EDL engine © B. Kerler, GPLv3'})
