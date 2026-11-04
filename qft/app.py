import sys


def main():
    if "--worker" in sys.argv:
        from .worker import main as worker_main

        worker_main()
        return
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    from PySide6.QtCore import QUrl, QTimer, QLockFile
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from .controller import Controller
    from .runtime import root, data_dir

    app = QApplication(sys.argv)
    app.setOrganizationName("Patrick Xu")
    app.setApplicationName("Qualcomm Flash Tool")
    app.setApplicationVersion("2.0.0")
    app.setWindowIcon(QIcon(str(root() / "qualcomm_icon.icns")))
    lock = QLockFile(str(data_dir() / "application.lock"))
    if not lock.tryLock(0):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            None, "Qualcomm Flash Tool", "Another instance is already running."
        )
        return
    QQuickStyle.setStyle("Basic")
    controller = Controller(demo=args.demo)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", controller)
    engine.load(QUrl.fromLocalFile(str(root() / "qft/ui/Main.qml")))
    if not engine.rootObjects():
        raise SystemExit("QML could not be loaded")
    app.aboutToQuit.connect(controller.close)
    if args.screenshot:
        window = engine.rootObjects()[0]

        def capture():
            image = window.grabWindow()
            if not image.save(args.screenshot):
                raise RuntimeError("Screenshot failed")
            app.quit()

        QTimer.singleShot(1600, capture)
    elif args.smoke:
        QTimer.singleShot(2500, app.quit)
    code = app.exec()
    del engine
    sys.exit(code)


if __name__ == "__main__":
    main()
