import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

ApplicationWindow {
    id: win
    width: 1240; height: 900; minimumWidth: 1000; minimumHeight: 740
    visible: true
    title: "Qualcomm Flash Tool"
    readonly property var s: backend ? backend.data : ({})
    readonly property bool zh: s.language === "zh"
    readonly property bool dark: s.theme === "dark" || (s.theme === "system" && Qt.styleHints.colorScheme === Qt.Dark)
    readonly property color bg: dark ? "#12161c" : "#f5f6f8"
    readonly property color surface: dark ? "#1c222b" : "#ffffff"
    readonly property color ink: dark ? "#eef1f5" : "#18222f"
    readonly property color muted: dark ? "#99a6b8" : "#718093"
    readonly property color line: dark ? "#303947" : "#e4e8ee"
    readonly property color accent: dark ? "#75d7c4" : "#087c70"
    readonly property var stateNames: ({idle:tr("Idle","空闲"),checking:tr("Checking","检查中"),ready:tr("Ready","就绪"),connecting:tr("Connecting","连接中"),flashing:tr("Writing images","写入镜像"),patching:tr("Applying patches","应用补丁"),verifying:tr("Verifying","核验中"),rebooting:tr("Rebooting","重启中"),stopping:tr("Stopping","停止中"),success:tr("Completed","已完成"),failed:tr("Failed","失败"),incomplete:tr("Verification incomplete","核验不完整"),interrupted:tr("Interrupted","已中断"),reboot_failed:tr("Reboot failed","重启失败")})
    color: bg
    font.family: "Helvetica Neue"
    font.pixelSize: 13
    palette.text: ink; palette.windowText: ink; palette.buttonText: ink
    palette.base: surface; palette.window: bg; palette.highlight: accent
    function tr(en, cn) { return zh ? cn : en }
    function basename(path) { return path ? path.split("/").pop() : "" }
    function bytes(n) { return n > 1073741824 ? (n/1073741824).toFixed(2)+" GB" : (n/1048576).toFixed(1)+" MB" }
    function selectedDevice() { return s.deviceIndex >= 0 && s.deviceIndex < s.devices.length ? s.devices[s.deviceIndex] : null }

    component Action: Button {
        id: button
        property bool primary: false
        property bool danger: false
        implicitHeight: 38; leftPadding: 16; rightPadding: 16
        font.pixelSize: 12; font.weight: Font.DemiBold
        contentItem: Text { text: button.text; font: button.font; color: !button.enabled ? muted : button.primary ? "#ffffff" : button.danger ? "#cf5656" : ink; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: button.primary ? (button.enabled ? "#087c70" : (dark ? "#283a3b" : "#c8d9d6")) : button.hovered && button.enabled ? (dark ? "#303b49" : "#edf1f5") : surface; border.width: button.primary ? 0 : 1; border.color: line }
        opacity: enabled ? 1 : 0.7
    }
    component Card: Rectangle {
        color: surface; radius: 13; border.color: line
        Layout.fillWidth: true
    }
    component Caption: Label { color: muted; font.pixelSize: 11; font.weight: Font.DemiBold; font.letterSpacing: 1.2 }
    component Combo: ComboBox {
        id: control
        implicitHeight: 38; Layout.fillWidth: true
        contentItem: Text { leftPadding: 10; rightPadding: 28; text: control.displayText; color: control.enabled ? ink : muted; elide: Text.ElideMiddle; verticalAlignment: Text.AlignVCenter; font.pixelSize: 12 }
        indicator: Text { text: "⌄"; color: muted; font.pixelSize: 18; anchors.right: parent.right; anchors.rightMargin: 11; anchors.verticalCenter: parent.verticalCenter }
        background: Rectangle { radius: 7; color: surface; border.color: control.activeFocus ? accent : line }
        delegate: ItemDelegate { required property var modelData; width: control.width; text: typeof modelData === "string" ? modelData : modelData[control.textRole]; highlighted: control.highlightedIndex === index }
    }
    component SectionTitle: Label { color: ink; font.pixelSize: 16; font.weight: Font.DemiBold }
    component FileRow: Rectangle {
        required property string filePath
        required property string kind
        required property bool chosen
        Layout.fillWidth: true; implicitHeight: 40
        color: check.hovered ? (dark ? "#252e39" : "#f6f8fa") : "transparent"
        RowLayout {
            anchors.fill: parent; spacing: 6
            CheckBox { id: check; checked: chosen; enabled: !s.busy; onClicked: backend.toggleFile(kind,filePath,checked) }
            Label { text: basename(filePath); color: ink; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideMiddle; ToolTip.visible: hover.hovered; ToolTip.text: filePath; HoverHandler { id: hover } }
            Label { text: "XML"; color: muted; font.pixelSize: 10; rightPadding: 12 }
        }
    }

    RowLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            Layout.preferredWidth: 210; Layout.fillHeight: true; color: dark ? "#171c23" : "#edf0f4"
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: line }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 20; spacing: 6
                RowLayout {
                    Layout.topMargin: 14; spacing: 10
                    Rectangle { width: 34; height: 34; radius: 10; color: "#087c70"; Text { anchors.centerIn: parent; text: "Q"; color: "white"; font.pixelSize: 23; font.weight: Font.Bold } }
                    ColumnLayout { spacing: 1; Label { text: "QUALCOMM"; font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 1.2; color: ink } Label { text: "Flash Tool"; font.pixelSize: 12; color: muted } }
                }
                Caption { text: tr("WORKSPACE","工作空间"); Layout.topMargin: 36; Layout.bottomMargin: 8 }
                Repeater {
                    model: [{name:tr("Flash","刷机"),icon:"↗"},{name:tr("Devices","设备"),icon:"▣"},{name:tr("History","历史"),icon:"◷"},{name:tr("Settings","设置"),icon:"⚙"}]
                    delegate: Rectangle {
                        required property int index
                        required property var modelData
                        Layout.fillWidth: true; height: 43; radius: 8
                        color: s.page === index ? surface : navMouse.containsMouse ? (dark ? "#242c36" : "#e5eaf0") : "transparent"
                        RowLayout { anchors.fill: parent; anchors.leftMargin: 12; spacing: 12; Label { text: modelData.icon; color: s.page===index ? accent : muted; font.pixelSize: 19; Layout.preferredWidth: 20 } Label { text: modelData.name; color: s.page===index ? ink : muted; font.weight: s.page===index ? Font.DemiBold : Font.Normal; Layout.fillWidth: true } }
                        MouseArea { id: navMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: backend.setPage(index) }
                    }
                }
                Item { Layout.fillHeight: true }
                Rectangle { Layout.fillWidth: true; height: 1; color: line }
                Label { text: tr("Internal preview","内部预览版"); color: muted; font.pixelSize: 11; Layout.topMargin: 10 }
                Label { text: "Version 2.0.0 · Apple Silicon"; color: muted; font.pixelSize: 10 }
                Label { text: tr("Hardware validation pending","待实机验收"); color: muted; font.pixelSize: 10; Layout.bottomMargin: 8 }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 86; color: surface
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: line }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 32; anchors.rightMargin: 32
                    ColumnLayout { spacing: 5
                        Label { text: [tr("Flash workspace","刷机工作台"),tr("Connected devices","连接的设备"),tr("Task history","任务历史"),tr("Settings","设置")][s.page]; font.pixelSize: 23; font.weight: Font.DemiBold; color: ink }
                        Label { text: tr("A clear path from firmware to a verified result.","从刷机包到核验结果，每一步清晰可见。"); font.pixelSize: 12; color: muted }
                    }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        width: statusRow.implicitWidth+22; height: 28; radius: 14; color: dark ? "#23332f" : "#e8f4ef"
                        Row { id: statusRow; anchors.centerIn: parent; spacing: 7; Rectangle { width: 6; height: 6; radius: 3; color: accent; anchors.verticalCenter: parent.verticalCenter } Text { text: stateNames[s.state] || s.state; color: accent; font.pixelSize: 11; font.weight: Font.DemiBold } }
                    }
                }
            }
            ScrollView {
                id: mainScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                contentWidth: availableWidth
                ColumnLayout {
                    width: mainScroll.availableWidth; spacing: 18
                    Item { Layout.preferredHeight: 5 }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.leftMargin: 32; Layout.rightMargin: 32; spacing: 18
                        // Flash workspace
                        ColumnLayout {
                            visible: s.page === 0; Layout.fillWidth: true; spacing: 18
                            RowLayout {
                                Layout.fillWidth: true; spacing: 0
                                Repeater {
                                    model: [tr("01  Device","01  设备"),tr("02  Firmware","02  刷机包"),tr("03  Review & flash","03  检查与刷写")]
                                    delegate: RowLayout {
                                        required property int index; required property string modelData
                                        Layout.fillWidth: true
                                        Label { text: modelData; color: index===0 || (index===1 && s.folder) || (index===2 && Object.keys(s.plan).length) ? accent : muted; font.pixelSize: 11; font.weight: Font.DemiBold }
                                        Rectangle { visible: index<2; Layout.fillWidth: true; Layout.leftMargin: 12; Layout.rightMargin: 20; height: 1; color: line }
                                    }
                                }
                            }
                            Card {
                                implicitHeight: deviceCard.implicitHeight+36
                                ColumnLayout { id: deviceCard; anchors.fill: parent; anchors.margins: 18; spacing: 12
                                    RowLayout { Layout.fillWidth: true; SectionTitle { text: tr("Target device","目标设备") } Item { Layout.fillWidth: true } Label { text: tr("One device at a time","单设备执行"); color: muted; font.pixelSize: 11 } }
                                    RowLayout { Layout.fillWidth: true; spacing: 10
                                        Combo {
                                            model: s.devices.map(function(d) {return d.name+" · "+d.status+" · "+(d.serial || d.id)})
                                            currentIndex: s.deviceIndex; enabled: !s.busy
                                            displayText: currentIndex<0 ? tr("Select a device — refresh to discover","选择设备 — 点击刷新发现设备") : currentText
                                            onActivated: backend.selectDevice(currentIndex)
                                        }
                                        Action { text: tr("Refresh","刷新"); enabled: !s.busy; onClicked: backend.refresh() }
                                        Action { text: tr("Enter EDL","进入 EDL"); enabled: !s.busy && selectedDevice() !== null && selectedDevice().mode === "adb" && selectedDevice().status === "device"; onClicked: edlDialog.open() }
                                    }
                                    Label { text: tr("ADB authorization and EDL identity are checked separately. Select the device again after a mode change.","ADB 授权与 EDL 身份分别检查；切换模式后请重新选择设备。"); wrapMode: Text.WordWrap; Layout.fillWidth: true; color: muted; font.pixelSize: 11 }
                                }
                            }
                            Card {
                                implicitHeight: firmwareCard.implicitHeight+36
                                DropArea { anchors.fill: parent; onDropped: function(drop) { if (drop.hasUrls && !s.busy) backend.dropFolder(drop.urls[0]) } }
                                ColumnLayout { id: firmwareCard; anchors.fill: parent; anchors.margins: 18; spacing: 14
                                    RowLayout { SectionTitle { text: tr("Firmware package","刷机包") } Item { Layout.fillWidth: true } Action { text: tr("Choose folder","选择文件夹"); enabled: !s.busy; onClicked: backend.chooseFolder() } }
                                    Rectangle {
                                        Layout.fillWidth: true; implicitHeight: 68; radius: 8; color: dark ? "#182b2b" : "#f0f8f5"; border.color: dark ? "#2d4542" : "#d7e9e2"
                                        ColumnLayout { anchors.fill: parent; anchors.margins: 13; spacing: 4
                                            Label { text: s.folder ? basename(s.folder) : tr("Drop an extracted firmware folder here","拖入已解压的刷机包文件夹"); color: ink; font.weight: Font.DemiBold; font.pixelSize: 13; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                            Label { text: s.folder || tr("rawprogram XML + patch XML + images + Firehose loader","rawprogram XML + patch XML + 镜像 + Firehose loader"); color: muted; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                        }
                                    }
                                    RowLayout { Layout.fillWidth: true; spacing: 12
                                        ColumnLayout { Layout.fillWidth: true; spacing: 6; Caption { text: "FIREHOSE LOADER" }
                                            RowLayout { Layout.fillWidth: true
                                                Combo { model: s.loaders; currentIndex: s.loaders.indexOf(s.loader); enabled: !s.busy; displayText: s.loader ? basename(s.loader) : tr("Select a compatible .elf / .mbn","选择兼容的 .elf / .mbn"); onActivated: backend.selectLoader(s.loaders[currentIndex]) }
                                                Action { text: "…"; enabled: !s.busy; onClicked: backend.chooseFiles("loader") }
                                            }
                                        }
                                        ColumnLayout { Layout.preferredWidth: 135; spacing: 6; Caption { text: tr("STORAGE","存储类型") } Combo { model: ["UFS","eMMC"]; currentIndex: s.memory==="ufs" ? 0 : 1; enabled: !s.busy; onActivated: backend.option("memory", currentIndex===0 ? "ufs" : "emmc") } }
                                    }
                                    RowLayout { visible: s.raws.length>0 || s.patches.length>0; Layout.fillWidth: true; spacing: 16
                                        ColumnLayout { Layout.fillWidth: true
                                            RowLayout { Caption { text: "RAWPROGRAM" } Item { Layout.fillWidth: true } ToolButton { text: "+"; enabled: !s.busy; onClicked: backend.chooseFiles("raw") } }
                                            Repeater { model: s.raws; FileRow { required property string modelData; filePath: modelData; kind: "raw"; chosen: s.selectedRaws.indexOf(modelData)>=0 } }
                                        }
                                        ColumnLayout { Layout.fillWidth: true
                                            RowLayout { Caption { text: "PATCH" } Item { Layout.fillWidth: true } ToolButton { text: "+"; enabled: !s.busy; onClicked: backend.chooseFiles("patch") } }
                                            Repeater { model: s.patches; FileRow { required property string modelData; filePath: modelData; kind: "patch"; chosen: s.selectedPatches.indexOf(modelData)>=0 } }
                                        }
                                    }
                                    RowLayout { visible: s.folder && s.raws.length===0; Action { text: tr("Add rawprogram XML","添加 rawprogram XML"); enabled: !s.busy; onClicked: backend.chooseFiles("raw") } Action { text: tr("Add patch XML","添加 patch XML"); enabled: !s.busy; onClicked: backend.chooseFiles("patch") } }
                                    Rectangle { Layout.fillWidth: true; height: 1; color: line }
                                    RowLayout { Layout.fillWidth: true
                                        Label { text: tr("Importing files never connects to Firehose.","导入文件不会连接 Firehose。"); color: muted; font.pixelSize: 11; Layout.fillWidth: true }
                                        Action { text: tr("Validate files","检查文件"); enabled: !s.busy && s.folder!=="" && s.loader!==""; onClicked: backend.validate() }
                                    }
                                }
                            }
                            Card {
                                visible: Object.keys(s.plan).length>0; implicitHeight: reviewCard.implicitHeight+36
                                ColumnLayout { id: reviewCard; anchors.fill: parent; anchors.margins: 18; spacing: 12
                                    RowLayout { SectionTitle { text: tr("Execution plan","执行计划") } Item { Layout.fillWidth: true } Label { text: s.plan.programs ? s.plan.programs.length+tr(" image writes"," 项镜像写入") : ""; color: muted } }
                                    Label { text: tr("Model / firmware compatibility is not established automatically. Review the selected loader and write ranges.","不会自动确认机型与固件匹配；请核对 loader 和写入范围。"); color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                    Repeater { model: s.plan.programs || []; delegate: RowLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Label { text: modelData.label; color: ink; Layout.preferredWidth: 125; elide: Text.ElideMiddle }
                                        Label { text: "LUN "+modelData.lun+" · "+modelData.start+" + "+modelData.sectors+tr(" sectors"," 扇区"); color: muted; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                        Label { text: bytes(modelData.sectors*modelData.sector_size); color: muted }
                                    } }
                                    Label { text: s.plan.patches ? s.plan.patches.length+tr(" patch operations · applied after all image writes"," 项 patch 操作 · 在全部镜像写入后执行") : ""; color: muted; font.pixelSize: 11 }
                                    Repeater { model: s.warnings; Label { required property string modelData; text: "• "+modelData; color: dark ? "#e5b982" : "#966319"; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                                    Repeater { model: Object.keys(s.capacities); Label { required property string modelData; text: "LUN "+modelData+" · "+s.capacities[modelData]+tr(" sectors confirmed"," 扇区已确认"); color: accent; font.pixelSize: 11 } }
                                    RowLayout { Layout.fillWidth: true
                                        Action { text: tr("Inspect device","检查设备"); enabled: !s.busy && selectedDevice()!==null && selectedDevice().mode==="edl"; onClicked: inspectDialog.open() }
                                        Item { Layout.fillWidth: true }
                                        CheckBox { text: tr("Reboot after verified success","核验通过后重启"); checked: s.reboot; enabled: !s.busy; onClicked: backend.option("reboot",checked) }
                                        Action { text: tr("Start flash","开始刷写"); primary: true; enabled: !s.busy && Object.keys(s.capacities).length>0; onClicked: flashDialog.open() }
                                    }
                                }
                            }
                        }
                        // Devices page
                        ColumnLayout {
                            visible: s.page===1; Layout.fillWidth: true; spacing: 18
                            RowLayout { SectionTitle { text: tr("Available devices","可用设备") } Item { Layout.fillWidth: true } Action { text: tr("Refresh devices","刷新设备"); enabled: !s.busy; onClicked: backend.refresh() } }
                            Card { visible: s.devices.length===0; implicitHeight: 130; Label { anchors.centerIn: parent; text: tr("No devices discovered. Connect a device, then refresh.","尚未发现设备。连接设备后点击刷新。"); color: muted } }
                            Repeater { model: s.devices; delegate: Card {
                                required property var modelData; required property int index
                                implicitHeight: 100
                                RowLayout { anchors.fill: parent; anchors.margins: 20
                                    ColumnLayout { Layout.fillWidth: true; Label { text: modelData.name; color: ink; font.pixelSize: 15; font.weight: Font.DemiBold } Label { text: modelData.mode.toUpperCase()+" · "+modelData.status+" · "+(modelData.serial || modelData.id); color: muted; Layout.fillWidth: true; elide: Text.ElideMiddle } }
                                    Action { text: s.deviceIndex===index ? tr("Selected","已选择") : tr("Select","选择"); enabled: !s.busy; onClicked: backend.selectDevice(index) }
                                }
                            } }
                            SectionTitle { text: tr("Environment diagnostics","环境诊断"); Layout.topMargin: 10 }
                            Repeater { model: s.environment; delegate: Card {
                                required property var modelData
                                implicitHeight: 72
                                RowLayout { anchors.fill: parent; anchors.margins: 18
                                    Label { text: modelData.ok ? "✓" : "○"; color: modelData.ok ? accent : muted; font.pixelSize: 20; Layout.preferredWidth: 28 }
                                    ColumnLayout { Layout.fillWidth: true; Label { text: modelData.name; color: ink; font.weight: Font.DemiBold } Label { text: modelData.detail; color: muted; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle } }
                                }
                            } }
                            Repeater { model: s.warnings; Label { required property string modelData; text: modelData; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true } }
                        }
                        // History page
                        ColumnLayout {
                            visible: s.page===2; Layout.fillWidth: true; spacing: 14
                            RowLayout { SectionTitle { text: tr("Recent tasks","最近任务") } Item { Layout.fillWidth: true } Action { text: tr("Export diagnostics","导出诊断"); onClicked: exportDialog.open() } }
                            Card { visible: s.history.length===0; implicitHeight: 140; Label { anchors.centerIn: parent; text: tr("Your checks and flash results will appear here.","检查和刷写结果将显示在这里。"); color: muted } }
                            Repeater { model: s.history; delegate: Card {
                                required property var modelData
                                implicitHeight: historyCol.implicitHeight+32
                                ColumnLayout { id: historyCol; anchors.fill: parent; anchors.margins: 16; spacing: 8
                                    RowLayout { Label { text: modelData.action.toUpperCase(); color: ink; font.weight: Font.DemiBold } Item { Layout.fillWidth: true } Label { text: new Date(modelData.started*1000).toLocaleString(Qt.locale(),"yyyy-MM-dd hh:mm:ss"); color: muted; font.pixelSize: 11 } Label { text: stateNames[modelData.status] || modelData.status; color: modelData.status==="failed" ? "#c65656" : accent; font.pixelSize: 11 } }
                                    Label { text: modelData.message || ""; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
                                    Label { visible: !!modelData.device; text: modelData.device ? modelData.device.name+" · "+(modelData.device.serial || modelData.device.id) : ""; color: muted; font.pixelSize: 11 }
                                }
                            } }
                        }
                        // Settings page
                        ColumnLayout {
                            visible: s.page===3; Layout.fillWidth: true; spacing: 18
                            Card {
                                implicitHeight: 240
                                ColumnLayout { anchors.fill: parent; anchors.margins: 22; spacing: 18
                                    SectionTitle { text: tr("Appearance & language","外观与语言") }
                                    RowLayout { Label { text: tr("Language","语言"); color: ink; Layout.fillWidth: true } Combo { Layout.fillWidth: false; Layout.preferredWidth: 230; model: ["English","简体中文"]; currentIndex: zh ? 1 : 0; enabled: !s.busy; onActivated: backend.option("language",currentIndex===0 ? "en" : "zh") } }
                                    RowLayout { Label { text: tr("Appearance","外观"); color: ink; Layout.fillWidth: true } Combo { Layout.fillWidth: false; Layout.preferredWidth: 230; model: [tr("System","跟随系统"),tr("Light","浅色"),tr("Dark","深色")]; currentIndex: ["system","light","dark"].indexOf(s.theme); enabled: !s.busy; onActivated: backend.option("theme",["system","light","dark"][currentIndex]) } }
                                    CheckBox { text: tr("Reboot automatically after verified success","必要核验通过后自动重启"); checked: s.reboot; enabled: !s.busy; onClicked: backend.option("reboot",checked) }
                                }
                            }
                            Card {
                                implicitHeight: aboutCol.implicitHeight+44
                                ColumnLayout { id: aboutCol; anchors.fill: parent; anchors.margins: 22; spacing: 12
                                    SectionTitle { text: "Qualcomm Flash Tool 2.0" }
                                    Label { text: tr("Built for macOS · Apple Silicon · Internal use","适用于 macOS · Apple Silicon · 内部使用"); color: muted }
                                    Label { text: tr("EDL engine: B. Kerler edlclient 3.62 (GPLv3). Third-party notices and source modifications are included with this build. Firmware and Firehose loaders are supplied by the user.","EDL 引擎：B. Kerler edlclient 3.62（GPLv3）。安装包包含第三方声明与修改记录。固件与 Firehose loader 由用户提供。"); color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
                                    Label { text: tr("No telemetry. Diagnostics are saved locally and exported only when you choose. Hardware compatibility has not yet been validated.","无遥测。诊断记录保存在本地，仅在你选择时导出。尚未完成实机兼容性验收。"); color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
                                }
                            }
                        }
                        // Shared task status and log
                        Card {
                            implicitHeight: taskCol.implicitHeight+36
                            ColumnLayout { id: taskCol; anchors.fill: parent; anchors.margins: 18; spacing: 10
                                RowLayout { SectionTitle { text: tr("Activity","任务状态") } Item { Layout.fillWidth: true } Label { visible: s.busy; text: s.elapsed+"s"; color: muted } Action { visible: s.busy; text: tr("Stop after current operation","当前操作后停止"); danger: true; enabled: s.state!=="stopping"; onClicked: backend.stop() } }
                                Label { text: s.message; color: s.state==="failed" ? "#c65656" : ink; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: 12 }
                                Label { visible: s.currentFile!==""; text: s.currentFile; elide: Text.ElideMiddle; Layout.fillWidth: true; color: muted; font.pixelSize: 11 }
                                ProgressBar { visible: s.busy; Layout.fillWidth: true; value: s.progress; indeterminate: !s.progressKnown || (s.state!=="flashing" && s.state!=="stopping") }
                                Label { visible: s.busy && s.progressKnown; text: bytes(s.transferred)+" / "+bytes(s.totalBytes)+" · "+Math.round(s.progress*100)+"%"; color: muted; font.pixelSize: 11 }
                                Repeater { model: s.result.verified || []; Label { required property string modelData; text: "✓ "+modelData; color: accent; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 } }
                                Repeater { model: s.result.unverified || []; Label { required property string modelData; text: "○ "+modelData; color: muted; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11 } }
                                RowLayout { visible: s.state==="incomplete" || s.state==="reboot_failed"; Label { text: tr("Review the result before rebooting.","重启前请核对结果。"); color: muted; Layout.fillWidth: true } Action { text: tr("Reboot device…","重启设备…"); enabled: !s.busy && selectedDevice()!==null; onClicked: resetDialog.open() } }
                                ToolButton { id: logToggle; background: Rectangle { radius: 6; color: logToggle.hovered ? (dark ? "#303b49" : "#edf1f5") : "transparent" } text: (checked ? "▾ " : "▸ ")+tr("Technical log","技术日志"); checkable: true; font.pixelSize: 11; palette.buttonText: muted }
                                ScrollView {
                                    visible: logToggle.checked; Layout.fillWidth: true; Layout.preferredHeight: 220; clip: true
                                    TextArea { text: s.logs || tr("No log output yet.","暂无日志。"); readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; font.family: "Menlo"; font.pixelSize: 10; color: muted; background: Rectangle { radius: 8; color: dark ? "#10151b" : "#f4f6f8" } }
                                }
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 14 }
                }
            }
        }
    }
    Dialog {
        id: flashDialog; title: tr("Confirm flash","确认刷写"); modal: true; anchors.centerIn: parent; width: 540
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: standardButton(Dialog.Ok).text=tr("Flash device","刷写设备")
        onAccepted: backend.run("flash")
        contentItem: Label { text: tr("This will overwrite the selected device's storage.\n\nTarget: ","将覆盖所选设备的存储内容。\n\n目标：")+(selectedDevice() ? selectedDevice().name+" · "+(selectedDevice().serial || selectedDevice().id) : "")+"\n"+tr("Package: ","刷机包：")+s.folder+"\n"+tr("Memory: ","存储：")+s.memory.toUpperCase()+"\n\n"+tr("Device model / firmware compatibility has not been established. All selected image writes run before patches. Failures stop the task without resetting the device.","尚未确认机型与固件匹配。全部选定镜像写入后再应用 patch。任何失败都会停止任务且不发送重启。"); wrapMode: Text.WordWrap; color: ink }
    }
    Dialog { id: inspectDialog; title: tr("Inspect EDL device","检查 EDL 设备"); modal: true; anchors.centerIn: parent; width: 450; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: backend.run("inspect"); contentItem: Label { text: tr("This uploads your selected Firehose loader to device RAM and queries storage geometry. It does not write firmware or reset the device.","会将所选 Firehose loader 上传至设备内存并查询存储信息。不会写入固件或重启设备。"); color: ink; wrapMode: Text.WordWrap } }
    Dialog { id: edlDialog; title: tr("Enter EDL mode","进入 EDL 模式"); modal: true; anchors.centerIn: parent; width: 440; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: backend.enterEdl(); contentItem: Label { text: tr("Send reboot edl to the selected authorized ADB device? Refresh and reselect it after the mode changes.","向选中的已授权 ADB 设备发送 reboot edl？切换模式后请刷新并重新选择设备。"); color: ink; wrapMode: Text.WordWrap } }
    Dialog { id: resetDialog; title: tr("Reset device","重启设备"); modal: true; anchors.centerIn: parent; width: 440; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: backend.run("reset"); contentItem: Label { text: tr("Send a reset command to the selected device? Verification may be incomplete. Confirm you have reviewed the task result.","向所选设备发送重启命令？核验可能尚不完整，请确认已核对任务结果。"); color: ink; wrapMode: Text.WordWrap } }
    Dialog { id: exportDialog; title: tr("Export local diagnostics","导出本地诊断"); modal: true; anchors.centerIn: parent; width: 440; standardButtons: Dialog.Ok | Dialog.Cancel; onAccepted: backend.export(); contentItem: Label { text: tr("The archive contains the latest 20 task records, local file paths and device identifiers. Firmware images and credentials are not included. Nothing is uploaded.","压缩包包含最近 20 项任务记录、本地路径和设备标识，不包含固件镜像或凭据，不会上传。"); color: ink; wrapMode: Text.WordWrap } }
    Dialog { id: closeDialog; title: tr("A task is still running","任务仍在运行"); modal: true; anchors.centerIn: parent; width: 440; standardButtons: Dialog.Ok; contentItem: Label { text: tr("Request a stop and wait for the task to finish before closing this window. Closing the app forcibly can leave an incomplete flash.","请请求停止并等待任务结束后关闭窗口。强制退出可能留下未完成的刷写。"); color: ink; wrapMode: Text.WordWrap } }
    onClosing: function(close) { if (s.busy) { close.accepted=false; closeDialog.open() } }
}
