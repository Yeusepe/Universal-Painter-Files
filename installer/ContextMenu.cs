using System;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

class ConversionWindow : Form {
    readonly string source;
    readonly bool compressed;
    readonly bool replaceOriginal;
    readonly object gate = new object();
    readonly StringBuilder log = new StringBuilder();
    readonly Stopwatch elapsed = new Stopwatch();
    readonly Label heading = new Label();
    readonly Label file = new Label();
    readonly Label status = new Label();
    readonly Label duration = new Label();
    readonly Label outcome = new Label();
    readonly ReferenceProgress progress = new ReferenceProgress();
    TableLayoutPanel body;
    Panel detailsHost;
    readonly TextBox details = new TextBox();
    readonly Button toggle = new SoftButton();
    readonly Button folder = new SoftButton();
    readonly Button close = new SoftButton();
    readonly Timer timer = new Timer();
    string latestStatus = "Starting conversion...";
    double fraction = -1;
    bool running = true;
    bool expanded;
    int logVersion;
    int displayedLogVersion = -1;
    public int Result { get; private set; }

    public ConversionWindow(string path, bool useCompression, bool replaceSource = false) {
        source = path;
        compressed = useCompression;
        replaceOriginal = replaceSource;
        Result = 1;
        Text = "Universal Substance Painter";
        Font = new Font("Segoe UI", 9F);
        BackColor = Color.FromArgb(18, 20, 26);
        AutoScaleMode = AutoScaleMode.Font;
        ClientSize = new Size(660, replaceOriginal ? 370 : 328);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        ControlBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        var canvas = new Panel { Dock = DockStyle.Fill, Padding = new Padding(20), BackColor = BackColor };
        Controls.Add(canvas);
        var card = new RoundedCard { Dock = DockStyle.Fill, Margin = Padding.Empty, Padding = new Padding(24) };
        body = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(27, 30, 39), ColumnCount = 1, RowCount = 7, Margin = Padding.Empty };
        body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        foreach (int height in new int[] { 32, 32, 90, 30, replaceOriginal ? 42 : 0, 0, 56 }) body.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
        card.Controls.Add(body);
        var header = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(27, 30, 39), ColumnCount = 2, Margin = Padding.Empty };
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 32));
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        heading.Text = "Converting project";
        heading.Font = new Font(Font.FontFamily, 12F);
        heading.ForeColor = Color.FromArgb(224, 228, 236);
        heading.Dock = DockStyle.Fill;
        heading.TextAlign = ContentAlignment.MiddleLeft;
        heading.AutoEllipsis = true;
        header.Controls.Add(new DocumentMark { Dock = DockStyle.Fill, Margin = Padding.Empty }, 0, 0);
        header.Controls.Add(heading, 1, 0);
        file.Text = Path.GetFileName(source);
        file.UseMnemonic = false;
        file.Dock = DockStyle.Fill;
        file.Font = new Font(Font.FontFamily, 10F);
        file.ForeColor = Color.FromArgb(155, 163, 178);
        file.TextAlign = ContentAlignment.MiddleLeft;
        file.AutoEllipsis = true;
        file.Margin = Padding.Empty;
        file.AccessibleDescription = source;
        var tooltip = new ToolTip();
        tooltip.SetToolTip(file, source);
        var stage = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(27, 30, 39), ColumnCount = 2, Margin = Padding.Empty };
        stage.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        stage.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 80));
        status.Text = latestStatus;
        status.UseMnemonic = false;
        status.Dock = DockStyle.Fill;
        status.TextAlign = ContentAlignment.MiddleLeft;
        status.ForeColor = Color.FromArgb(199, 206, 219);
        status.AutoEllipsis = true;
        status.Margin = Padding.Empty;
        duration.Dock = DockStyle.Fill;
        duration.Font = new Font("Consolas", 9F);
        duration.TextAlign = ContentAlignment.MiddleRight;
        duration.ForeColor = Color.FromArgb(155, 163, 178);
        duration.Margin = Padding.Empty;
        stage.Controls.Add(status, 0, 0);
        stage.Controls.Add(duration, 1, 0);
        progress.Dock = DockStyle.Fill;
        progress.Margin = Padding.Empty;
        progress.Style = ProgressBarStyle.Marquee;
        progress.AccessibleName = "Conversion progress";
        outcome.Text = replaceOriginal ? "After verification, the original moves to the Recycle Bin." : String.Empty;
        outcome.UseMnemonic = false;
        outcome.Dock = DockStyle.Fill;
        outcome.ForeColor = Color.FromArgb(155, 163, 178);
        outcome.TextAlign = ContentAlignment.MiddleLeft;
        outcome.Margin = Padding.Empty;
        outcome.AutoEllipsis = true;
        details.Dock = DockStyle.Fill;
        details.Multiline = true;
        details.ReadOnly = true;
        details.WordWrap = false;
        details.ScrollBars = ScrollBars.Both;
        details.BorderStyle = BorderStyle.None;
        details.BackColor = Color.FromArgb(21, 24, 32);
        details.ForeColor = Color.FromArgb(182, 193, 213);
        details.Font = new Font("Consolas", 9F);
        details.Visible = false;
        detailsHost = new Panel { Dock = DockStyle.Fill, BackColor = details.BackColor, Padding = new Padding(10), Margin = Padding.Empty, Visible = false };
        detailsHost.Controls.Add(details);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.Transparent, FlowDirection = FlowDirection.RightToLeft, WrapContents = false, Margin = Padding.Empty, Padding = new Padding(0, 12, 0, 0) };
        close.Text = "Close"; close.Enabled = false;
        folder.Text = "Open folder"; folder.Enabled = false;
        toggle.Text = "Details";
        foreach (Button button in new Button[] { close, folder, toggle }) {
            button.Size = new Size(112, 40);
            button.Margin = new Padding(8, 0, 0, 0);
            actions.Controls.Add(button);
        }
        ((SoftButton)close).Primary = true;
        close.Click += delegate { Close(); };
        folder.Click += delegate {
            try { Process.Start("explorer.exe", "/select,\"" + Path.ChangeExtension(source, compressed ? ".usppz" : ".uspp") + "\""); }
            catch (Exception error) { MessageBox.Show(this, error.Message, "Could not open folder"); }
        };
        toggle.Click += delegate { ExpandDetails(!expanded); };
        body.Controls.Add(header, 0, 0); body.Controls.Add(file, 0, 1);
        body.Controls.Add(progress, 0, 2); body.Controls.Add(stage, 0, 3);
        body.Controls.Add(outcome, 0, 4); body.Controls.Add(detailsHost, 0, 5);
        body.Controls.Add(actions, 0, 6);
        canvas.Controls.Add(card);
        FormClosed += delegate { tooltip.Dispose(); };
        timer.Interval = 150;
        timer.Tick += delegate { RefreshProgress(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (running && e.CloseReason == CloseReason.UserClosing) e.Cancel = true;
        };
        Shown += async delegate {
            elapsed.Start(); timer.Start();
            int result = await Task.Run(() => Convert());
            Finish(result);
        };
        FormClosed += delegate { timer.Dispose(); };
    }

    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        try {
            int dark = 1;
            if (DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int)) != 0)
                DwmSetWindowAttribute(Handle, 19, ref dark, sizeof(int));
        } catch (DllNotFoundException) { } catch (EntryPointNotFoundException) { }
    }

    void ExpandDetails(bool show) {
        expanded = show;
        details.Visible = show;
        detailsHost.Visible = show;
        body.RowStyles[5].Height = show ? 172 : 0;
        ResizeForContent();
        toggle.Text = show ? "Hide details" : "Details";
    }

    void ResizeForContent() {
        int outcomeHeight = String.IsNullOrEmpty(outcome.Text) ? 0 : 42;
        body.RowStyles[4].Height = outcomeHeight;
        ClientSize = new Size(ClientSize.Width, 328 + outcomeHeight + (expanded ? 172 : 0));
    }

    void ReadLine(string line) {
        if (line == null) return;
        lock (gate) {
            string[] fields = line.Split(new char[] { '\t' }, 3);
            double value;
            if (fields.Length == 3 && fields[0] == "__USPP_PROGRESS__" &&
                Double.TryParse(fields[1], NumberStyles.Float, CultureInfo.InvariantCulture, out value) &&
                !Double.IsNaN(value) && !Double.IsInfinity(value)) {
                fraction = value;
                latestStatus = fields[2];
            } else {
                log.AppendLine(line);
                if (log.Length > 128 * 1024) log.Remove(0, log.Length - 128 * 1024);
                logVersion++;
            }
        }
    }

    void RefreshProgress() {
        lock (gate) {
            if (running) {
                status.Text = latestStatus;
                progress.Style = fraction < 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
                if (fraction >= 0) progress.Value = Math.Max(0, Math.Min(99, (int)(fraction * 100)));
            }
            if (displayedLogVersion != logVersion) {
                details.Text = log.Length == 0 ? "Waiting for converter output..." : log.ToString();
                details.SelectionStart = details.TextLength; details.ScrollToCaret();
                displayedLogVersion = logVersion;
            }
        }
        progress.Advance();
        duration.Text = elapsed.Elapsed.ToString(@"hh\:mm\:ss");
    }

    int Convert() {
        try {
            if (!File.Exists(source) || !String.Equals(Path.GetExtension(source), ".spp", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Select an existing .spp project.");
            string tool = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "uspp_tool.exe");
            var start = new ProcessStartInfo(tool, "batch-pack \"" + source + "\"" + (compressed ? " --compress" : "")
                + (replaceOriginal ? " --overwrite --delete-source" : ""));
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.StandardOutputEncoding = Encoding.UTF8;
            start.StandardErrorEncoding = Encoding.UTF8;
            start.EnvironmentVariables["USPP_PROGRESS"] = "1";
            start.WorkingDirectory = Path.GetDirectoryName(source);
            using (Process process = new Process { StartInfo = start }) {
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { ReadLine(e.Data); };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { ReadLine(e.Data); };
                process.Start();
                process.BeginOutputReadLine(); process.BeginErrorReadLine();
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch (Exception error) { ReadLine(error.Message); return 1; }
    }

    void Finish(int result) {
        Result = result;
        running = false;
        elapsed.Stop(); timer.Stop(); RefreshProgress();
        progress.Style = ProgressBarStyle.Continuous;
        close.Enabled = true; folder.Enabled = result == 0; ControlBox = true;
        if (result == 0) {
            progress.Value = 100;
            heading.Text = "Universal project created";
            status.Text = replaceOriginal ? "Complete. Original moved to Recycle Bin." : "Conversion complete";
            outcome.Text = Path.ChangeExtension(source, compressed ? ".usppz" : ".uspp");
        } else {
            heading.Text = "Could not convert project";
            status.Text = "Conversion failed";
            status.ForeColor = Color.FromArgb(244, 113, 129);
            outcome.Text = String.Empty;
            progress.Failed = true;
            ExpandDetails(true);
        }
        ResizeForContent();
        AcceptButton = close;
    }
}

class ContextMenu {
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        return Run(args);
    }

    internal static int Run(string[] args) {
        bool compressed = false, replaceOriginal = false, valid = args.Length >= 1;
        for (int i = 0; i < args.Length - 1; i++) {
            if (args[i] == "--compress" && !compressed) compressed = true;
            else if (args[i] == "--replace-original" && !replaceOriginal) replaceOriginal = true;
            else valid = false;
        }
        if (!valid) {
            MessageBox.Show("Select one .spp project to convert.", "Universal Substance Painter", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        try {
            using (var window = new ConversionWindow(Path.GetFullPath(args[args.Length - 1]), compressed, replaceOriginal)) {
                Application.Run(window);
                return window.Result;
            }
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Universal Substance Painter", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

static class UiShape {
    public static GraphicsPath Round(RectangleF rect, float radius) {
        var path = new GraphicsPath();
        float d = Math.Min(radius * 2, Math.Min(rect.Width, rect.Height));
        path.AddArc(rect.X, rect.Y, d, d, 180, 90);
        path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
        path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
        path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}

class RoundedCard : Panel {
    public RoundedCard() {
        SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        BackColor = Color.Transparent;
    }
    protected override void OnPaint(PaintEventArgs e) {
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using (var shadow = UiShape.Round(new RectangleF(1, 3, Width - 2, Height - 4), 23))
        using (var brush = new SolidBrush(Color.FromArgb(60, 0, 0, 0))) e.Graphics.FillPath(brush, shadow);
        using (var shape = UiShape.Round(new RectangleF(0, 0, Width - 1, Height - 3), 22))
        using (var brush = new SolidBrush(Color.FromArgb(27, 30, 39))) e.Graphics.FillPath(brush, shape);
        base.OnPaint(e);
    }
}

class DocumentMark : Control {
    public DocumentMark() { SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.OptimizedDoubleBuffer, true); BackColor = Color.FromArgb(27, 30, 39); }
    protected override void OnPaint(PaintEventArgs e) {
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using (var pen = new Pen(Color.FromArgb(170, 182, 202), 1.6F)) {
            pen.StartCap = pen.EndCap = LineCap.Round;
            e.Graphics.DrawLines(pen, new PointF[] { new PointF(6, 23), new PointF(6, 8), new PointF(16, 8), new PointF(22, 14), new PointF(22, 23), new PointF(6, 23) });
            e.Graphics.DrawLines(pen, new PointF[] { new PointF(16, 8), new PointF(16, 14), new PointF(22, 14) });
            e.Graphics.DrawLine(pen, 10, 18, 18, 18);
        }
    }
}

class SoftButton : Button {
    bool primary;
    public bool Primary {
        get { return primary; }
        set { primary = value; ApplyColors(); }
    }
    public SoftButton() {
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        UseVisualStyleBackColor = false;
        Cursor = Cursors.Hand;
        ApplyColors();
    }
    protected override void OnEnabledChanged(EventArgs e) {
        base.OnEnabledChanged(e);
        ApplyColors();
    }
    void ApplyColors() {
        BackColor = primary ? (Enabled ? Color.FromArgb(52, 120, 246) : Color.FromArgb(43, 65, 103)) : Color.FromArgb(38, 42, 53);
        ForeColor = Enabled ? (primary ? Color.White : Color.FromArgb(205, 212, 226)) : Color.FromArgb(115, 127, 148);
        FlatAppearance.MouseOverBackColor = primary ? Color.FromArgb(64, 132, 255) : Color.FromArgb(49, 54, 67);
        FlatAppearance.MouseDownBackColor = primary ? Color.FromArgb(37, 99, 235) : Color.FromArgb(58, 64, 79);
    }
}

class ReferenceProgress : Control {
    int value;
    bool failed;
    float phase = .15F;
    ProgressBarStyle style;
    void Describe() { AccessibleDescription = failed ? "Conversion stopped" : style == ProgressBarStyle.Marquee ? "Working. Progress is not measurable for this stage." : value + " percent"; }
    public int Value { get { return value; } set { this.value = Math.Max(0, Math.Min(100, value)); Describe(); Invalidate(); } }
    public bool Failed { get { return failed; } set { failed = value; Describe(); Invalidate(); } }
    public ProgressBarStyle Style { get { return style; } set { style = value; Describe(); Invalidate(); } }
    public ReferenceProgress() {
        SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw | ControlStyles.UserPaint, true);
        BackColor = Color.FromArgb(27, 30, 39);
        AccessibleRole = AccessibleRole.ProgressBar;
    }
    public void Advance() {
        if (style == ProgressBarStyle.Marquee && !failed && SystemInformation.UIEffectsEnabled) {
            phase = (phase + .025F) % 1.3F; Invalidate();
        }
    }
    protected override void OnPaint(PaintEventArgs e) {
        if (Width < 20 || Height < 20) return;
        Graphics g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        bool indefinite = style == ProgressBarStyle.Marquee || failed;
        string number = indefinite ? "\u2014" : value.ToString(CultureInfo.InvariantCulture) + "%";
        using (var numberFont = new Font("Segoe UI", 31F, FontStyle.Regular))
            TextRenderer.DrawText(g, number, numberFont, new Rectangle(-3, 0, Width, 58), Color.FromArgb(242, 244, 249), TextFormatFlags.Left | TextFormatFlags.NoPadding | TextFormatFlags.VerticalCenter);
        var track = new RectangleF(0, 66, Width - 1, 10);
        using (var tick = new Pen(Color.FromArgb(67, 73, 88), 2F)) {
            tick.StartCap = tick.EndCap = LineCap.Round;
            for (float x = 2; x < track.Width; x += 8) g.DrawLine(tick, x, track.Y + 2, x, track.Bottom - 2);
        }
        if (failed) return;
        float filled = indefinite ? track.Width * .22F : track.Width * value / 100F;
        float left = indefinite ? (track.Width + filled) * phase - filled : 0;
        var fill = new RectangleF(left, track.Y, Math.Max(1, filled), track.Height);
        if (filled < 1) return;
        var previous = g.Save(); g.SetClip(track);
        using (var shape = UiShape.Round(fill, 4))
        using (var brush = new LinearGradientBrush(fill, Color.FromArgb(133, 218, 255), Color.FromArgb(29, 78, 216), 0F)) {
            brush.InterpolationColors = new ColorBlend { Colors = new Color[] { Color.FromArgb(133, 218, 255), Color.FromArgb(59, 145, 246), Color.FromArgb(29, 78, 216) }, Positions = new float[] { 0, .5F, 1 } };
            g.FillPath(brush, shape);
        }
        g.Restore(previous);
    }
}
