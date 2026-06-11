import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import threading
import re
import os
import sys
import json

# ----------------------------- Configuration & Cache -----------------------------
CONFIG_FILENAME = "proxmark3_gui_config.json"
TREE_CACHE_FILENAME = "proxmark3_gui_tree_cache.json"
OPTIONS_CACHE_FILENAME = "proxmark3_gui_options.json"

def get_user_path(filename):
    return os.path.join(os.path.expanduser("~"), filename)

def load_config():
    default = {
        "pm3_folder": "",
        "com_port": "COM9",
        "encoding": "utf-8",
        "last_path": ""
    }
    try:
        if os.path.exists(get_user_path(CONFIG_FILENAME)):
            with open(get_user_path(CONFIG_FILENAME), 'r', encoding='utf-8') as f:
                data = json.load(f)
            default.update(data)
    except Exception:
        pass
    return default

def save_config(config):
    try:
        with open(get_user_path(CONFIG_FILENAME), 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Failed to save config: {e}")

def load_tree_cache(folder):
    cache_path = get_user_path(TREE_CACHE_FILENAME)
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get("pm3_folder") == folder:
                return data.get("tree", [])
    except Exception:
        pass
    return None

def save_tree_cache(folder, tree):
    cache_path = get_user_path(TREE_CACHE_FILENAME)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({"pm3_folder": folder, "tree": tree}, f, indent=2)
    except Exception as e:
        print(f"Failed to save tree cache: {e}")

def load_options_cache():
    cache_path = get_user_path(OPTIONS_CACHE_FILENAME)
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_options_cache(options):
    cache_path = get_user_path(OPTIONS_CACHE_FILENAME)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(options, f, indent=2)
    except Exception as e:
        print(f"Failed to save options cache: {e}")

# ----------------------------- Find COM Ports -----------------------------
def get_available_com_ports():
    """Возвращает список доступных COM-портов в Windows."""
    ports = []
    try:
        result = subprocess.run(
            ["wmic", "path", "Win32_SerialPort", "get", "DeviceID"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("COM"):
                ports.append(line)
    except Exception:
        pass
    return sorted(ports, key=lambda x: int(re.search(r'\d+', x).group()))

# ----------------------------- Proxmark Commander (batch mode) -----------------------------
class ProxmarkBatch:
    def __init__(self, client_folder, com_port, encoding='utf-8'):
        self.client_folder = client_folder
        self.com_port = com_port
        self.encoding = encoding
        self.lock = threading.Lock()

    def _find_proxmark_exe(self):
        exe = os.path.join(self.client_folder, "client", "proxmark3.exe")
        if os.path.isfile(exe):
            return exe
        exe = os.path.join(self.client_folder, "client", "build", "proxmark3.exe")
        if os.path.isfile(exe):
            return exe
        return None

    def _build_environment(self):
        env = os.environ.copy()
        client_dir = os.path.join(self.client_folder, "client")
        env['HOME'] = client_dir
        qt_plugin_path = os.path.join(client_dir, "libs") + os.sep
        env['QT_PLUGIN_PATH'] = qt_plugin_path
        env['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_plugin_path
        path_addition = qt_plugin_path + os.pathsep + os.path.join(qt_plugin_path.rstrip(os.sep), "shell")
        if 'PATH' in env:
            env['PATH'] = path_addition + os.pathsep + env['PATH']
        else:
            env['PATH'] = path_addition
        env['MSYSTEM'] = 'MINGW64'
        return env

    def run_command(self, cmd, timeout=30):
        with self.lock:
            exe = self._find_proxmark_exe()
            if not exe:
                return "[ERROR] proxmark3.exe not found"
            try:
                cwd = os.path.join(self.client_folder, "client")
                env = self._build_environment()
                full_cmd_str = f"{cmd}; exit"
                proc = subprocess.run(
                    [exe, "-p", self.com_port, "-c", full_cmd_str],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding=self.encoding,
                    errors='replace',
                    cwd=cwd,
                    env=env,
                    timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                return proc.stdout
            except subprocess.TimeoutExpired:
                return "[ERROR] Command timed out"
            except Exception as e:
                return f"[ERROR] {e}"

    def get_help(self, base_path=""):
        cmd = f"{base_path} -h" if base_path else "-h"
        return self.run_command(cmd, timeout=60)

# ----------------------------- Wiegand Format Chooser (Popup) -----------------------------
class FormatChooser(tk.Toplevel):
    def __init__(self, parent, formats, current_var):
        super().__init__(parent)
        self.title("Select Wiegand Format")
        self.geometry("500x400")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.var = current_var
        self.formats = formats

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree = ttk.Treeview(frame, columns=("name", "desc"), show="headings")
        self.tree.heading("name", text="Name")
        self.tree.heading("desc", text="Description")
        self.tree.column("name", width=120)
        self.tree.column("desc", width=300)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        for name, desc in sorted(self.formats, key=lambda x: x[0].lower()):
            self.tree.insert("", "end", values=(name, desc))

        self.tree.bind("<Double-1>", self.on_select)
        ttk.Button(self, text="Select", command=self.on_select).pack(pady=5)

    def on_select(self, event=None):
        sel = self.tree.selection()
        if sel:
            name = self.tree.item(sel[0])["values"][0]
            self.var.set(name)
            self.destroy()

# ----------------------------- Command Options Dialog -----------------------------
class CommandDialog(tk.Toplevel):
    def __init__(self, parent, prox, command_path, wiegand_formats, update_manual_callback, saved_options=None):
        super().__init__(parent)
        self.prox = prox
        self.command_path = command_path
        self.wiegand_formats = wiegand_formats
        self.update_manual = update_manual_callback
        self.option_widgets = {}
        self.saved_options = saved_options or {}
        self.title(f"Options for {command_path}")
        self.geometry("800x700")
        self.resizable(True, True)

        ttk.Label(self, text=f"Command: {command_path}", font=('TkDefaultFont', 10, 'bold')).pack(pady=5)

        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        opt_frame = ttk.Frame(paned)
        paned.add(opt_frame, weight=3)

        canvas = tk.Canvas(opt_frame, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(opt_frame, orient="vertical", command=canvas.yview)
        self.option_frame = ttk.Frame(canvas)
        self.option_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=self.option_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.status_label = ttk.Label(opt_frame, text="Loading options...", foreground="gray")
        self.status_label.pack(pady=5)

        self.examples_frame = ttk.LabelFrame(paned, text="Examples / Notes (double-click to apply)")
        paned.add(self.examples_frame, weight=2)
        example_container = ttk.Frame(self.examples_frame)
        example_container.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.examples_tree = ttk.Treeview(example_container, columns=("command",), show="headings", height=6)
        self.examples_tree.heading("command", text="Example")
        self.examples_tree.column("command", width=700)
        self.examples_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ex_scroll = ttk.Scrollbar(example_container, orient=tk.VERTICAL, command=self.examples_tree.yview)
        ex_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.examples_tree.configure(yscrollcommand=ex_scroll.set)
        self.examples_tree.bind("<Double-1>", self.on_example_double_click)

        ttk.Button(self, text="Execute", command=self.execute_command).pack(pady=10)

        self.update_manual(command_path)
        threading.Thread(target=self.load_options, daemon=True).start()

    def load_options(self):
        try:
            output = self.prox.run_command(f"{self.command_path} -h", timeout=30)
        except Exception as e:
            self.after(0, self.show_error, f"Failed to get help: {e}")
            return
        self.after(0, self.process_help, output)

    def clean_text(self, text):
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        text = ansi_escape.sub('', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text

    def process_help(self, output):
        output = self.clean_text(output)
        lines = output.splitlines()
        opts_start = -1
        examples_start = -1
        for i, line in enumerate(lines):
            if re.search(r'^\s*options\s*:', line.strip(), re.IGNORECASE):
                opts_start = i
            if re.search(r'^\s*examples/notes\s*:', line.strip(), re.IGNORECASE):
                examples_start = i
                break

        if opts_start == -1:
            self.after(0, self.show_error, "Options section not found.")
            return

        if examples_start != -1:
            examples_lines = [line.strip() for line in lines[examples_start+1:] if line.strip()]
            if examples_lines and 'pm3 -->' in examples_lines[-1]:
                examples_lines.pop()
            self.after(0, self.populate_examples, examples_lines)
        else:
            self.after(0, self.populate_examples, [])

        option_lines = []
        end = examples_start if examples_start != -1 else len(lines)
        for i in range(opts_start+1, end):
            line = lines[i].strip()
            if not line or line.startswith('---'):
                break
            option_lines.append(line)

        option_pattern = re.compile(
            r'^\s*(?P<flag>-{1,2}[\w-]+)(?:,\s*(?P<alias>-{1,2}[\w-]+))?(?:\s+<(?P<type>\w+)>)?\s*(?P<desc>.*)$'
        )
        for line in option_lines:
            match = option_pattern.match(line)
            if not match:
                continue
            flag = match.group('flag')
            alias = match.group('alias')
            opt_type = match.group('type')
            desc = match.group('desc').strip() if match.group('desc') else ""

            if flag == '-h' or flag == '--help':
                continue

            self.after(0, self.create_option_widget, flag, alias, opt_type, desc)

        self.status_label.config(text="Ready")
        self.after(50, self.apply_saved_options)

    def populate_examples(self, examples):
        self.examples_tree.delete(*self.examples_tree.get_children())
        for ex in examples:
            self.examples_tree.insert("", "end", values=(ex,))

    def create_option_widget(self, flag, alias, opt_type, desc):
        frame = ttk.Frame(self.option_frame)
        frame.pack(fill='x', padx=5, pady=2)

        display = flag
        if alias:
            display += f", {alias}"
        label = ttk.Label(frame, text=display, width=30, anchor='w')
        label.pack(side='left')

        if opt_type is None:
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(frame, variable=var, command=lambda: self.update_manual(self.build_command()))
            cb.pack(side='left', padx=5)
            self.option_widgets[flag] = ('bool', var, cb)
        elif opt_type == 'format':
            var = tk.StringVar()
            combo_frame = ttk.Frame(frame)
            combo_frame.pack(side='left', padx=5)
            entry = ttk.Entry(combo_frame, textvariable=var, width=25)
            entry.pack(side='left')
            entry.bind("<Button-1>", lambda e: self.choose_format(var))
            entry.bind("<KeyRelease>", lambda e: self.update_manual(self.build_command()))
            type_label = ttk.Label(frame, text="<format>", foreground='gray')
            type_label.pack(side='left', padx=5)
            self.option_widgets[flag] = ('value', var, entry)
        else:
            var = tk.StringVar()
            entry = ttk.Entry(frame, textvariable=var, width=25)
            entry.pack(side='left', padx=5)
            entry.bind("<KeyRelease>", lambda e: self.update_manual(self.build_command()))
            type_label = ttk.Label(frame, text=f"<{opt_type}>", foreground='gray')
            type_label.pack(side='left', padx=5)
            self.option_widgets[flag] = ('value', var, entry)

        if desc:
            desc_label = ttk.Label(frame, text=desc, wraplength=250, foreground='gray')
            desc_label.pack(side='left', padx=5)

    def apply_saved_options(self):
        if not self.saved_options:
            return
        for flag, (kind, var, widget) in self.option_widgets.items():
            if flag in self.saved_options:
                saved_val = self.saved_options[flag]
                if kind == 'bool':
                    var.set(bool(saved_val))
                else:
                    var.set(str(saved_val))
        self.update_manual(self.build_command())

    def choose_format(self, var):
        FormatChooser(self, self.wiegand_formats, var)

    def build_command(self):
        args = [self.command_path]
        for flag, (kind, var, *_) in self.option_widgets.items():
            if kind == 'bool':
                if var.get():
                    args.append(flag)
            else:
                val = var.get().strip()
                if val:
                    args.append(flag)
                    args.append(val)
        return ' '.join(args)

    def on_example_double_click(self, event):
        sel = self.examples_tree.selection()
        if not sel:
            return
        example = self.examples_tree.item(sel[0])['values'][0]
        cmd_part = example.split('->')[0].strip()
        parts = cmd_part.split()
        if not parts:
            return
        flags = list(self.option_widgets.keys())
        i = 1
        while i < len(parts):
            token = parts[i]
            if token in flags:
                widget_type, var, widget = self.option_widgets[token]
                if widget_type == 'bool':
                    var.set(True)
                    i += 1
                else:
                    i += 1
                    if i < len(parts):
                        var.set(parts[i])
                        i += 1
            else:
                i += 1
        self.update_manual(self.build_command())

    def show_error(self, msg):
        self.status_label.config(text=msg, foreground='red')

    def execute_command(self):
        cmd = self.build_command()
        self.update_manual(cmd)
        current_options = {}
        for flag, (kind, var, *_) in self.option_widgets.items():
            current_options[flag] = var.get()
        self.master.save_command_options(self.command_path, current_options)
        self.destroy()
        self.master.run_manual_command(cmd)

# ----------------------------- GUI Application -----------------------------
class ProxmarkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Proxmark3 GUI Commander")
        self.geometry("1200x700")
        self.minsize(800, 500)

        config = load_config()
        self.pm3_folder = tk.StringVar(value=config.get("pm3_folder", ""))
        self.com_port = tk.StringVar(value=config.get("com_port", "COM9"))
        self.encoding_var = tk.StringVar(value=config.get("encoding", "utf-8"))
        self.last_path = config.get("last_path", "")

        self.prox = None
        self.path_to_iid = {}
        self.tree_building = False
        self.wiegand_formats = []
        self.wiegand_loaded = threading.Event()
        self.tree_cache_loaded = False
        self.options_cache = load_options_cache()

        self.create_widgets()
        self.create_menu()

        # Восстановление последнего узла после загрузки дерева
        if self.pm3_folder.get():
            self.init_prox()
            cached = load_tree_cache(self.pm3_folder.get())
            if cached:
                self.populate_tree_from_cache(cached)
                self.tree_cache_loaded = True
                self.status_var.set("Ready (cached tree)")
                self.restore_last_path()
            else:
                self.build_tree()

    def create_widgets(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # Левая панель
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)

        # Строка выбора COM-порта
        com_frame = ttk.Frame(left_frame)
        com_frame.pack(fill=tk.X, padx=5, pady=(5,0))
        ttk.Label(com_frame, text="COM Port:").pack(side=tk.LEFT)

        self.com_combo = ttk.Combobox(com_frame, textvariable=self.com_port, state="readonly", width=10)
        self.com_combo.pack(side=tk.LEFT, padx=5)
        self._update_combo_ports()

        # Кнопка обновления 🔄
        refresh_btn = ttk.Button(com_frame, text="\U0001F5D8", width=3, command=self.refresh_com_ports)
        refresh_btn.pack(side=tk.LEFT)

        self.com_combo.bind("<<ComboboxSelected>>", self.on_com_port_changed)

        ttk.Label(left_frame, text="Command Tree", font=('TkDefaultFont', 10, 'bold')).pack(pady=(5,0))
        tree_container = ttk.Frame(left_frame)
        tree_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree = ttk.Treeview(tree_container, show='tree')
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Double-1>', self.on_tree_double_click)
        self.tree.bind('<<TreeviewOpen>>', self.on_tree_open)
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Execute with options", command=self.execute_selected_command)
        self.tree.bind('<Button-3>', self.show_context_menu)

        # Правая панель
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=3)
        ttk.Label(right_frame, text="Session Log", font=('TkDefaultFont', 10, 'bold')).pack(pady=(5,0))
        self.log_text = scrolledtext.ScrolledText(
            right_frame, wrap=tk.WORD, state='normal',
            font=('Consolas', 9), bg='black', fg='lightgreen', insertbackground='white'
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        cmd_frame = ttk.Frame(right_frame)
        cmd_frame.pack(fill=tk.X, padx=5, pady=(0,5))
        ttk.Label(cmd_frame, text="Manual command:").pack(side=tk.LEFT)
        self.cmd_entry = ttk.Entry(cmd_frame, font=('Consolas', 10))
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.cmd_entry.bind('<Return>', self.send_manual_command)
        ttk.Button(cmd_frame, text="Send", command=self.send_manual_command).pack(side=tk.LEFT)

        ttk.Button(right_frame, text="Build / Refresh Tree", command=self.build_tree).pack(pady=5)

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    def _update_combo_ports(self):
        ports = get_available_com_ports()
        self.com_combo['values'] = ports
        if self.com_port.get() not in ports and ports:
            self.com_port.set(ports[0])

    def refresh_com_ports(self):
        """Заново опрашивает COM-порты и обновляет ComboBox."""
        self._update_combo_ports()
        self.log_message("[INFO] COM port list refreshed.\n")

    def on_com_port_changed(self, event=None):
        new_port = self.com_port.get()
        self.log_message(f"[INFO] COM port changed to {new_port}. Reconnecting...\n")
        self.save_current_config()
        self.init_prox()

    def create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Exit", command=self.on_closing)

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Select Proxmark folder...", command=self.select_folder)
        enc_menu = tk.Menu(settings_menu, tearoff=0)
        encodings = [
            ("UTF-8", "utf-8"), ("CP1251 (Windows Cyrillic)", "cp1251"),
            ("CP866 (DOS Cyrillic)", "cp866"), ("CP1250 (Central European)", "cp1250"),
            ("Latin-1", "latin-1"), ("IBM850", "cp850")
        ]
        for label, code in encodings:
            enc_menu.add_radiobutton(label=label, variable=self.encoding_var, value=code,
                                     command=self.on_encoding_changed)
        settings_menu.add_cascade(label="Output Encoding", menu=enc_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", "Proxmark3 GUI Commander v26\nIcon refresh + tree auto-close."))

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select Proxmark root folder (contains 'client' subfolder)")
        if folder:
            if not os.path.isdir(os.path.join(folder, "client")):
                messagebox.showwarning("Warning", "В выбранной папке нет подпапки 'client'.")
                return
            self.pm3_folder.set(folder)
            self.log_message(f"[INFO] Selected folder: {folder}\n")
            self.save_current_config()
            self.init_prox()
            self.build_tree()

    def save_current_config(self):
        config = {
            "pm3_folder": self.pm3_folder.get(),
            "com_port": self.com_port.get(),
            "encoding": self.encoding_var.get(),
            "last_path": self.last_path
        }
        save_config(config)

    def on_encoding_changed(self):
        self.log_message(f"[INFO] Encoding changed to {self.encoding_var.get()}. Reinit.\n")
        self.save_current_config()
        self.init_prox()
        self.build_tree()

    def init_prox(self):
        if self.pm3_folder.get() and os.path.isdir(os.path.join(self.pm3_folder.get(), "client")):
            self.log_message(f"[INFO] Connecting to {self.com_port.get()}...\n")
            self.prox = ProxmarkBatch(self.pm3_folder.get(), self.com_port.get(), self.encoding_var.get())
            self.wiegand_loaded.clear()
            threading.Thread(target=self._init_sequence, daemon=True).start()
            self.status_var.set("Connected")
        else:
            self.prox = None
            self.status_var.set("Folder not set")

    def _init_sequence(self):
        self.load_wiegand_formats()
        self.show_hw_version()

    def load_wiegand_formats(self):
        if not self.prox:
            return
        self.log_message("[INFO] Loading Wiegand format list...\n")
        output = self.prox.run_command("wiegand list", timeout=15)
        lines = output.splitlines()
        formats = []
        start = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('[=]') and '----' in stripped:
                start = i + 1
                break
        if start > 0:
            for line in lines[start:]:
                stripped = line.strip()
                if not stripped.startswith('[=]'):
                    continue
                token = stripped[3:].strip()
                if token:
                    parts = token.split(None, 1)
                    name = parts[0]
                    desc = parts[1] if len(parts) > 1 else ""
                    formats.append((name, desc))
        self.wiegand_formats = formats
        self.wiegand_loaded.set()
        self.log_message(f"[INFO] Loaded {len(formats)} Wiegand formats.\n")

    def show_hw_version(self):
        if not self.prox:
            return
        self.log_message("[INFO] Getting hardware version...\n")
        output = self.prox.run_command("hw version", timeout=15)
        self.log_message(self.clean_text(output) + "\n")

    def clean_text(self, text):
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        text = ansi_escape.sub('', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text

    def build_tree(self):
        if self.tree_building:
            return
        if not self.prox:
            messagebox.showerror("Error", "Сначала выберите папку Proxmark.")
            return
        self.tree_building = True
        self.status_var.set("Building tree...")
        self.tree.delete(*self.tree.get_children())
        self.path_to_iid.clear()
        self.tree_cache_loaded = False
        threading.Thread(target=self._tree_builder_thread, daemon=True).start()

    def _tree_builder_thread(self):
        self.wiegand_loaded.wait(timeout=10)
        tree_data = []
        self._build_node('', tree_data)
        self.tree_building = False
        self.status_var.set("Ready")
        save_tree_cache(self.pm3_folder.get(), tree_data)
        # Восстанавливаем последний путь после построения
        self.after(100, self.restore_last_path)

    def _build_node(self, base_path, parent_list):
        output = self.prox.get_help(base_path)
        if not output:
            return
        output_clean = self.clean_text(output)
        self.log_message(f"[CMD] {base_path} -h\n")
        self.log_message(output_clean + "\n")
        commands = self.parse_help_output(output_clean)
        for name, desc, is_folder in commands:
            full_path = f"{base_path} {name}".strip()
            node = {"name": name, "path": full_path, "desc": desc, "children": []}
            self.after(0, self._add_tree_node, base_path, name, full_path, desc)
            if is_folder:
                self._build_node(full_path, node["children"])
            parent_list.append(node)

    def _add_tree_node(self, parent_path, name, full_path, desc):
        parent_iid = '' if parent_path == '' else self.path_to_iid.get(parent_path, '')
        iid = full_path
        if iid in self.path_to_iid:
            return
        display = f"{name} ({desc})" if desc else name
        self.tree.insert(parent_iid, 'end', iid=iid, text=display, open=False)
        self.path_to_iid[full_path] = iid

    def populate_tree_from_cache(self, tree_data):
        def add_nodes(parent_list, parent_iid=''):
            for node in parent_list:
                iid = node["path"]
                display = f"{node['name']} ({node['desc']})" if node.get('desc') else node['name']
                self.tree.insert(parent_iid, 'end', iid=iid, text=display, open=False)
                self.path_to_iid[iid] = iid
                if node.get("children"):
                    add_nodes(node["children"], iid)
        add_nodes(tree_data)

    def restore_last_path(self):
        """Раскрывает последний выбранный узел и заполняет Manual command."""
        if not self.last_path:
            return
        if self.last_path in self.path_to_iid:
            iid = self.path_to_iid[self.last_path]
            self.tree.see(iid)
            self.tree.selection_set(iid)
            # Закроем все другие ветви, открываем только нужную
            self._close_all_except_path(self.last_path)
            # Заполним Manual command
            self.cmd_entry.delete(0, tk.END)
            self.cmd_entry.insert(0, self.last_path)
            self.last_path = ""  # сбросим, чтобы не повторять

    def parse_help_output(self, text):
        commands = []
        lines = text.splitlines()
        start_index = 0
        for i, line in enumerate(lines):
            if re.search(r'---', line):
                start_index = i + 1
                break
        for line in lines[start_index:]:
            line = line.strip()
            if not line or line.startswith('---') or line.startswith('help') or line.startswith('['):
                continue
            match = re.match(r'^(\S+)(?:\s+(.*))?$', line)
            if match:
                name = match.group(1)
                rest = match.group(2) or ''
                folder_match = re.search(r'\{(.+?)\.\.\.\s*\}', rest)
                if folder_match:
                    desc = folder_match.group(1).strip()
                    is_folder = True
                else:
                    if '...' in rest:
                        desc = rest.split('...')[0].strip()
                        is_folder = True
                    else:
                        desc = rest.strip()
                        is_folder = False
                desc = re.sub(r'\.\.\.$', '', desc).strip()
                desc = desc.replace('{', '').replace('}', '').strip()
                commands.append((name, desc, is_folder))
        return commands

    def on_tree_open(self, event):
        """При открытии узла закрываем все остальные."""
        item = self.tree.focus()
        if not item:
            return
        # Получаем путь от корня
        path_parts = []
        current = item
        while current:
            path_parts.insert(0, self.tree.item(current, "text").split(" (")[0])
            current = self.tree.parent(current)
        full_path = " ".join(path_parts)
        self._close_all_except_path(full_path)

    def _close_all_except_path(self, path):
        """Закрывает все узлы, кроме тех, что входят в путь path."""
        # Откроем все родительские узлы пути
        if not path:
            return
        parts = path.split()
        # Закроем всех детей корня, кроме первого элемента пути
        root_children = self.tree.get_children('')
        first_part = parts[0] if parts else ""
        for child in root_children:
            if self.tree.item(child, "text").split(" (")[0] != first_part:
                self.tree.item(child, open=False)
            else:
                self.tree.item(child, open=True)
        # Теперь пройдём по родительской цепочке
        parent = self.path_to_iid.get(first_part, '')
        for part in parts[1:]:
            children = self.tree.get_children(parent)
            for child in children:
                if self.tree.item(child, "text").split(" (")[0] == part:
                    self.tree.item(child, open=True)
                    parent = child
                    break
            # Закроем всех других детей
            for child in children:
                if self.tree.item(child, "text").split(" (")[0] != part:
                    self.tree.item(child, open=False)

    def save_command_options(self, command_path, options):
        self.options_cache[command_path] = options
        save_options_cache(self.options_cache)

    def on_tree_select(self, event):
        selection = self.tree.selection()
        if selection:
            full_path = selection[0]
            if not self.tree.get_children(selection[0]):
                self.cmd_entry.delete(0, tk.END)
                self.cmd_entry.insert(0, full_path)
                self.last_path = full_path
                self.save_current_config()

    def on_tree_double_click(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        full_path = selection[0]
        if self.tree.get_children(selection[0]):
            return
        if not self.prox:
            return
        if not self.wiegand_loaded.is_set():
            self.log_message("[WARN] Wiegand formats still loading, please wait...\n")
            return
        saved = self.options_cache.get(full_path, {})
        CommandDialog(self, self.prox, full_path, self.wiegand_formats, self.set_manual_command, saved)

    def execute_selected_command(self):
        selection = self.tree.selection()
        if not selection:
            return
        full_path = selection[0]
        if self.tree.get_children(selection[0]):
            return
        if not self.prox:
            return
        if not self.wiegand_loaded.is_set():
            self.log_message("[WARN] Wiegand formats still loading, please wait...\n")
            return
        saved = self.options_cache.get(full_path, {})
        CommandDialog(self, self.prox, full_path, self.wiegand_formats, self.set_manual_command, saved)

    def set_manual_command(self, cmd):
        self.cmd_entry.delete(0, tk.END)
        self.cmd_entry.insert(0, cmd)

    def send_manual_command(self, event=None):
        cmd = self.cmd_entry.get().strip()
        if cmd:
            self.run_manual_command(cmd)
            self.cmd_entry.delete(0, tk.END)

    def run_manual_command(self, cmd):
        if not self.prox:
            self.log_message("[ERROR] Proxmark not initialized.\n")
            return
        self.status_var.set(f"Running: {cmd}")
        self.log_message(f"[EXEC] {cmd}\n")
        def worker():
            output = self.prox.run_command(cmd, timeout=60)
            self.log_message(output + "\n")
            self.status_var.set("Ready")
        threading.Thread(target=worker, daemon=True).start()

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)

    def log_message(self, text):
        if hasattr(self, 'log_text') and self.log_text.winfo_exists():
            self.log_text.insert(tk.END, text)
            self.log_text.see(tk.END)

    def on_closing(self):
        self.destroy()

if __name__ == '__main__':
    app = ProxmarkApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
