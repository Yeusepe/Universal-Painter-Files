// Compile with installer/Setup.cs and run against disposable temporary folders.
using System;
using System.IO;
using System.Linq;

class InstallerTransaction {
    static void Require(bool condition, string message) { if (!condition) throw new Exception(message); }
    static void Write(string path, string content) {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, content);
    }
    static void Main() {
        string root = Path.Combine(Path.GetTempPath(), "uspp-installer-test-" + Guid.NewGuid().ToString("N"));
        string target = Path.Combine(root, "install"), source = Path.Combine(root, "payload");
        try {
            Write(Path.Combine(target, "uspp_tool.exe"), "old executable");
            Write(Path.Combine(target, "_internal", "old.dll"), "old library");
            Write(Path.Combine(target, "user.txt"), "user file");
            File.WriteAllLines(Path.Combine(target, "installed-files.txt"), new[] { "uspp_tool.exe", @"_internal\old.dll" });
            Write(Path.Combine(source, "uspp_tool.exe"), "new executable");
            Write(Path.Combine(source, "_internal", "new.dll"), "new library");
            string manifestBefore = File.ReadAllText(Path.Combine(target, "installed-files.txt"));

            using (var locked = new FileStream(Path.Combine(source, "_internal", "new.dll"), FileMode.Open, FileAccess.Read, FileShare.None)) {
                try { Setup.InstallPayload(source, target, new byte[] { 1 }); throw new Exception("Copy unexpectedly succeeded"); }
                catch (IOException) { }
            }
            Require(File.ReadAllText(Path.Combine(target, "uspp_tool.exe")) == "old executable", "Copy failure changed the executable");
            Require(File.ReadAllText(Path.Combine(target, "installed-files.txt")) == manifestBefore, "Copy failure changed the manifest");
            Require(File.Exists(Path.Combine(target, "_internal", "old.dll")), "Copy failure removed the old library");

            try { Setup.InstallPayload(source, target, new byte[] { 1 }, () => { throw new IOException("Publish failed"); }); throw new Exception("Publish unexpectedly succeeded"); }
            catch (IOException) { }
            Require(File.ReadAllText(Path.Combine(target, "uspp_tool.exe")) == "old executable", "Publish failure changed the executable");
            Require(File.ReadAllText(Path.Combine(target, "installed-files.txt")) == manifestBefore, "Publish failure changed the manifest");
            Require(File.ReadAllText(Path.Combine(target, "user.txt")) == "user file", "Publish failure lost the user file");

            Setup.InstallPayload(source, target, new byte[] { 1 });
            Require(File.ReadAllText(Path.Combine(target, "uspp_tool.exe")) == "new executable", "Upgrade did not install the executable");
            Require(File.Exists(Path.Combine(target, "_internal", "new.dll")), "Upgrade did not install the library");
            Require(!File.Exists(Path.Combine(target, "_internal", "old.dll")), "Upgrade retained the obsolete library");
            Require(File.ReadAllText(Path.Combine(target, "user.txt")) == "user file", "Upgrade lost the user file");
            Require(File.ReadAllLines(Path.Combine(target, "installed-files.txt")).Contains(@"_internal\new.dll"), "Upgrade did not update the manifest");
            Console.WriteLine("Installer copy failure, publication rollback, and successful upgrade passed.");
        } finally {
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
            if (!String.Equals(Path.GetDirectoryName(Path.GetFullPath(root)), temp, StringComparison.OrdinalIgnoreCase))
                throw new IOException("Invalid test directory");
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
}
