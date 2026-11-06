// Visual-only snapshots. DrawToBitmap does not start a conversion or touch a project.
using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

class PreviewWindow : ConversionWindow {
    public PreviewWindow(bool replace) : base(@"C:\Projects\Winterpaw project.spp", true, replace) { }
    protected override void OnShown(EventArgs e) { } // Suppress conversion startup in visual previews.
}

class ContextMenuPreview {
    static void Call(ConversionWindow window, string method, params object[] args) {
        typeof(ConversionWindow).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window, args);
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        foreach (string state in new string[] { "progress", "verifying", "compact", "details-empty", "recycle", "buttons" }) {
            using (var window = new PreviewWindow(state == "recycle")) {
                if (state == "compact") window.ClientSize = new Size(540, window.ClientSize.Height);
                if (state == "details-empty") Call(window, "ExpandDetails", true);
                window.Show();
                Application.DoEvents();
                if (state == "buttons") {
                    Action<Control> style = null;
                    style = delegate(Control parent) {
                        foreach (Control child in parent.Controls) {
                            var button = child as Button;
                            if (button != null) {
                                button.Enabled = true;
                                if (button.Text == "Details") typeof(Button).GetMethod("OnMouseEnter", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button, new object[] { EventArgs.Empty });
                                if (button.Text == "Close") typeof(Button).GetMethod("OnMouseDown", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button, new object[] { new MouseEventArgs(MouseButtons.Left, 1, 20, 20, 0) });
                            }
                            style(child);
                        }
                    };
                    style(window);
                }
                Call(window, "ReadLine", "__USPP_PROGRESS__\t" + (state == "verifying" ? "-1" : "0.54") + "\t" + (state == "verifying" ? "Verifying archive..." : "Compressing textures..."));
                Call(window, "RefreshProgress");
                using (var image = new Bitmap(window.Width, window.Height)) {
                    window.DrawToBitmap(image, new Rectangle(Point.Empty, window.Size));
                    image.Save(Path.Combine(args[0], state + ".png"));
                }
            }
        }
        return 0;
    }
}
