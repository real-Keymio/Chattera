// Chattera launcher for Windows (Chattera.exe).
//
// Double-click: if Chattera is already running for this data folder, open its window; otherwise start the server that
// sits next to this file, wait until it answers, then open the window (Edge app window when Edge is installed, else the
// default browser). Stays in the notification area so Chattera can be reopened, restarted or quit.
//
// It only uses its own folder and the data folder:
//   <program>\runtime\chattera-server.exe        packaged server (portable ZIP)
//   <program>\.venv\Scripts\pythonw.exe + src\chattera.py   source checkout after scripts\setup.ps1
//   data folder: `--home <folder>` argument, else %CHATTERA_HOME%, else <program>\data in portable mode
//                (portable.txt next to this file), else %LOCALAPPDATA%\Chattera.
//                The server writes run\server.json there with its actual port.
//
// Built with the C# compiler that ships with Windows (.NET Framework 4, C# 5 syntax): see Build-Launcher.ps1.
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

static class Program
{
    public static string Root, Home;

    [STAThread]
    static void Main()
    {
        Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string[] args = Environment.GetCommandLineArgs();
        int render = Array.IndexOf(args, "--render-splash");
        if (render > 0 && render + 1 < args.Length)
        {
            // documentation screenshots: draw the real start-up window into a PNG and exit
            using (Splash splash = new Splash())
            using (Bitmap image = new Bitmap(splash.ClientSize.Width, splash.ClientSize.Height))
            {
                splash.Status = "正在启动 Chattera…（第一次启动会稍慢）";
                splash.DrawToBitmap(image, new Rectangle(0, 0, image.Width, image.Height));
                image.Save(args[render + 1], System.Drawing.Imaging.ImageFormat.Png);
            }
            return;
        }
        Home = DataHome();
        string key = "ChatteraLauncher-" + Hash(Home.ToLowerInvariant());
        bool first;
        using (Mutex mutex = new Mutex(true, "Local\\" + key, out first))
        {
            if (!first)
            {
                try { EventWaitHandle.OpenExisting("Local\\" + key + "-open").Set(); }
                catch { string url = Service.Url(); if (url != null) Service.OpenWindow(url); }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new Launcher(key));
        }
    }

    static string DataHome()
    {
        // `Chattera.exe --home D:\somewhere` (e.g. in a shortcut) wins over everything else
        string[] args = Environment.GetCommandLineArgs();
        int at = Array.IndexOf(args, "--home");
        if (at > 0 && at + 1 < args.Length) return Path.GetFullPath(Environment.ExpandEnvironmentVariables(args[at + 1]));
        string env = Environment.GetEnvironmentVariable("CHATTERA_HOME");
        if (!string.IsNullOrEmpty(env)) return Path.GetFullPath(Environment.ExpandEnvironmentVariables(env));
        if (File.Exists(Path.Combine(Root, "portable.txt")) || File.Exists(Path.Combine(Root, "data", "chattera-home.json")))
            return Path.Combine(Root, "data");
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Chattera");
    }

    static string Hash(string text)
    {
        uint h = 2166136261;
        foreach (char c in text) { h ^= c; h *= 16777619; }
        return h.ToString("x8");
    }

    public static void Log(string text)
    {
        try
        {
            string folder = Path.Combine(Home, "logs");
            Directory.CreateDirectory(folder);
            string path = Path.Combine(folder, "launcher.log");
            if (File.Exists(path) && new FileInfo(path).Length > 512 * 1024) File.Delete(path);
            File.AppendAllText(path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + Environment.NewLine, Encoding.UTF8);
        }
        catch { }
    }
}

static class Service
{
    static string StateFile { get { return Path.Combine(Program.Home, "run", "server.json"); } }

    static string Field(string json, string name)
    {
        Match m = Regex.Match(json, "\"" + name + "\"\\s*:\\s*(\"(?<s>[^\"]*)\"|(?<n>[0-9]+))");
        if (!m.Success) return null;
        return m.Groups["s"].Success ? Regex.Unescape(m.Groups["s"].Value) : m.Groups["n"].Value;
    }

