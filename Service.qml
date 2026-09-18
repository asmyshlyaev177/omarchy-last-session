import QtQuick
import Quickshell.Io

// Headless service: shortly after the shell starts, reopen the last session,
// then keep re-saving it. Both processes are children of this object, so a
// shell restart or a plugin disable ends them and the next instance starts
// its own; restore itself refuses to run into an already populated desktop.
Item {
  id: root

  // Injected by omarchy-shell.
  property var shell: null
  property var manifest: null

  readonly property string script: Qt.resolvedUrl("bin/omarchy-last-session").toString().replace(/^file:\/\//, "")

  Timer {
    // Autostarted apps are still mapping their windows; the script's guard
    // only counts what is already there.
    interval: 2000
    running: true
    onTriggered: restore.running = true
  }

  Process {
    id: restore
    command: [root.script, "restore"]
    stdout: SplitParser { onRead: data => console.log(data) }
    stderr: SplitParser { onRead: data => console.warn(data) }
    onExited: daemon.running = true
  }

  Process {
    id: daemon
    command: [root.script, "daemon"]
    stdout: SplitParser { onRead: data => console.log(data) }
    stderr: SplitParser { onRead: data => console.warn(data) }
  }
}
