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
    static void CopyExisting(string source, string destination) {
        if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("The existing installation contains a link: " + source);
        Directory.CreateDirectory(destination);
        foreach (string file in Directory.GetFiles(source)) {
            if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("The existing installation contains a link: " + file);
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)), false);
        }
        foreach (string directory in Directory.GetDirectories(source))
            CopyExisting(directory, Path.Combine(destination, Path.GetFileName(directory)));
    }
    static string ManagedPath(string root, string relative) {
        string path = Path.GetFullPath(Path.Combine(root, relative));
        if (!path.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Invalid installed file path: " + relative);
        return path;
    }
    // Build a complete replacement beside the install. No installed file is
    // changed until every copy and the replacement manifest have succeeded.
    internal static void InstallPayload(string source, string target, byte[] uninstall, Action beforePublish = null) {
        target = Path.GetFullPath(target).TrimEnd(Path.DirectorySeparatorChar);
        string parent = Path.GetDirectoryName(target);
        Directory.CreateDirectory(parent);
        string candidate = Path.Combine(parent, ".UniversalSPP-new-" + Guid.NewGuid().ToString("N"));
        string backup = Path.Combine(parent, ".UniversalSPP-backup-" + Guid.NewGuid().ToString("N"));
        if (!String.Equals(Path.GetDirectoryName(Path.GetFullPath(candidate)), parent, StringComparison.OrdinalIgnoreCase)
                || !String.Equals(Path.GetDirectoryName(Path.GetFullPath(backup)), parent, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Invalid installation staging path");
        bool oldMoved = false, published = false;
        try {
            if (Directory.Exists(target)) CopyExisting(target, candidate);
            else Directory.CreateDirectory(candidate);
            string oldManifest = Path.Combine(candidate, "installed-files.txt");
            if (File.Exists(oldManifest)) {
                foreach (string relative in File.ReadAllLines(oldManifest)) {
                    if (String.IsNullOrWhiteSpace(relative)) continue;
                    string obsolete = ManagedPath(candidate, relative);
                    if (File.Exists(obsolete)) File.Delete(obsolete);
                }
            }
            var manifest = new System.Collections.Generic.List<string>();
            foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories)) {
                string relative = file.Substring(source.Length + 1);
                string dest = ManagedPath(candidate, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                File.Copy(file, dest, true);
                manifest.Add(relative);
            }
            File.WriteAllLines(Path.Combine(candidate, "installed-files.txt"), manifest);
            File.WriteAllBytes(Path.Combine(candidate, "uninstall.ps1"), uninstall);
            if (Directory.Exists(target)) { Directory.Move(target, backup); oldMoved = true; }
            try {
                if (beforePublish != null) beforePublish();
                Directory.Move(candidate, target);
                published = true;
            } catch (Exception error) {
                if (oldMoved) {
                    try { Directory.Move(backup, target); oldMoved = false; }
                    catch (Exception rollback) {
                        throw new IOException("Installation failed and the previous installation remains at " + backup + ": " + rollback.Message, error);
                    }
                }
                throw;
            }
        } finally {
            try { if (Directory.Exists(candidate)) Directory.Delete(candidate, true); }
            catch (IOException) { /* Leave staging files for recovery if locked. */ }
            catch (UnauthorizedAccessException) { /* Leave staging files for recovery. */ }
            if (published && oldMoved && Directory.Exists(backup)) {
                try { Directory.Delete(backup, true); }
                catch (IOException) { /* Keep the previous copy if another process still has it open. */ }
                catch (UnauthorizedAccessException) { /* Keep the previous copy for recovery. */ }
            }
        }
    }
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
            byte[] uninstall;
            using (Stream script = Assembly.GetExecutingAssembly().GetManifestResourceStream("uninstall.ps1"))
            using (var buffer = new MemoryStream()) { script.CopyTo(buffer); uninstall = buffer.ToArray(); }
            InstallPayload(source, target, uninstall);
            using (RegistryKey env = Registry.CurrentUser.CreateSubKey("Environment")) {
                string path = (string)env.GetValue("Path", "", RegistryValueOptions.DoNotExpandEnvironmentNames);
                bool found = false;
                foreach (string part in path.Split(';')) if (part.Trim().TrimEnd('\\').Equals(target, StringComparison.OrdinalIgnoreCase)) found = true;
                if (!found) {
                    env.SetValue("Path", path.TrimEnd(';') + ";" + target, RegistryValueKind.ExpandString);
                    File.WriteAllText(Path.Combine(target, ".path-added"), "");
                }
            }
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
