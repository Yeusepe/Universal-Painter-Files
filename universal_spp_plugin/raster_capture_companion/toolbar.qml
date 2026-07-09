import QtQuick 2.3
import QtQuick.Dialogs 1.0
import QtQuick.Controls 1.4

import "raster_capture.js" as RasterCapture

Button {
  id: control
  width: 150
  height: 28
  text: "USPP Raster Capture"
  tooltip: "Capture Universal SPP raster fallbacks"
  property string planPath: ""

  function localPath(url) {
    var s = url.toString()
    if (s.indexOf("file:///") === 0) {
      s = s.substring(8)
      if (Qt.platform.os === "windows") {
        return decodeURIComponent(s).replace(/\//g, "\\")
      }
      return "/" + decodeURIComponent(s)
    }
    if (s.indexOf("file://") === 0) {
      return decodeURIComponent(s.substring(7))
    }
    return decodeURIComponent(s)
  }

  onClicked: {
    planDialog.open()
  }

  FileDialog {
    id: planDialog
    title: "Choose Universal SPP raster-plan JSON"
    nameFilters: [ "JSON files (*.json)", "All files (*)" ]
    onAccepted: {
      control.planPath = localPath(fileUrl)
      outDialog.open()
    }
  }

  FileDialog {
    id: outDialog
    title: "Choose capture output manifest"
    selectExisting: false
    nameFilters: [ "JSON files (*.json)", "All files (*)" ]
    onAccepted: {
      RasterCapture.capture(control.planPath, localPath(fileUrl))
    }
  }
}
