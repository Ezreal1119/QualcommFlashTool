import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: win
    width: 520; height: 780; minimumWidth: 430; minimumHeight: 660
    visible: true
    title: "Qualcomm Flash Tool"
    readonly property var s: backend ? backend.data : ({})
    readonly property bool dark: s.theme === "dark" || (s.theme === "system" && Qt.styleHints.colorScheme === Qt.Dark)
    readonly property color bg: dark ? "#14191f" : "#f5f6f8"
    readonly property color surface: dark ? "#1e252e" : "#ffffff"
    readonly property color ink: dark ? "#eef2f6" : "#18222f"
    readonly property color muted: dark ? "#9baabd" : "#586779"
    readonly property color line: dark ? "#323c49" : "#dce2e8"
    readonly property color accent: dark ? "#79ddc7" : "#08786c"
    readonly property color warning: dark ? "#f0ba81" : "#965013"
    readonly property color error: dark ? "#ffa49e" : "#b73535"
    readonly property var stateNames: ({idle:"Ready when you are",checking:"Checking",ready:"Ready to flash",connecting:"Connecting",flashing:"Writing images",patching:"Applying patches",verifying:"Verifying",rebooting:"Rebooting",stopping:"Stopping safely",success:"Completed",failed:"Failed",incomplete:"Verification incomplete",interrupted:"Interrupted",reboot_failed:"Reboot failed",attention:"Needs attention",entering_edl:"Entering EDL",waiting_edl:"Waiting for EDL"})
    readonly property var stageOrder: ({checking:0,entering_edl:1,waiting_edl:1,connecting:1,flashing:2,patching:2,verifying:3,rebooting:4})
    property int lastStep: 0
    property bool followLog: true
    readonly property int currentStep: s.state === "success" ? 5 : s.state === "ready" ? 1 : s.state === "idle" ? (s.packageStatus === "passed" ? 1 : 0) : stageOrder[s.state] !== undefined ? stageOrder[s.state] : lastStep
    readonly property string targetNotice: ({missing:"Connect a device",choose:"Select a device",unauthorized:"Allow USB debugging on the device",offline:"Device offline · reconnect USB",usb_identity_missing:"USB identity unavailable",unavailable:"Device unavailable",adb_ready:"ADB · ready",edl_ready:"EDL · connected"})[s.deviceNotice] || ""
    color: bg
    font.family: "Helvetica Neue"; font.pixelSize: 13
    palette.text: ink; palette.windowText: ink; palette.buttonText: ink; palette.button: surface
    palette.base: surface; palette.window: bg; palette.highlight: accent; palette.highlightedText: dark ? "#14221e" : "#ffffff"
    function basename(path) { return path ? path.split("/").pop() : "" }
    function bytes(n) { return n >= 1073741824 ? (n/1073741824).toFixed(2)+" GB" : (n/1048576).toFixed(1)+" MB" }
    function elapsed(n) { return Math.floor(n/60).toString().padStart(2,"0")+":"+(n%60).toString().padStart(2,"0") }
    function selectedDevice() { return s.deviceIndex >= 0 && s.deviceIndex < s.devices.length ? s.devices[s.deviceIndex] : null }
    function openConfiguration(tab) { configTabs.currentIndex=tab || 0; configDialog.open() }

    component Action: Button {
        id: button
        property bool primary: false
        property bool danger: false
        implicitHeight: 34; leftPadding: 12; rightPadding: 12
        font.pixelSize: 12; font.weight: Font.DemiBold
        contentItem: Text { text: button.text; font: button.font; color: !button.enabled ? muted : button.primary ? "#ffffff" : button.danger ? error : ink; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle {
            radius: 8
            color: button.primary ? (button.enabled ? button.down ? "#056358" : "#087c70" : (dark ? "#293c3b" : "#d4e5df")) : button.hovered && button.enabled ? (dark ? "#303b49" : "#edf1f5") : surface
            border.width: button.visualFocus ? 2 : button.primary ? 0 : 1; border.color: button.visualFocus ? accent : line
        }
        opacity: enabled ? 1 : 0.65
        ToolTip.visible: hovered && ToolTip.text.length > 0
        ToolTip.delay: 500
    }
    component DialogActions: Control {
        id: actions
        property string acceptText: "Confirm"
        property string cancelText: "Cancel"
        property bool acceptEnabled: true
        signal confirmed()
        signal cancelled()
        padding: 18; topPadding: 10
        contentItem: RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }
            Action { objectName: "dialogCancel"; text: actions.cancelText; Layout.minimumWidth: 82; onClicked: actions.cancelled() }
            Action { objectName: "dialogConfirm"; text: actions.acceptText; primary: true; enabled: actions.acceptEnabled; Layout.minimumWidth: 104; onClicked: actions.confirmed() }
        }
    }
    component Modal: Dialog {
        id: modal
        modal: true; anchors.centerIn: parent; width: Math.min(win.width-28,510); padding: 18
        contentHeight: contentItem ? contentItem.implicitHeight : 0
        background: Rectangle { color: surface; radius: 12; border.color: line }
        header: Label { text: modal.title; color: ink; font.pixelSize: 16; font.weight: Font.DemiBold; padding: 18; bottomPadding: 6; wrapMode: Text.WordWrap }
    }
    component ConfigTab: TabButton {
        id: tab
        implicitHeight: 32
        contentItem: Text { text: tab.text; color: tab.checked ? ink : muted; font.pixelSize: 12; font.weight: tab.checked ? Font.DemiBold : Font.Normal; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { color: tab.checked ? surface : bg; radius: 7; border.color: tab.checked ? line : "transparent" }
    }
    component Caption: Label { color: muted; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1 }
    component Combo: ComboBox {
        id: control
        implicitHeight: 34; Layout.fillWidth: true
        contentItem: Text { leftPadding: 10; rightPadding: 26; text: control.displayText; color: control.enabled ? ink : muted; elide: Text.ElideMiddle; verticalAlignment: Text.AlignVCenter; font.pixelSize: 12 }
        indicator: Text { text: "⌄"; color: muted; font.pixelSize: 17; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter }
        background: Rectangle { radius: 7; color: surface; border.color: control.activeFocus ? accent : line }
        delegate: ItemDelegate {
            id: option
            required property var modelData
            required property int index
            width: control.width; highlighted: control.highlightedIndex === index
            text: typeof modelData === "string" ? modelData : modelData[control.textRole]
            contentItem: Text { text: option.text; color: ink; font.pixelSize: 12; elide: Text.ElideMiddle; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: option.highlighted ? (dark ? "#30453f" : "#e5f3ef") : surface }
        }
        popup: Popup {
            y: control.height+4; width: control.width; padding: 4
            implicitHeight: Math.min(contentItem.implicitHeight+8,260)
            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: control.popup.visible ? control.delegateModel : null; currentIndex: control.highlightedIndex; ScrollIndicator.vertical: ScrollIndicator {} }
            background: Rectangle { color: surface; border.color: line; radius: 8 }
        }
    }
    component FileRow: RowLayout {
        required property string filePath
        required property string kind
        required property bool chosen
        Layout.fillWidth: true; spacing: 6
        CheckBox { checked: chosen; enabled: !s.busy; onClicked: backend.toggleFile(kind,filePath,checked) }
        Label { text: basename(filePath); color: ink; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.visible: fileHover.hovered && filePath.length > 0; ToolTip.delay: 500; ToolTip.text: filePath; HoverHandler { id: fileHover } }
        Action { text: "Edit"; enabled: !s.busy && !s.demo; onClicked: backend.editXml(filePath) }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 22; spacing: 16
        RowLayout {
            Layout.fillWidth: true; spacing: 10
            Rectangle { width: 32; height: 32; radius: 9; color: "#087c70"; Text { anchors.centerIn: parent; text: "Q"; color: "white"; font.pixelSize: 23; font.weight: Font.Bold } }
            ColumnLayout { spacing: 2
                Label { text: "Qualcomm Flash Tool"; color: ink; font.pixelSize: 17; font.weight: Font.DemiBold }
                Label { text: "EDL / 2.3.2"+(s.demo ? " · Preview" : ""); color: muted; font.pixelSize: 10; font.letterSpacing: 1 }
            }
            Item { Layout.fillWidth: true }
            Action { objectName: "configureButton"; text: "Configure…"; enabled: !s.busy; onClicked: openConfiguration(0) }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: deviceCol.implicitHeight+24; color: surface; radius: 10; border.color: line
            ColumnLayout { id: deviceCol; anchors.fill: parent; anchors.margins: 12; spacing: 5
                RowLayout { Layout.fillWidth: true; spacing: 8
                    Caption { text: "DEVICE" }
                    Item { Layout.fillWidth: true }
                    Label { text: targetNotice; color: s.deviceNotice === "adb_ready" || s.deviceNotice === "edl_ready" ? accent : muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: win.width-170 }
                    Action { text: "↻"; implicitHeight: 26; leftPadding: 8; rightPadding: 8; enabled: !s.busy; ToolTip.text: "Refresh devices"; onClicked: backend.refresh() }
                }
                Combo { objectName: "deviceSelector"; model: s.devices.map(function(d) {return d.name+" · "+(d.serial || d.id)}); currentIndex: s.deviceIndex; enabled: !s.busy && s.devices.length > 0; displayText: currentIndex < 0 ? "No device selected" : currentText; onActivated: backend.selectDevice(currentIndex) }
            }
        }
        Rectangle {
            Layout.fillWidth: true; implicitHeight: firmwareCol.implicitHeight+28; radius: 10; color: surface; border.color: firmwareDrop.containsDrag ? accent : line
            DropArea { id: firmwareDrop; anchors.fill: parent; onDropped: function(drop) { if (drop.hasUrls && s.canChoosePackage) backend.dropFolder(drop.urls[0]) } }
            ColumnLayout { id: firmwareCol; anchors.fill: parent; anchors.margins: 14; spacing: 8
                RowLayout { Layout.fillWidth: true
                    Caption { text: "FIRMWARE" }
                    Item { Layout.fillWidth: true }
                    Action { text: "Clear"; implicitHeight: 28; visible: !!s.folder || !!s.packageSource; enabled: s.canChoosePackage; onClicked: backend.clearPackage() }
                    Action { objectName: "chooseFirmwareButton"; text: "Choose folder…"; implicitHeight: 28; enabled: s.canChoosePackage; onClicked: backend.chooseFolder() }
                }
                Label { text: basename(s.packageSource || s.folder) || "Drop a firmware folder here"; font.pixelSize: 13; font.weight: Font.DemiBold; color: ink; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.visible: firmwareHover.hovered && !!(s.packageSource || s.folder); ToolTip.delay: 500; ToolTip.text: s.packageSource || s.folder; HoverHandler { id: firmwareHover } }
                Label { text: s.plan.programs ? s.memory.toUpperCase()+" · "+s.plan.programs.length+" writes"+" · "+(s.plan.patches || []).length+" patches" : s.packageStatus === "checking" ? "Checking files…" : s.packageStatus === "changed" ? "Changed · check again" : s.packageStatus === "attention" ? "Check failed · open Configure" : s.folder ? "Not checked yet" : "No firmware selected"; font.pixelSize: 11; color: s.packageStatus === "attention" ? warning : muted; Layout.fillWidth: true; elide: Text.ElideRight }
            }
        }
        RowLayout {
            objectName: "stepIndicator"; Layout.fillWidth: true; spacing: 0
            Repeater {
                model: ["Check","Connect","Flash","Verify","Reboot"]
                delegate: Item {
                    required property int index
                    required property string modelData
                    Layout.fillWidth: true; implicitHeight: 48
                    Rectangle { visible: index < 4; x: parent.width/2+14; y: 11; width: parent.width-28; height: 1; color: currentStep > index ? accent : line }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter; width: 23; height: 23; radius: 12
                        color: currentStep > index ? "#087c70" : index === currentStep && s.busy ? (dark ? "#263f3b" : "#e0f2ed") : surface
                        border.color: currentStep >= index && (s.folder || s.busy) ? accent : line
                        Text { anchors.centerIn: parent; text: currentStep > index ? "✓" : index+1; color: currentStep > index ? "white" : currentStep === index && s.busy ? accent : muted; font.pixelSize: 10; font.weight: Font.DemiBold }
                    }
                    Label { anchors.horizontalCenter: parent.horizontalCenter; y: 30; text: modelData; color: currentStep >= index && (s.folder || s.busy) ? ink : muted; font.pixelSize: 11 }
                }
            }
        }
        ColumnLayout { Layout.fillWidth: true; spacing: 6
            RowLayout { Layout.fillWidth: true
                Label { objectName: "phaseLabel"; text: stateNames[s.state] || s.state; color: s.state === "failed" || s.state === "reboot_failed" ? error : ink; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                Label { text: s.progressKnown && s.busy ? Math.round(s.progress*100)+"%  ·  "+elapsed(s.elapsed) : elapsed(s.elapsed); color: muted; font.family: "Menlo"; font.pixelSize: 11 }
            }
            Label { text: s.currentFile ? basename(s.currentFile) : s.state === "success" ? "Verified · reboot requested" : s.state === "incomplete" ? "Review unverified items in console" : s.state === "failed" || s.state === "interrupted" || s.state === "reboot_failed" ? "See console for details" : "Reboots automatically after verification"; color: muted; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle }
            ProgressBar {
                Layout.fillWidth: true; implicitHeight: 4
                value: s.state === "success" ? 1 : s.busy || ["failed","interrupted","incomplete","reboot_failed"].indexOf(s.state)>=0 ? s.progress : 0
                indeterminate: s.busy && (!s.progressKnown || (s.state !== "flashing" && s.state !== "stopping"))
                background: Rectangle { color: line; radius: 2 }
                contentItem: Item {
                    clip: true
                    Rectangle { height: parent.height; radius: 2; color: "#087c70"; width: parent.parent.indeterminate ? parent.width*0.28 : parent.width*parent.parent.position; x: parent.parent.indeterminate ? (parent.width-width)*pulse.value : 0
                        QtObject { id: pulse; property real value: 0 }
                        NumberAnimation { target: pulse; property: "value"; from: 0; to: 1; duration: 1100; loops: Animation.Infinite; running: s.busy && (!s.progressKnown || s.state !== "flashing") }
                    }
                }
            }
        }
        RowLayout { Layout.fillWidth: true; spacing: 8
            Action { text: "Check"; enabled: !s.busy && !!s.folder; onClicked: backend.check() }
            Action { text: "Reboot…"; visible: s.state === "incomplete" || s.state === "reboot_failed"; enabled: !s.busy && selectedDevice() !== null; onClicked: resetDialog.open() }
            Action { objectName: "startFlashButton"; Layout.fillWidth: true; implicitHeight: 40; primary: true; text: "Flash…"; visible: !s.busy; enabled: s.canStart; onClicked: flashDialog.open() }
            Action { objectName: "stopButton"; Layout.fillWidth: true; implicitHeight: 40; danger: true; text: s.canCancelNow ? "Cancel check" : "Stop safely"; visible: s.busy; enabled: s.state !== "stopping"; onClicked: backend.stop() }
        }
        Rectangle { objectName: "consolePanel"; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 150; color: dark ? "#0d1218" : "#ffffff"; radius: 10; border.color: line
            ColumnLayout { anchors.fill: parent; anchors.margins: 1; spacing: 0
                RowLayout { Layout.fillWidth: true; Layout.margins: 10; Layout.bottomMargin: 5; spacing: 6
                    Caption { text: "CONSOLE" }
                    Label { text: s.busy && s.progressKnown ? bytes(s.transferred)+" / "+bytes(s.totalBytes) : ""; color: muted; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideRight }
                    Action { text: "↓"; implicitHeight: 24; leftPadding: 7; rightPadding: 7; ToolTip.text: "Follow latest output"; onClicked: { followLog=true; consoleBar.position=Math.max(0,1-consoleBar.size) } }
                    Action { text: "History"; implicitHeight: 24; leftPadding: 8; rightPadding: 8; onClicked: historyDialog.open() }
                }
                ScrollView { id: consoleScroll; objectName: "consoleScroll"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    WheelHandler { target: null; onWheel: function(event) { followLog=false; event.accepted=false } }
                    ScrollBar.vertical: ScrollBar { id: consoleBar; onPressedChanged: if (pressed) followLog=false }
                    TextArea { id: consoleText; objectName: "consoleText"; text: (s.logs || "Waiting for a task.\n")+(s.packageIssues.length ? "\n"+s.packageIssues.join("\n") : ""); readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; textFormat: TextEdit.PlainText; font.family: "Menlo"; font.pixelSize: 11; color: dark ? "#b9c9d8" : "#344357"; padding: 12; background: null
                        onTextChanged: if (followLog) Qt.callLater(function() { consoleBar.position=Math.max(0,1-consoleBar.size) })
                        onSelectedTextChanged: if (selectedText.length) followLog=false
                    }
                }
            }
        }
    }

    Modal {
        id: configDialog; objectName: "configurationDialog"; title: "Configure"
        width: Math.min(win.width-28,580); height: Math.min(win.height-28,730)
        contentItem: ColumnLayout { spacing: 12
            TabBar { id: configTabs; objectName: "configurationTabs"; Layout.fillWidth: true
                ConfigTab { text: "Files" }
                ConfigTab { text: "Plan" }
            }
            ScrollView { id: configScroll; objectName: "configurationScroll"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout { width: configScroll.availableWidth; spacing: 12
                    ColumnLayout { visible: configTabs.currentIndex === 0; Layout.fillWidth: true; spacing: 12
                        Caption { text: "FIREHOSE LOADER" }
                        RowLayout { Layout.fillWidth: true
                            Combo { model: s.loaders; currentIndex: s.loaders.indexOf(s.loader); enabled: !s.busy; displayText: s.loader ? basename(s.loader) : "Choose loader"; onActivated: backend.selectLoader(s.loaders[currentIndex]) }
                            Action { text: "…"; enabled: !s.busy; onClicked: backend.chooseFiles("loader") }
                        }
                        RowLayout { Layout.fillWidth: true
                            Caption { text: "STORAGE"; Layout.fillWidth: true }
                            Combo { Layout.fillWidth: false; Layout.preferredWidth: 150; model: ["UFS","eMMC"]; currentIndex: ["ufs","emmc"].indexOf(s.memory); enabled: !s.busy; displayText: currentIndex<0 ? "Choose type" : currentText; onActivated: backend.option("memory",currentIndex===0 ? "ufs" : "emmc") }
                        }
                        RowLayout { Caption { text: "RAWPROGRAM" } Item { Layout.fillWidth: true } Action { text: "+ XML"; enabled: !s.busy; onClicked: backend.chooseFiles("raw") } }
                        Repeater { model: s.raws; FileRow { required property string modelData; filePath: modelData; kind: "raw"; chosen: s.selectedRaws.indexOf(modelData)>=0 } }
                        RowLayout { Caption { text: "PATCH" } Item { Layout.fillWidth: true } Action { text: "+ XML"; enabled: !s.busy; onClicked: backend.chooseFiles("patch") } }
                        Repeater { model: s.patches; FileRow { required property string modelData; filePath: modelData; kind: "patch"; chosen: s.selectedPatches.indexOf(modelData)>=0 } }
                        RowLayout { Layout.fillWidth: true
                            Action { text: "Open in Finder"; enabled: !!s.folder && !s.busy; onClicked: backend.openFolder() }
                            Action { text: "Enter EDL…"; enabled: !s.busy && selectedDevice()!==null && selectedDevice().mode === "adb" && selectedDevice().status === "device"; onClicked: edlDialog.open() }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: line }
                        RowLayout { Layout.fillWidth: true
                            Caption { text: "APPEARANCE"; Layout.fillWidth: true }
                            Combo { Layout.fillWidth: false; Layout.preferredWidth: 150; model: ["System","Light","Dark"]; currentIndex: ["system","light","dark"].indexOf(s.theme); enabled: !s.busy; onActivated: backend.option("theme",["system","light","dark"][currentIndex]) }
                        }
                        Repeater { model: s.environment.filter(function(c) { return !c.ok && c.name !== "Hardware validation" }); Label { required property var modelData; text: modelData.name+": "+modelData.detail; color: warning; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; font.pixelSize: 11 } }
                    }
                    ColumnLayout { visible: configTabs.currentIndex === 1; Layout.fillWidth: true; spacing: 10
                        Label { text: s.loader; color: muted; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; font.pixelSize: 11 }
                        Label { text: s.plan.programs ? s.memory.toUpperCase()+" · "+s.plan.programs.length+" writes" : "Check firmware to build the plan"; color: ink; font.weight: Font.DemiBold }
                        Repeater { model: s.plan.programs || []; delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 2
                            Label { text: modelData.label+" · LUN "+modelData.lun+" · "+bytes(modelData.sectors*modelData.sector_size); color: ink; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideMiddle }
                            Label { text: modelData.start+" + "+modelData.sectors+" sectors"; color: muted; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle }
                        } }
                        Repeater { model: s.plan.patches || []; Label { required property var modelData; text: "PATCH · LUN "+modelData.lun+" · "+modelData.start+" + "+modelData.byte_offset+" B"; color: muted; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle } }
                        Repeater { model: s.warnings.length ? s.warnings : s.layoutNotes; Label { required property string modelData; text: "• "+modelData; color: warning; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 } }
                    }
                }
            }
            Label { text: "File changes require a new check. XML saves keep a backup."; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
        }
        footer: DialogActions { cancelText: "Close"; acceptText: "Check changes"; acceptEnabled: !s.busy && !!s.folder; onConfirmed: { configDialog.close(); backend.check() } onCancelled: configDialog.close() }
    }
    Modal {
        id: historyDialog; objectName: "historyDialog"; title: "History"
        height: Math.min(win.height-40,650)
        contentItem: ColumnLayout { spacing: 10
            RowLayout { Layout.fillWidth: true
                Label { text: "Recent tasks"; color: muted; Layout.fillWidth: true; font.pixelSize: 11 }
                Action { text: "Export…"; enabled: !s.busy; onClicked: exportDialog.open() }
            }
            Label { visible: s.history.length === 0; text: "No tasks yet"; color: muted }
            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10; model: s.history
                delegate: Rectangle { required property var modelData; width: ListView.view.width; height: recordCol.implicitHeight+24; radius: 8; color: bg; border.color: line
                    ColumnLayout { id: recordCol; anchors.fill: parent; anchors.margins: 12; spacing: 6
                        RowLayout { Layout.fillWidth: true
                            Label { text: modelData.action.toUpperCase(); color: ink; font.pixelSize: 11; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Label { text: stateNames[modelData.status] || modelData.status; color: modelData.status === "failed" ? error : accent; font.pixelSize: 11 }
                        }
                        Label { text: new Date(modelData.started*1000).toLocaleString(Qt.locale(),"yyyy-MM-dd hh:mm:ss"); color: muted; font.pixelSize: 10 }
                        Label { text: modelData.message || ""; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
            Action { text: "Close"; Layout.alignment: Qt.AlignRight; onClicked: historyDialog.close() }
        }
    }
    Modal {
        id: folderDialog; objectName: "folderConfirmation"; title: "Use this firmware folder?"
        onClosed: if (s.folderToConfirm) backend.cancelFolderSelection()
        contentItem: ScrollView {
            id: folderSummaryScroll; clip: true; contentWidth: availableWidth
            implicitHeight: Math.min(folderSummary.implicitHeight,win.height-190)
            ColumnLayout { id: folderSummary; width: folderSummaryScroll.availableWidth; spacing: 12
            Label { text: s.folderToConfirm; color: ink; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; font.pixelSize: 12 }
            Label { text: "Confirm to scan and check. Flashing starts separately."; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 11 }
        }
        }
        footer: DialogActions { acceptText: "Check folder"; acceptEnabled: s.canChoosePackage; onConfirmed: { backend.confirmFolder(); folderDialog.close() } onCancelled: folderDialog.close() }
    }
    Modal {
        id: flashDialog; objectName: "flashConfirmation"; title: "Confirm flash"
        contentItem: ScrollView {
            id: flashSummaryScroll; clip: true; contentWidth: availableWidth
            implicitHeight: Math.min(flashSummary.implicitHeight,win.height-190)
            ColumnLayout { id: flashSummary; width: flashSummaryScroll.availableWidth; spacing: 12
            Label { text: "This overwrites the selected device's storage."; color: ink; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Label { text: selectedDevice() ? selectedDevice().name+"\n"+(selectedDevice().serial || selectedDevice().id) : "No device selected"; color: ink; font.weight: Font.DemiBold; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
            Label { text: s.folder; color: muted; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true; font.pixelSize: 11 }
            RowLayout { Layout.fillWidth: true
                Label { text: s.memory.toUpperCase()+" · "+(s.plan.programs || []).length+" writes"+" · "+(s.plan.patches || []).length+" patches"; color: muted; font.pixelSize: 11; Layout.fillWidth: true }
                Action { text: "Plan…"; onClicked: { flashDialog.close(); openConfiguration(1) } }
            }
            Label { text: "ADB enters EDL automatically. Reboot follows successful verification. Confirm this firmware is intended for this device."; color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
        }
        footer: DialogActions { acceptText: "Flash & reboot"; acceptEnabled: s.canStart; onConfirmed: flashDialog.accept(); onCancelled: flashDialog.reject() }
        onAccepted: backend.startFlash()
    }
    Modal {
        id: xmlDialog; objectName: "xmlEditorDialog"; title: "Edit XML"
        width: Math.min(win.width-28,900); height: Math.min(win.height-28,700)
        onClosed: if (s.editorPath) backend.cancelEdit()
        contentItem: ColumnLayout { spacing: 10
            Label { text: s.editorPath; color: muted; elide: Text.ElideMiddle; Layout.fillWidth: true; font.pixelSize: 11 }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea { id: xmlText; selectByMouse: true; wrapMode: TextEdit.NoWrap; color: ink; font.family: "Menlo"; font.pixelSize: 12; background: Rectangle { color: bg; border.color: line; radius: 6 } }
            }
            Label { text: "Original backed up when saved."; color: muted; font.pixelSize: 11 }
            Label { visible: !!s.editorError; text: s.editorError; color: error; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
        footer: DialogActions { acceptText: "Save & check"; onConfirmed: { if (backend.saveXml(xmlText.text)) xmlDialog.close() } onCancelled: xmlDialog.close() }
    }
    Modal { id: edlDialog; title: "Enter EDL?"; contentItem: ColumnLayout { Label { Layout.fillWidth: true; text: "Restart the selected ADB device into EDL mode?"; color: ink; wrapMode: Text.WordWrap } } footer: DialogActions { acceptText: "Enter EDL"; onConfirmed: edlDialog.accept(); onCancelled: edlDialog.reject() } onAccepted: backend.enterEdl() }
    Modal { id: resetDialog; title: "Reboot device?"; contentItem: ColumnLayout { Label { Layout.fillWidth: true; text: "Review the console result before rebooting. Verification may be incomplete."; color: ink; wrapMode: Text.WordWrap } } footer: DialogActions { acceptText: "Reboot"; onConfirmed: resetDialog.accept(); onCancelled: resetDialog.reject() } onAccepted: backend.run("reset") }
    Modal { id: exportDialog; title: "Export diagnostics?"; contentItem: ColumnLayout { Label { Layout.fillWidth: true; text: "Includes 20 recent tasks, local paths and device IDs. No firmware images; nothing is uploaded."; color: ink; wrapMode: Text.WordWrap } } footer: DialogActions { acceptText: "Export"; onConfirmed: exportDialog.accept(); onCancelled: exportDialog.reject() } onAccepted: backend.export() }
    Modal {
        id: closeDialog; objectName: "closeProtection"; title: "Device operation running"
        contentItem: ColumnLayout { Label { Layout.fillWidth: true; text: "Stop at a safe operation boundary before exiting."; color: ink; wrapMode: Text.WordWrap } }
        footer: DialogActions { cancelText: "Keep running"; acceptText: "Stop & exit"; onConfirmed: { backend.quitAfterStop(); closeDialog.close() } onCancelled: closeDialog.close() }
    }
    Connections {
        target: backend
        function onEditorRequested() { xmlText.text=s.editorText; xmlDialog.open() }
        function onFolderConfirmationRequested() { folderDialog.open() }
        function onChanged() { if (s.busy && stageOrder[s.state] !== undefined) lastStep=stageOrder[s.state] }
    }
    onClosing: function(close) {
        if (s.closeBlocked) { close.accepted=false; closeDialog.open() }
        else if (s.busy) { close.accepted=false; backend.quitAfterStop() }
    }
}
