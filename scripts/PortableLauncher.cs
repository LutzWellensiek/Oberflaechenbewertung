using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Compiled with the Windows .NET Framework compiler. No Python installation,
// installer, registry change, administrator access or network download is used.
class PortableLauncher
{
    static string Executable = Assembly.GetExecutingAssembly().Location;
    static JavaScriptSerializer Json = new JavaScriptSerializer();
    static string[] Arguments;
    static bool Has(string flag) { return Array.IndexOf(Arguments, flag) >= 0; }
    static string Value(string flag) {
        int at = Array.IndexOf(Arguments, flag);
        return at >= 0 && at + 1 < Arguments.Length ? Arguments[at + 1] : null;
    }
    [STAThread]
    static int Main(string[] args) {
        Arguments = args;
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        Application.EnableVisualStyles();
        try {
            if (Has("--headless") || Has("--extract-only") || Has("--stop")) {
                string root = Prepare(delegate(string message, int percent) {});
                if (Has("--extract-only")) return 0;
                Session session = Start(root, Has("--stop"));
                if (Has("--stop") && session != null) Stop(session);
                return 0;
            }
            Application.Run(new ControlWindow());
            return 0;
        } catch (Exception error) {
            try { File.AppendAllText(Path.Combine(Path.GetDirectoryName(Executable), "Startfehler.txt"),
                DateTime.Now + "\r\n" + error + "\r\n", Encoding.UTF8); } catch {}
            if (!Has("--headless") && !Has("--extract-only"))
                MessageBox.Show(error.Message, "Oberflächenbewertung – Start fehlgeschlagen", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
    static string Prepare(Action<string, int> progress) {
        string adjacent = Path.GetDirectoryName(Executable);
        long offset;
        using (FileStream file = File.OpenRead(Executable)) {
            if (file.Length < 16) return adjacent;
            file.Seek(-16, SeekOrigin.End);
            byte[] magic = new byte[8]; file.Read(magic, 0, 8);
            if (Encoding.ASCII.GetString(magic) != "SFLAB01!") return adjacent;
            offset = new BinaryReader(file).ReadInt64();
            if (offset < 0 || offset >= file.Length - 16) throw new InvalidDataException("Das Programmpaket ist beschädigt.");
            string root = Path.GetFullPath(Value("--target") ?? Path.Combine(adjacent, "Oberflaechenbewertung"));
            Directory.CreateDirectory(root);
            using (FileStream buildLock = new FileStream(Path.Combine(root, ".unpack-lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
            using (SliceStream payload = new SliceStream(file, offset, file.Length - offset - 16))
            using (ZipArchive archive = new ZipArchive(payload, ZipArchiveMode.Read)) {
                string id;
                using (StreamReader info = new StreamReader(archive.GetEntry("portable.json").Open(), Encoding.UTF8))
                    id = (string)Json.Deserialize<Dictionary<string, object>>(info.ReadToEnd())["build_id"];
                string complete = Path.Combine(root, ".package-complete");
                if (File.Exists(complete) && File.ReadAllText(complete) == id) return root;
                // A newer package must not replace binaries of an active instance.
                for (int port = 8765; port <= 8795; port++) {
                    Dictionary<string, object> state = State(port);
                    if (SameRoot(state, root)) throw new IOException("Bitte die laufende Oberflächenbewertung zuerst über ihr Startfenster beenden und die EXE erneut öffnen.");
                }
                bool keepData = File.Exists(Path.Combine(root, "data", "surface.sqlite3"));
                long total = 0, done = 0;
                foreach (ZipArchiveEntry item in archive.Entries) total += item.Length;
                DriveInfo drive = new DriveInfo(Path.GetPathRoot(root));
                if (drive.AvailableFreeSpace < total + 256L * 1024 * 1024)
                    throw new IOException("Zum ersten Auspacken werden etwa " + Math.Ceiling(total / 1073741824.0 + 0.25) + " GB freier Speicher benötigt.");
                byte[] buffer = new byte[1024 * 1024];
                foreach (ZipArchiveEntry item in archive.Entries) {
                    string name = item.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string destination = Path.GetFullPath(Path.Combine(root, name));
                    if (!destination.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Ungültiger Pfad im Programmpaket.");
                    if (keepData && item.FullName.StartsWith("data/", StringComparison.OrdinalIgnoreCase)) { done += item.Length; continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    string temporary = destination + ".unpacking";
                    using (Stream source = item.Open())
                    using (FileStream target = File.Create(temporary)) {
                        int n;
                        while ((n = source.Read(buffer, 0, buffer.Length)) != 0) {
                            target.Write(buffer, 0, n); done += n;
                            progress("Programmpaket wird lokal ausgepackt. Deine vorhandenen Bewertungen bleiben erhalten.", (int)(100 * done / Math.Max(total, 1)));
                        }
                    }
                    if (File.Exists(destination)) File.Delete(destination);
                    File.Move(temporary, destination);
                }
                File.WriteAllText(complete, id);
            }
            return root;
        }
    }
    static bool SameRoot(Dictionary<string, object> state, string root) {
        return state != null && state.ContainsKey("instance_root") &&
            String.Equals(Path.GetFullPath((string)state["instance_root"]), root, StringComparison.OrdinalIgnoreCase);
    }
    static Dictionary<string, object> State(int port) {
        try {
            bool bound = false;
            try {
                foreach (IPEndPoint endpoint in System.Net.NetworkInformation.IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
                    if (endpoint.Port == port) { bound = true; break; }
                if (!bound) return null;
            } catch {} // A restricted listener inventory need not prevent localhost access.
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/state");
            request.Proxy = null; request.Timeout = 1200;
            using (WebResponse response = request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                return Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
        } catch { return null; }
    }
    class Session { public int Port; public string Token; public Process Process; }
    static Session FromState(int port, Dictionary<string, object> state) {
        Process process = null;
        try { process = Process.GetProcessById(Convert.ToInt32(state["process_id"])); } catch {}
        return new Session { Port = port, Token = (string)state["token"], Process = process };
    }
    static Session Start(string root, bool stopOnly) {
        int requested = Value("--port") == null ? 0 : Int32.Parse(Value("--port"));
        int free = 0;
        for (int port = requested == 0 ? 8765 : requested; port <= (requested == 0 ? 8795 : requested); port++) {
            Dictionary<string, object> state = State(port);
            if (SameRoot(state, root)) return FromState(port, state);
            if (free == 0) {
                try { TcpListener listener = new TcpListener(IPAddress.Loopback, port); listener.Start(); listener.Stop(); free = port; }
                catch (SocketException) {}
            }
        }
        if (stopOnly) return null;
        if (free == 0) throw new IOException("Kein freier lokaler Port verfügbar.");
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        if (!File.Exists(python)) throw new FileNotFoundException("Der mitgelieferte runtime-Ordner fehlt. Bitte den vollständigen Programmordner kopieren oder die Paket-EXE erneut auspacken.");
        string probe = Path.Combine(root, ".write-test-" + Guid.NewGuid());
        File.WriteAllText(probe, ""); File.Delete(probe);
        ProcessStartInfo start = new ProcessStartInfo(python,
            "-I -S -X utf8 -u \"" + Path.Combine(root, "portable_bootstrap.py") + "\" --no-browser --port " + free);
        start.WorkingDirectory = root; start.UseShellExecute = false; start.CreateNoWindow = true;
        start.EnvironmentVariables.Remove("PYTHONHOME"); start.EnvironmentVariables.Remove("PYTHONPATH");
        Process child = Process.Start(start);
        for (int attempt = 0; attempt < 120; attempt++) {
            Dictionary<string, object> state = State(free);
            if (SameRoot(state, root)) return FromState(free, state);
            if (child.HasExited) throw new IOException("Die Anwendung konnte nicht starten. Details stehen im Programmordner unter logs\\server-error.log.");
            Thread.Sleep(500);
        }
        throw new TimeoutException("Der lokale Server startet nicht rechtzeitig. Details stehen unter logs\\server-error.log.");
    }
    static void Stop(Session session) {
        HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + session.Port + "/api/shutdown");
        request.Proxy = null; request.Timeout = 5000; request.Method = "POST";
        request.ContentType = "application/json"; request.Headers["X-Surface-Token"] = session.Token;
        byte[] bytes = Encoding.UTF8.GetBytes("{}"); request.ContentLength = bytes.Length;
        using (Stream stream = request.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
        using (WebResponse response = request.GetResponse()) {}
    }
    class ControlWindow : Form {
        Label status = new Label(); ProgressBar bar = new ProgressBar();
        Button browser = new Button(), stop = new Button();
        Session session; bool stopping, finished;
        System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        public ControlWindow() {
            Text = "Oberflächenbewertung"; ClientSize = new Size(535, 215);
            StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false;
            Font = new Font("Segoe UI", 10);
            status.SetBounds(22, 22, 490, 75); status.Text = "Anwendung wird vorbereitet …";
            bar.SetBounds(22, 105, 490, 20);
            browser.SetBounds(22, 148, 230, 37); browser.Text = "Im Browser öffnen"; browser.Enabled = false;
            stop.SetBounds(272, 148, 240, 37); stop.Text = "Anwendung beenden"; stop.Enabled = false;
            Controls.AddRange(new Control[] { status, bar, browser, stop });
            browser.Click += delegate { OpenBrowser(); }; stop.Click += delegate { Shutdown(); };
            Shown += delegate { ThreadPool.QueueUserWorkItem(delegate {
                try {
                    string root = Prepare(delegate(string message, int percent) {
                        // Limit UI updates to once per percent even for large model files.
                        if (percent != bar.Value) Invoke((Action)delegate { status.Text = message; bar.Value = percent; });
                    });
                    Invoke((Action)delegate { status.Text = "Lokale Anwendung wird gestartet …"; });
                    session = Start(root, false);
                    Invoke((Action)delegate { status.Text = "Die Anwendung läuft vollständig lokal.\r\nDieses Fenster beendet auch den lokalen Server.\r\nDeine Bewertungen werden im Programmordner gespeichert.";
                        bar.Value = 100; browser.Enabled = stop.Enabled = true; OpenBrowser(); });
                } catch (Exception error) { Invoke((Action)delegate { status.Text = error.Message; finished = true; stop.Text = "Schließen"; stop.Enabled = true; }); }
            }); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (!finished) { e.Cancel = true; Shutdown(); } };
            timer.Interval = 500; timer.Tick += delegate {
                if (session.Process != null ? session.Process.HasExited : State(session.Port) == null) { finished = true; timer.Stop(); Close(); }
            };
        }
        void OpenBrowser() { Process.Start(new ProcessStartInfo("http://127.0.0.1:" + session.Port) { UseShellExecute = true }); }
        void Shutdown() {
            if (finished) { Close(); return; }
            if (session == null || stopping) return;
            stopping = true; browser.Enabled = stop.Enabled = false;
            status.Text = "Anwendung wird beendet. Laufende Berechnungen werden noch abgeschlossen …";
            ThreadPool.QueueUserWorkItem(delegate {
                try { Stop(session); } catch {}
                Invoke((Action)delegate { timer.Start(); });
            });
        }
    }
    class SliceStream : Stream {
        Stream inner; long origin, length, position;
        public SliceStream(Stream file, long offset, long count) { inner = file; origin = offset; length = count; }
        public override bool CanRead { get { return true; } } public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } } public override long Length { get { return length; } }
        public override long Position { get { return position; } set { Seek(value, SeekOrigin.Begin); } }
        public override int Read(byte[] buffer, int offset, int count) {
            inner.Position = origin + position;
            int read = inner.Read(buffer, offset, (int)Math.Min(count, length - position)); position += read; return read;
        }
        public override long Seek(long offset, SeekOrigin from) {
            long next = (from == SeekOrigin.Begin ? 0 : from == SeekOrigin.Current ? position : length) + offset;
            if (next < 0 || next > length) throw new IOException("Ungültiger Archivzugriff.");
            return position = next;
        }
        public override void Flush() {} public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }
}