    /// <summary>The address of a Chattera server that is running for this data folder and answers, or null.</summary>
    public static string Url()
    {
        try
        {
            if (!File.Exists(StateFile)) return null;
            string json = File.ReadAllText(StateFile, Encoding.UTF8);
            string url = Field(json, "url");
            return url != null && Page(url) != null ? url : null;
        }
        catch { return null; }
    }

    /// <summary>The home page if it is Chattera (not some other program on that port), else null.</summary>
    static string Page(string url)
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.Timeout = 2500; request.Proxy = null; request.KeepAlive = false;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
            {
                string text = reader.ReadToEnd();
                return response.StatusCode == HttpStatusCode.OK && text.Contains("content=\"Chattera\"") ? text : null;
            }
        }
        catch { return null; }
    }

    static ProcessStartInfo ServerCommand(out string problem)
    {
        problem = null;
        string packaged = Path.Combine(Program.Root, "runtime", "chattera-server.exe");
        string home = " --home \"" + Program.Home + "\"";
        if (File.Exists(packaged))
            return Info(packaged, home.Trim(), Path.Combine(Program.Root, "runtime"));
        string pythonw = Path.Combine(Program.Root, ".venv", "Scripts", "pythonw.exe");
        string entry = Path.Combine(Program.Root, "src", "chattera.py");
        if (File.Exists(pythonw) && File.Exists(entry))
            return Info(pythonw, "\"" + entry + "\"" + home, Path.Combine(Program.Root, "src"));
        problem = "在程序文件夹里找不到 Chattera 服务（runtime\\chattera-server.exe）。请重新解压完整的 Chattera 压缩包。";
        return null;
    }

    static ProcessStartInfo Info(string file, string arguments, string folder)
    {
        ProcessStartInfo info = new ProcessStartInfo(file, arguments);
        info.UseShellExecute = false; info.CreateNoWindow = true; info.WorkingDirectory = folder;
        return info;
    }

    /// <summary>Starts the server and waits until it answers. Returns null on success, otherwise what went wrong.</summary>
    public static string Start()
    {
        string problem;
        ProcessStartInfo info = ServerCommand(out problem);
        if (info == null) return problem;
        try { if (File.Exists(StateFile)) File.Delete(StateFile); } catch { }
        Process process;
        try { process = Process.Start(info); }
        catch (Exception error) { Program.Log("server could not start: " + error.Message); return "无法启动 Chattera 服务：" + error.Message; }
        Program.Log("server started: " + info.FileName + " (pid " + process.Id + ")");
        // the first start can take longer: it creates the data folder and asks installed CLIs for their model lists
        for (int i = 0; i < 240; i++)
        {
            if (Url() != null) return null;
            if (process.HasExited) return "Chattera 服务启动后立即退出了（代码 " + process.ExitCode + "）。详情见日志文件夹中的 server.log。";
            Thread.Sleep(500);
        }
        return "Chattera 服务启动超过 2 分钟仍未就绪。详情见日志文件夹中的 server.log。";
    }

    /// <summary>Asks the server to quit (the same as 设置 → 帮助 → 退出), and waits until it is gone.</summary>
    public static string Stop()
    {
        string url = Url();
        if (url == null) return null;
        try
        {
            Match token = Regex.Match(Page(url) ?? "", "__SESSION_TOKEN__=\"([0-9a-f]+)\"");
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url + "api/chattera/quit");
            request.Method = "POST"; request.Proxy = null; request.Timeout = 5000; request.ContentType = "application/json";
            request.Headers.Add("X-Session-Token", token.Success ? token.Groups[1].Value : "");
            request.Headers.Add("Origin", url.TrimEnd('/'));
            byte[] body = Encoding.UTF8.GetBytes("{}");
            using (Stream stream = request.GetRequestStream()) stream.Write(body, 0, body.Length);
            using (request.GetResponse()) { }
        }
        catch (Exception error) { Program.Log("quit request failed: " + error.Message); }
        for (int i = 0; i < 40; i++) { if (Url() == null) return null; Thread.Sleep(250); }
        return "Chattera 服务没有响应退出请求。";
    }

    /// <summary>Chattera in its own window: Edge app mode when Edge is installed (a real browser, so uploads,
    /// clipboard and previews work as usual), otherwise the default browser.</summary>
    public static void OpenWindow(string url)
    {
        // `Chattera.exe --no-window` (automated checks): start the server and stay in the tray, but open no browser.
        if (Array.IndexOf(Environment.GetCommandLineArgs(), "--no-window") > 0) return;
        string[] candidates = {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Microsoft\\Edge\\Application\\msedge.exe"),
            Path.Combine(Environment.GetEnvironmentVariable("ProgramW6432") ?? "", "Microsoft\\Edge\\Application\\msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Microsoft\\Edge\\Application\\msedge.exe") };
        foreach (string edge in candidates)
        {
            if (!File.Exists(edge)) continue;
            try { Process.Start(edge, "--app=" + url + " --window-size=1440,920"); return; }
            catch (Exception error) { Program.Log("Edge could not be started: " + error.Message); }
        }
        try { Process.Start(url); } catch (Exception error) { Program.Log("No browser could be opened: " + error.Message); }
    }

    public static void OpenFolder(string sub)
    {
        string path = sub == null ? Program.Home : Path.Combine(Program.Home, sub);
        try { Directory.CreateDirectory(path); Process.Start("explorer.exe", "\"" + path + "\""); } catch { }
    }

    /// <summary>Desktop shortcut "Chattera.lnk" pointing at this Chattera.exe (only when the user asks for it).</summary>
    public static string MakeShortcut()
    {
        try
        {
            string link = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Chattera.lnk");
            Type type = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(type);
            object shortcut = type.InvokeMember("CreateShortcut", System.Reflection.BindingFlags.InvokeMethod, null, shell, new object[] { link });
            Type kind = shortcut.GetType();
            kind.InvokeMember("TargetPath", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { Application.ExecutablePath });
            kind.InvokeMember("WorkingDirectory", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { Program.Root });
            if (Array.IndexOf(Environment.GetCommandLineArgs(), "--home") > 0)   // keep a data folder chosen on the command line
                kind.InvokeMember("Arguments", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { "--home \"" + Program.Home + "\"" });
            kind.InvokeMember("IconLocation", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { Application.ExecutablePath + ",0" });
            kind.InvokeMember("Description", System.Reflection.BindingFlags.SetProperty, null, shortcut, new object[] { "Chattera — a group chat for you and your AIs" });
            kind.InvokeMember("Save", System.Reflection.BindingFlags.InvokeMethod, null, shortcut, null);
            return null;
        }
        catch (Exception error) { return error.Message; }
    }

    public static void OpenGuide()
    {
        string guide = Path.Combine(Program.Root, "01_先看这个.pdf");
        try { if (File.Exists(guide)) Process.Start(guide); } catch { }
    }
}

