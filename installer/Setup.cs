using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;
using System.Runtime.InteropServices;

class Setup {
    const string Key = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\UniversalSPP";
    const string ShellKey = @"Software\Classes\SystemFileAssociations\.spp\shell\";
    [DllImport("shell32.dll")] static extern void SHChangeNotify(uint e, uint f, IntPtr a, IntPtr b);
    static void RegisterConversion(string target, string verb, string label, bool compressed, bool replaceOriginal = false) {
        string launcher = Path.Combine(target, "UniversalSPP-Convert.exe");
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(ShellKey + verb)) {
            key.SetValue("", label);
            key.SetValue("Icon", "\"" + Path.Combine(target, "uspp_tool.exe") + "\",0");
            key.SetValue("MultiSelectModel", "Single");
            using (RegistryKey command = key.CreateSubKey("command"))
                command.SetValue("", "\"" + launcher + "\" " + (compressed ? "--compress " : "") + (replaceOriginal ? "--replace-original " : "") + "\"%1\"");
        }
    }
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr SendMessageTimeout(IntPtr h, uint m, UIntPtr w, string l, uint f, uint t, out UIntPtr r);
    [STAThread] static int Main(string[] args) {
        bool silent = Array.IndexOf(args, "/silent") >= 0;
        string stage = Path.Combine(Path.GetTempPath(), "UniversalSPP-" + Guid.NewGuid());
        try {
            Application.EnableVisualStyles();
            string target = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\UniversalSPP");
            if (!silent && MessageBox.Show("Install Universal SPP CLI 1.0.0 (modified build)?\n\nLocation: " + target + "\n\nAdds uspp_tool to your user PATH. No administrator access is required.", "Universal SPP Setup", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK) return 0;
            Directory.CreateDirectory(stage);
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
                foreach (var entry in zip.Entries) {
                    string file = Path.GetFullPath(Path.Combine(stage, entry.FullName));
                    if (!file.StartsWith(stage + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new Exception("Invalid archive path.");
                    if (String.IsNullOrEmpty(entry.Name)) { Directory.CreateDirectory(file); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(file));
                    entry.ExtractToFile(file);
                }
            }
            string source = Path.Combine(stage, "uspp_tool");
            if (!File.Exists(Path.Combine(source, "uspp_tool.exe"))) throw new Exception("Converter missing from installer.");
            string installedExe = Path.Combine(target, "uspp_tool.exe");
            if (File.Exists(installedExe)) {
                try {
                    using (var check = new FileStream(installedExe, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }
                } catch (IOException) {
                    throw new IOException("Universal SPP is running. Finish or cancel the active batch, then run this installer again.");
                }
            }
            Directory.CreateDirectory(target);
            var files = Directory.GetFiles(source, "*", SearchOption.AllDirectories);
            var manifest = new System.Collections.Generic.List<string>();
            foreach (string file in files) {
                string relative = file.Substring(source.Length + 1);
                string dest = Path.Combine(target, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                File.Copy(file, dest, true);
                manifest.Add(relative);
            }
            using (RegistryKey env = Registry.CurrentUser.CreateSubKey("Environment")) {
                string path = (string)env.GetValue("Path", "", RegistryValueOptions.DoNotExpandEnvironmentNames);
                bool found = false;
                foreach (string part in path.Split(';')) if (part.Trim().TrimEnd('\\').Equals(target, StringComparison.OrdinalIgnoreCase)) found = true;
                if (!found) {
                    env.SetValue("Path", path.TrimEnd(';') + ";" + target, RegistryValueKind.ExpandString);
                    File.WriteAllText(Path.Combine(target, ".path-added"), "");
                }
            }
            File.WriteAllLines(Path.Combine(target, "installed-files.txt"), manifest);
            using (Stream script = Assembly.GetExecutingAssembly().GetManifestResourceStream("uninstall.ps1"))
            using (var output = File.Create(Path.Combine(target, "uninstall.ps1"))) script.CopyTo(output);
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(Key)) {
                key.SetValue("DisplayName", "Universal SPP CLI (modified build)");
                key.SetValue("DisplayVersion", "1.0.0");
                key.SetValue("InstallLocation", target);
                key.SetValue("UninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(target, "uninstall.ps1") + "\"");
                key.SetValue("NoModify", 1); key.SetValue("NoRepair", 1);
            }
            RegisterConversion(target, "UniversalSPP", "Convert to Universal Substance Painter", false);
            RegisterConversion(target, "UniversalSPPCompressed", "Convert to Universal Substance Painter Compressed", true);
            RegisterConversion(target, "UniversalSPPReplace", "Replace original with Universal Substance Painter", false, true);
            RegisterConversion(target, "UniversalSPPCompressedReplace", "Replace original with Universal Substance Painter Compressed", true, true);
            SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
            UIntPtr result;
            SendMessageTimeout(new IntPtr(0xffff), 0x1a, UIntPtr.Zero, "Environment", 2, 5000, out result);
            if (!silent) MessageBox.Show("Installation complete.\n\nOpen a new terminal and run: uspp_tool --help\n\nYou can uninstall from Windows Settings > Apps.", "Universal SPP Setup");
            return 0;
        } catch (Exception ex) {
            if (silent) Console.Error.WriteLine(ex); else MessageBox.Show(ex.Message, "Installation failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        } finally { if (Directory.Exists(stage)) Directory.Delete(stage, true); }
    }
}
