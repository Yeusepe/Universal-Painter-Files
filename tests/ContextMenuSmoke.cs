// Compile with installer/ContextMenu.cs, /main:ContextMenuSmoke, and WinForms/Drawing references.
// Run next to uspp_tool.exe: ContextMenuSmoke.exe <fixture.spp> <compressed:true|false> <capture.png> <expected exit>
using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

class ContextMenuSmoke {
    static T Find<T>(Control root, string text) where T : Control {
        foreach (Control child in root.Controls) {
            if (child is T && child.Text == text) return (T)child;
            T found = Find<T>(child, text);
            if (found != null) return found;
        }
        return null;
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string failure = null;
        ConversionWindow window = null;
        using (var timer = new Timer { Interval = 100 }) {
            DateTime deadline = DateTime.UtcNow.AddSeconds(60);
            bool capturedRunning = false;
            DateTime completed = DateTime.MaxValue;
            timer.Tick += delegate {
                try {
                    if (window == null) {
                        foreach (Form open in Application.OpenForms) {
                            if (open is ConversionWindow) { window = (ConversionWindow)open; break; }
                        }
                        if (window == null) throw new Exception("Conversion window not opened.");
                    }
                    Button close = Find<Button>(window, "Close");
                    if (!close.Enabled) {
                        if (DateTime.UtcNow > deadline) throw new Exception("Conversion timed out.");
                        if (!capturedRunning) {
                            using (var image = new Bitmap(window.Width, window.Height)) {
                                window.DrawToBitmap(image, new Rectangle(Point.Empty, window.Size));
                                image.Save(args[2] + ".running.png");
                            }
                            capturedRunning = true;
                        }
                        return;
                    }
                    if (completed == DateTime.MaxValue) { completed = DateTime.UtcNow; return; }
                    if ((DateTime.UtcNow - completed).TotalMilliseconds < 400) return;
                    if (window.Result != Int32.Parse(args[3])) throw new Exception("Unexpected conversion result: " + window.Result);
                    if (Find<Button>(window, "Open folder").Enabled != (window.Result == 0)) throw new Exception("Invalid Open folder state.");
                    string title = window.Result == 0 ? "Universal project created" : "Could not convert project";
                    if (Find<Label>(window, title) == null) throw new Exception("Missing result heading.");
                    using (var image = new Bitmap(window.Width, window.Height)) {
                        window.DrawToBitmap(image, new Rectangle(Point.Empty, window.Size));
                        image.Save(args[2]);
                    }
                    timer.Stop();
                    close.PerformClick();
                } catch (Exception error) {
                    failure = error.ToString();
                    File.WriteAllText(args[2] + ".error.txt", failure);
                    Environment.Exit(1);
                }
            };
            timer.Start();
            var commandArgs = new System.Collections.Generic.List<string>();
            if (Boolean.Parse(args[1])) commandArgs.Add("--compress");
            if (args.Length > 4 && Boolean.Parse(args[4])) commandArgs.Add("--replace-original");
            commandArgs.Add(args[0]);
            int result = ContextMenu.Run(commandArgs.ToArray());
            if (result != Int32.Parse(args[3])) throw new Exception("Unexpected launcher exit code: " + result);
        }
        return failure == null ? 0 : 1;
    }
}