/// <summary>The small window shown while the server comes up: four voices on their orbits, and one line of status.</summary>
class Splash : Form
{
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly Color[] hues = { Color.FromArgb(255, 141, 94), Color.FromArgb(61, 220, 174), Color.FromArgb(110, 169, 255), Color.FromArgb(181, 146, 255) };
    readonly Stopwatch clock = Stopwatch.StartNew();
    public string Status = "正在检查 Chattera…";
    public string Problem;
    public Button Retry, Logs, Quit;

    public Splash()
    {
        FormBorderStyle = FormBorderStyle.None; StartPosition = FormStartPosition.CenterScreen; ShowInTaskbar = true;
        Text = "Chattera"; ClientSize = new Size(480, 300); BackColor = Color.FromArgb(9, 11, 17); DoubleBuffered = true;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        using (GraphicsPath path = Rounded(new Rectangle(0, 0, Width, Height), 22)) Region = new Region(path);
        Retry = Make("重试", 150); Logs = Make("打开日志", 250); Quit = Make("关闭", 350);
        timer.Interval = 33; timer.Tick += delegate { Invalidate(); }; timer.Start();
        MouseDown += delegate(object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left) { ReleaseCapture(); SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero); } };
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool ReleaseCapture();
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr handle, int message, IntPtr w, IntPtr l);

    Button Make(string text, int x)
    {
        Button button = new Button();
        button.Text = text; button.FlatStyle = FlatStyle.Flat; button.ForeColor = Color.FromArgb(232, 235, 244); button.BackColor = Color.FromArgb(22, 27, 42);
        button.FlatAppearance.BorderColor = Color.FromArgb(60, 70, 110); button.Font = new Font("Microsoft YaHei UI", 9f);
        button.SetBounds(x, 248, 90, 30); button.Visible = false; Controls.Add(button);
        return button;
    }

    static GraphicsPath Rounded(Rectangle r, int radius)
    {
        GraphicsPath path = new GraphicsPath(); int d = radius * 2;
        path.AddArc(r.X, r.Y, d, d, 180, 90); path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90); path.AddArc(r.X, r.Bottom - d, d, d, 90, 90); path.CloseFigure();
        return path;
    }

    public void ShowProblem(string text) { Problem = text; Retry.Visible = Logs.Visible = Quit.Visible = true; Invalidate(); }
    public void ClearProblem() { Problem = null; Retry.Visible = Logs.Visible = Quit.Visible = false; Invalidate(); }

    protected override void OnPaint(PaintEventArgs e)
    {
        Graphics g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        using (LinearGradientBrush back = new LinearGradientBrush(ClientRectangle, Color.FromArgb(18, 22, 44), Color.FromArgb(7, 8, 13), 65f)) g.FillRectangle(back, ClientRectangle);
        double t = clock.Elapsed.TotalSeconds; float cx = Width / 2f, cy = 112f;
        for (int i = 0; i < 4; i++)
        {
            float rx = 62 + i * 30, ry = rx * 0.36f;
            using (Pen far = new Pen(Color.FromArgb(34, 150, 168, 220), 1f)) g.DrawArc(far, cx - rx, cy - ry, rx * 2, ry * 2, 180, 180);
            using (Pen near = new Pen(Color.FromArgb(70, 150, 168, 220), 1f)) g.DrawArc(near, cx - rx, cy - ry, rx * 2, ry * 2, 0, 180);
        }
        for (int i = 0; i < 4; i++)
        {
            float rx = 62 + i * 30, ry = rx * 0.36f;
            double a = t * (0.9 - i * 0.14) * (i % 2 == 0 ? 1 : -1) + i * 1.7;
            float x = cx + (float)Math.Cos(a) * rx, y = cy + (float)Math.Sin(a) * ry, depth = (float)(0.75 + 0.25 * Math.Sin(a)), size = 9 * depth + 3;
            using (GraphicsPath halo = new GraphicsPath())
            {
                halo.AddEllipse(x - size * 2.4f, y - size * 2.4f, size * 4.8f, size * 4.8f);
                using (PathGradientBrush glow = new PathGradientBrush(halo))
                {
                    glow.CenterColor = Color.FromArgb(120, hues[i]); glow.SurroundColors = new Color[] { Color.FromArgb(0, hues[i]) };
                    g.FillPath(glow, halo);
                }
            }
            using (SolidBrush body = new SolidBrush(hues[i])) g.FillEllipse(body, x - size / 2, y - size / 2, size, size);
        }
        using (Font title = new Font("Segoe UI Light", 20f)) using (SolidBrush ink = new SolidBrush(Color.FromArgb(232, 235, 244)))
        {
            StringFormat centre = new StringFormat(); centre.Alignment = StringAlignment.Center;
            g.DrawString("C H A T T E R A", title, ink, new RectangleF(0, 172, Width, 40), centre);
            using (Font small = new Font("Microsoft YaHei UI", 9.5f))
            using (SolidBrush soft = new SolidBrush(Problem == null ? Color.FromArgb(162, 169, 189) : Color.FromArgb(255, 142, 134)))
                g.DrawString(Problem ?? Status, small, soft, new RectangleF(30, 214, Width - 60, Problem == null ? 40 : 34), centre);
        }
    }
}

class Launcher : ApplicationContext
{
    readonly Splash splash = new Splash();
    readonly NotifyIcon tray = new NotifyIcon();
    readonly EventWaitHandle open;
    readonly SynchronizationContext ui;
    volatile bool busy;

    public Launcher(string key)
    {
        open = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\" + key + "-open");
        splash.Show();
        ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        tray.Text = "Chattera";
        ContextMenuStrip menu = new ContextMenuStrip();
        menu.Items.Add("打开 Chattera", null, delegate { Work(false, true); });
        menu.Items.Add("重新启动", null, delegate { Work(true, true); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("使用指南", null, delegate { Service.OpenGuide(); });
        menu.Items.Add("打开数据文件夹", null, delegate { Service.OpenFolder(null); });
        menu.Items.Add("打开日志文件夹", null, delegate { Service.OpenFolder("logs"); });
        menu.Items.Add("在桌面创建快捷方式", null, delegate {
            string problem = Service.MakeShortcut();
            tray.ShowBalloonTip(3000, "Chattera", problem == null ? "已在桌面创建“Chattera”快捷方式。" : "没能创建快捷方式：" + problem, ToolTipIcon.Info); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出 Chattera", null, delegate { Shutdown(true); });
        tray.ContextMenuStrip = menu;
        tray.MouseClick += delegate(object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left) Work(false, true); };
        tray.Visible = true;
        splash.Retry.Click += delegate { Work(false, true); };
        splash.Logs.Click += delegate { Service.OpenFolder("logs"); };
        splash.Quit.Click += delegate { Shutdown(false); };
        Thread listener = new Thread(delegate() { while (true) { open.WaitOne(); ui.Post(delegate { Work(false, true); }, null); } });
        listener.IsBackground = true; listener.Start();
        Program.Log("launcher started; program " + Program.Root + "; data " + Program.Home);
        Work(false, true);
    }

    void Say(string text) { ui.Post(delegate { splash.Status = text; splash.Invalidate(); }, null); }

    void Work(bool restart, bool show)
    {
        if (busy) return;
        busy = true;
        splash.ClearProblem();
        Thread worker = new Thread(delegate()
        {
            string problem = null;
            try
            {
                string url = Service.Url();
                if (restart && url != null)
                {
                    ui.Post(delegate { splash.Opacity = 1; splash.Show(); }, null);
                    Say("正在停止 Chattera…");
                    problem = Service.Stop();
                    url = null;
                }
                if (problem == null && url == null)
                {
                    ui.Post(delegate { splash.Opacity = 1; splash.Show(); }, null);
                    Say("正在启动 Chattera…（第一次启动会稍慢）");
                    problem = Service.Start();
                    url = Service.Url();
                }
                if (problem == null && url != null)
                {
                    Say("就绪");
                    Program.Log("ready at " + url);
                    if (show) Service.OpenWindow(url);
                    ui.Post(delegate { FadeOut(); }, null);
                }
            }
            catch (Exception error) { problem = error.Message; Program.Log("unexpected: " + error); }
            if (problem != null)
            {
                Program.Log("problem: " + problem);
                string text = "没能启动 Chattera：" + problem;
                ui.Post(delegate { splash.Opacity = 1; splash.Show(); splash.ShowProblem(text); }, null);
            }
            busy = false;
        });
        worker.IsBackground = true; worker.Start();
    }

    void FadeOut()
    {
        System.Windows.Forms.Timer fade = new System.Windows.Forms.Timer();
        fade.Interval = 24; int hold = 14;
        fade.Tick += delegate
        {
            if (hold-- > 0) return;
            splash.Opacity -= 0.09;
            if (splash.Opacity <= 0.05) { fade.Stop(); fade.Dispose(); splash.Hide(); splash.Opacity = 1; }
        };
        fade.Start();
    }

    void Shutdown(bool stopServer)
    {
        if (stopServer)
        {
            splash.Opacity = 1; splash.Show(); splash.ClearProblem(); splash.Status = "正在退出 Chattera…"; splash.Refresh();
            string problem = Service.Stop();
            Program.Log(problem == null ? "Chattera quit from the launcher" : "quit failed: " + problem);
            if (problem != null) { splash.ShowProblem("没能退出：" + problem); return; }
        }
        tray.Visible = false; tray.Dispose();
        ExitThread();
    }
}
