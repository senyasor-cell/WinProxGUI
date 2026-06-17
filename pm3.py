import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import threading
import re
import os
import sys
import json
import queue
import time
from winpty import PtyProcess

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
        "last_path": "",
        "last_full_command": "",
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
        self.last_full_command = config.get("last_full_command", "")

        # PTY related
        self.pty_process = None
        self.connected = False
        self.pty_lock = threading.Lock()
        self._closing = threading.Event()

        # Sync command helpers
        self.output_buffer = []
        self.sync_event = threading.Event()
        self.sync_start_index = 0
        self.sync_output = ""

        # GUI components
        self.log_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.path_to_iid = {}
        self.tree_building = False
        self.wiegand_formats = []
        self.wiegand_loaded = threading.Event()
        self.options_cache = load_options_cache()
        self.current_command_path = ""
        self.option_widgets = {}
        self.option_panel_content = None

        self.create_widgets()
        self.create_menu()
        self.after(50, self.show_manual_input)
        self.after(100, self._process_queues)

        if self.pm3_folder.get():
            cached = load_tree_cache(self.pm3_folder.get())
            if cached:
                self.populate_tree_from_cache(cached)
                self.status_var.set("Ready (cached tree)")
                self.restore_last_state()
            else:
                self.status_var.set("Not connected. Press Connect.")

    # ---------- Виджеты ----------
    def create_widgets(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)

        # Верхняя панель: COM Port, кнопка обновления, Connect
        com_frame = ttk.Frame(left_frame)
        com_frame.pack(fill=tk.X, padx=5, pady=(5,0))
        ttk.Label(com_frame, text="COM Port:").pack(side=tk.LEFT)
        self.com_combo = ttk.Combobox(com_frame, textvariable=self.com_port, state="readonly", width=10)
        self.com_combo.pack(side=tk.LEFT, padx=5)

        self.connect_btn = ttk.Button(com_frame, text="Connect", width=10, command=self.toggle_connection)
        self.connect_btn.pack(side=tk.LEFT, padx=5)
        refresh_ports_btn = ttk.Button(com_frame, text="\U0001F5D8", width=3, command=self.refresh_com_ports)
        refresh_ports_btn.pack(side=tk.RIGHT, padx=(0, 5))

        self.com_combo.bind("<<ComboboxSelected>>", self.on_com_port_changed)

        tree_header = ttk.Frame(left_frame)
        tree_header.pack(fill=tk.X, padx=5, pady=(5,0))
        ttk.Label(tree_header, text="Command Tree", font=('TkDefaultFont', 10, 'bold')).pack(side=tk.LEFT, padx=(0, 10))
        self.tree_refresh_btn = ttk.Button(tree_header, text="\U0001F5D8", width=3, command=self.build_tree)
        self.tree_refresh_btn.pack(side=tk.RIGHT)

        tree_container = ttk.Frame(left_frame)
        tree_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_container, show='tree')
        self.tree.grid(row=0, column=0, sticky='nsew')

        v_scrollbar = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky='ns')
        h_scrollbar = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree.xview)
        h_scrollbar.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Double-1>', self.on_tree_double_click)
        self.tree.bind('<<TreeviewOpen>>', self.on_tree_open)
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Execute with options", command=self.execute_selected_command)
        self.tree.bind('<Button-3>', self.show_context_menu)

        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=3)

        bottom_panel = ttk.Frame(right_frame)
        bottom_panel.pack(side=tk.BOTTOM, fill=tk.X)

        status_frame = ttk.Frame(bottom_panel, height=80)
        status_frame.pack(fill=tk.X, padx=5, pady=(5,0))
        status_frame.pack_propagate(False)
        self.status_text = scrolledtext.ScrolledText(
            status_frame, wrap=tk.WORD, font=('Consolas', 9), bg='lightyellow', state='normal', height=4
        )
        self.status_text.pack(fill=tk.BOTH, expand=True)

        cmd_frame = ttk.Frame(bottom_panel, height=40)
        cmd_frame.pack(fill=tk.X, padx=5, pady=5)
        cmd_frame.pack_propagate(False)
        ttk.Label(cmd_frame, text="Manual command:").pack(side=tk.LEFT)
        self.cmd_entry = ttk.Entry(cmd_frame, font=('Consolas', 10))
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.cmd_entry.bind('<Return>', lambda e: self.send_manual_command())
        send_btn = ttk.Button(cmd_frame, text="\u25B6", width=3, command=self.send_manual_command)
        send_btn.pack(side=tk.LEFT, padx=(0,5))
        cmd_btn = ttk.Button(cmd_frame, text="cmd", width=4, command=self.open_cmd)
        cmd_btn.pack(side=tk.LEFT, padx=(5,0))

        self.right_paned = ttk.PanedWindow(right_frame, orient=tk.VERTICAL)
        self.right_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.options_frame = ttk.Frame(right_frame)

        self.log_frame = ttk.Frame(self.right_paned)
        self.right_paned.add(self.log_frame, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            self.log_frame, wrap=tk.WORD, state='normal',
            font=('Consolas', 9), bg='black', fg='lightgreen', insertbackground='white'
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------- Управление подключением ----------
    def toggle_connection(self):
        if self.connected:
            self.disconnect_pty()
        else:
            self.connect_pty()

    def connect_pty(self):
        if self.connected:
            return
        folder = self.pm3_folder.get()
        if not folder or not os.path.isdir(os.path.join(folder, "client")):
            messagebox.showerror("Error", "Proxmark folder not set or invalid.")
            return

        exe = os.path.join(folder, "client", "proxmark3.exe")
        if not os.path.isfile(exe):
            exe = os.path.join(folder, "client", "build", "proxmark3.exe")
        if not os.path.isfile(exe):
            messagebox.showerror("Error", "proxmark3.exe not found in client folder.")
            return

        env = os.environ.copy()
        client_dir = os.path.join(folder, "client")
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

        try:
            self.pty_process = PtyProcess.spawn(
                [exe, '-p', self.com_port.get(), '-w'],
                cwd=client_dir,
                env=env,
                dimensions=(40, 120)
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start PTY: {e}")
            return

        self.connected = True
        self.connect_btn.config(text="Disconnect")
        self.status_var.set(f"Connected to {self.com_port.get()}")
        self.status_queue.put(f"[INFO] Connected to {self.com_port.get()}")

        with self.pty_lock:
            self.output_buffer.clear()
            self.sync_event.clear()

        threading.Thread(target=self._pty_reader, daemon=True).start()

        # Ждём появления первого промпта, чтобы буфер содержал всё приветствие
        self.sync_event.wait(timeout=10)

        # Теперь запускаем загрузку форматов (и дерево, если нужно)
        threading.Thread(target=self._load_initial_data, daemon=True).start()

        if not self.tree.get_children():
            self.build_tree()

    def disconnect_pty(self):
        if not self.connected:
            return
        self.connected = False
        if self.pty_process:
            try:
                self.pty_process.write("exit\n")
                self.pty_process.close()
            except:
                pass
            self.pty_process = None
        self.sync_event.set()
        self.after(0, self._update_connection_status)

    def _update_connection_status(self):
        if self._closing.is_set() or not self.winfo_exists():
            return
        self.connected = False
        self.pty_process = None
        self.connect_btn.config(text="Connect")
        self.status_var.set("Disconnected")
        self.status_queue.put("[INFO] Pseudo‑terminal closed.")

    def _pty_reader(self):
        try:
            while not self._closing.is_set() and self.connected and self.pty_process and self.pty_process.isalive():
                data = self.pty_process.read(1024)
                if data:
                    lines = data.splitlines(keepends=True)
                    with self.pty_lock:
                        for line in lines:
                            clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
                            self.output_buffer.append(clean)
                            self.log_queue.put(clean)
                            if clean.strip().endswith("pm3 -->") or clean.strip().endswith("pm3 #"):
                                self.sync_event.set()
        except EOFError:
            pass
        except Exception as e:
            self.log_queue.put(f"[ERROR] PTY reader: {e}\n")
        finally:
            if not self._closing.is_set():
                self.after(0, self._update_connection_status)
            self.sync_event.set()

    # ---------- Синхронное выполнение команд через PTY ----------
    def execute_command_sync(self, command, timeout=30):
        if not self.connected or not self.pty_process or not self.pty_process.isalive():
            return "[ERROR] Not connected."

        with self.pty_lock:
            self.sync_event.clear()
            start_idx = len(self.output_buffer)
            self.pty_process.write(command + "\n")
        self.sync_event.wait(timeout)

        with self.pty_lock:
            end_idx = len(self.output_buffer)
            if self.sync_event.is_set():
                prompt_idx = -1
                for i in range(end_idx - 1, start_idx - 1, -1):
                    line = self.output_buffer[i].strip()
                    if line.endswith("pm3 -->") or line.endswith("pm3 #"):
                        prompt_idx = i
                        break
                if prompt_idx != -1:
                    result = ''.join(self.output_buffer[start_idx:prompt_idx])
                else:
                    result = ''.join(self.output_buffer[start_idx:end_idx])
            else:
                result = ''.join(self.output_buffer[start_idx:end_idx])
            return result

    # ---------- Загрузка Wiegand форматов ----------
    def _load_initial_data(self):
        output = self.execute_command_sync("wiegand list", timeout=15)
        self._parse_wiegand(output)

    def _parse_wiegand(self, output):
        formats = []
        lines = output.splitlines()
        start = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('[=]') and '----' in stripped:
                start = i + 1
                break
        if start == -1:
            self.wiegand_formats = formats
            self.wiegand_loaded.set()
            self.status_queue.put(f"[INFO] Loaded {len(formats)} Wiegand formats.")
            return
        for line in lines[start:]:
            stripped = line.strip()
            if not stripped.startswith('[=]'):
                continue
            token = stripped[3:].strip()
            if not token:
                continue
            if token.strip('-') == '':
                continue
            parts = token.split(None, 1)
            if len(parts) < 2:
                continue
            name = parts[0]
            desc = parts[1].strip()
            if name == "Name" and desc == "Description":
                continue
            formats.append((name, desc))
        self.wiegand_formats = formats
        self.wiegand_loaded.set()
        self.status_queue.put(f"[INFO] Loaded {len(formats)} Wiegand formats.")

    # ---------- Построение дерева через PTY ----------
    def build_tree(self):
        if self.tree_building:
            return
        if not self.connected:
            messagebox.showerror("Error", "Not connected. Press Connect first.")
            return

        self.tree_building = True
        self.status_var.set("Building tree...")
        self.tree.delete(*self.tree.get_children())
        self.path_to_iid.clear()
        folder = self.pm3_folder.get()
        threading.Thread(target=self._tree_builder_thread, args=(folder,), daemon=True).start()
        self.show_manual_input()

    def _tree_builder_thread(self, folder):
        self.wiegand_loaded.wait(timeout=10)
        tree_data = []
        self._build_node('', tree_data)
        self.tree_building = False
        self.after(0, lambda: self.status_var.set("Ready"))
        save_tree_cache(folder, tree_data)
        self.after(100, self.restore_last_state)
        self.after(600, self.show_manual_input)

    def _build_node(self, base_path, parent_list):
        cmd = f"{base_path} -h" if base_path else "-h"
        output = self.execute_command_sync(cmd, timeout=60)
        if not output:
            return
        output_clean = self.clean_text(output)
        self.log_queue.put(f"[CMD] {cmd}\n")
        self.log_queue.put(output_clean + "\n")
        commands = self.parse_help_output(output_clean)
        for name, desc, is_folder in commands:
            full_path = f"{base_path} {name}".strip()
            node = {"name": name, "path": full_path, "desc": desc, "children": []}
            self.after(0, self._add_tree_node, base_path, name, full_path, desc)
            if is_folder:
                self._build_node(full_path, node["children"])
            parent_list.append(node)

    # ---------- Отправка команд в PTY ----------
    def run_manual_command(self, cmd):
        if not self.connected or not self.pty_process or not self.pty_process.isalive():
            self.status_queue.put("[ERROR] Not connected.")
            return
        self.status_var.set(f"Running: {cmd}")
        self.status_queue.put(f"[EXEC] {cmd}")
        with self.pty_lock:
            self.pty_process.write(cmd + "\n")

    # ---------- Открытие внешней консоли ----------
    def open_cmd(self):
        self.show_manual_input()
        if self.connected:
            self.disconnect_pty()
            time.sleep(0.3)

        folder = self.pm3_folder.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Proxmark folder not set.")
            return
        cmd_text = self.cmd_entry.get().strip()
        if cmd_text:
            vbs_path = os.path.join(folder, "_type_cmd.vbs")
            try:
                with open(vbs_path, 'w') as f:
                    f.write('Set WshShell = WScript.CreateObject("WScript.Shell")\n')
                    f.write('WScript.Sleep 2000\n')
                    for ch in cmd_text:
                        if ch in ('+', '^', '%', '~', '(', ')', '{', '}', '[', ']'):
                            f.write(f'WshShell.SendKeys "{{{ch}}}"\n')
                        else:
                            f.write(f'WshShell.SendKeys "{ch}"\n')
                        f.write('WScript.Sleep 10\n')
                subprocess.Popen(['wscript.exe', vbs_path], creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as e:
                self.status_queue.put(f"[WARN] VBScript error: {e}")
        try:
            subprocess.Popen(['cmd.exe', '/k', 'pm3.bat'], cwd=folder, creationflags=subprocess.CREATE_NEW_CONSOLE)
            self.status_queue.put("[INFO] Command typed in console. Press Enter to execute.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open cmd: {e}")

    # ---------- Очереди ----------
    def _process_queues(self):
        if self._closing.is_set():
            return
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self.log_message(msg)
            except queue.Empty:
                break
        while not self.status_queue.empty():
            try:
                msg = self.status_queue.get_nowait()
                self.status_message(msg)
            except queue.Empty:
                break
        self.after(100, self._process_queues)

    # ---------- Панель опций ----------
    def show_manual_input(self):
        for widget in self.options_frame.winfo_children():
            widget.destroy()
        self.option_widgets = {}
        self.current_command_path = ""
        self.option_panel_content = None
        try:
            self.right_paned.forget(self.options_frame)
        except tk.TclError:
            pass

    def load_command_options(self, command_path):
        if not self.connected:
            self.status_queue.put("[ERROR] Not connected.")
            return
        if not self.wiegand_loaded.is_set():
            self.status_queue.put("[WARN] Wiegand formats still loading...")
            return
        self.current_command_path = command_path
        saved = self.options_cache.get(command_path, {})
        for widget in self.options_frame.winfo_children():
            widget.destroy()
        self.option_widgets = {}

        self.right_paned.insert(0, self.options_frame)
        self.right_paned.pane(self.options_frame, weight=0)
        self.right_paned.pane(self.log_frame, weight=1)
        self.update_idletasks()
        self.right_paned.sashpos(0, 300)

        self.option_panel_content = ttk.Frame(self.options_frame)
        self.option_panel_content.pack(fill=tk.BOTH, expand=True)

        ttk.Label(self.option_panel_content, text=f"Command: {command_path}", font=('TkDefaultFont', 10, 'bold')).pack(pady=5, anchor='w')

        examples_frame = ttk.LabelFrame(self.option_panel_content, text="Examples / Notes (double-click to apply)")
        examples_frame.pack(fill=tk.BOTH, expand=False, padx=5, pady=5)
        self.examples_tree = ttk.Treeview(examples_frame, columns=("command",), show="tree", height=4)
        self.examples_tree.column("#0", width=0, stretch=False)
        self.examples_tree.column("command", width=400)
        self.examples_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ex_scroll = ttk.Scrollbar(examples_frame, orient=tk.VERTICAL, command=self.examples_tree.yview)
        ex_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.examples_tree.configure(yscrollcommand=ex_scroll.set)
        self.examples_tree.bind("<Double-1>", self.on_example_double_click)

        opt_canvas = tk.Canvas(self.option_panel_content, borderwidth=0, highlightthickness=0)
        opt_scroll = ttk.Scrollbar(self.option_panel_content, orient=tk.VERTICAL, command=opt_canvas.yview)
        self.options_container = ttk.Frame(opt_canvas)
        self.options_container.bind("<Configure>", lambda e: opt_canvas.configure(scrollregion=opt_canvas.bbox("all")))
        opt_canvas.create_window((0,0), window=self.options_container, anchor="nw")
        opt_canvas.configure(yscrollcommand=opt_scroll.set)
        opt_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        opt_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_label = ttk.Label(self.option_panel_content, text="Loading options...", foreground="gray")
        self.status_label.pack(pady=5)

        self.update_manual(command_path)
        threading.Thread(target=self._load_options_thread, args=(command_path, saved), daemon=True).start()

    def _load_options_thread(self, command_path, saved_options):
        output = self.execute_command_sync(f"{command_path} -h", timeout=60)
        if output is None:
            self.after(0, self._show_error, "Failed to get help (timeout).")
            return
        self.after(0, self._process_options_help, output, saved_options)

    def _process_options_help(self, output, saved_options):
        output = self.clean_text(output)
        lines = output.splitlines()
        opts_start = -1
        usage_start = -1
        usage_end = -1
        examples_start = -1

        for i, line in enumerate(lines):
            if re.search(r'^\s*usage\s*:', line.strip(), re.IGNORECASE):
                usage_start = i
                for j in range(i+1, len(lines)):
                    if re.search(r'^[a-zA-Z].*:\s*$', lines[j].strip()):
                        usage_end = j
                        break
                if usage_end == -1:
                    usage_end = len(lines)
                break

        if usage_start != -1:
            usage_lines = []
            for i in range(usage_start+1, usage_end):
                line = lines[i].strip()
                if line:
                    usage_lines.append(f"[USAGE] {line}")
            usage_str = '\n'.join(usage_lines)
            self.status_queue.put(usage_str)

        for i, line in enumerate(lines):
            if re.search(r'examples', line, re.IGNORECASE):
                examples_start = i + 1
                break

        if examples_start != -1:
            examples_lines = []
            for i in range(examples_start, len(lines)):
                line = lines[i].strip()
                if not line:
                    break
                examples_lines.append(line)
            if examples_lines and 'pm3 -->' in examples_lines[-1]:
                examples_lines.pop()
            self.examples_tree.delete(*self.examples_tree.get_children())
            for ex in examples_lines:
                self.examples_tree.insert("", "end", values=(ex,))
        else:
            self.examples_tree.delete(*self.examples_tree.get_children())

        for i, line in enumerate(lines):
            if re.search(r'^\s*options\s*:', line.strip(), re.IGNORECASE):
                opts_start = i
                break

        if opts_start == -1:
            self._show_error("Options section not found.")
            return

        option_lines = []
        end = examples_start if examples_start != -1 else len(lines)
        for i in range(opts_start+1, end):
            line = lines[i].strip()
            if not line or line.startswith('---'):
                break
            option_lines.append(line)

        option_pattern = re.compile(
            r'^\s*(?P<flag>-{0,2}[\w-]+)(?:,\s*(?P<alias>-{0,2}[\w-]+))?(?:\s+<(?P<type>\w+)>)?\s*(?P<desc>.*)$'
        )

        for child in self.options_container.winfo_children():
            child.destroy()

        for line in option_lines:
            match = option_pattern.match(line)
            if not match:
                continue
            flag = match.group('flag')
            alias = match.group('alias')
            opt_type = match.group('type')
            desc = match.group('desc').strip() if match.group('desc') else ""
            if flag.lower() in ('h', 'help', '-h', '--help'):
                continue
            self._create_option_widget(self.options_container, flag, alias, opt_type, desc)

        if saved_options:
            for flag, data in self.option_widgets.items():
                kind = data[0]
                var = data[1]
                if flag in saved_options:
                    saved_val = saved_options[flag]
                    if kind == 'bool':
                        var.set(bool(saved_val))
                    elif kind == 'format':
                        full_str = next((f"{n} ({d})" for n, d in self.wiegand_formats if n == saved_val), saved_val)
                        if len(data) > 2 and isinstance(data[2], ttk.Combobox):
                            data[2].set(full_str)
                        var.set(str(saved_val))
                    else:
                        var.set(str(saved_val))
        self.update_manual(self.build_command())
        self.status_label.config(text="Ready")

    # ---------- Остальные методы ----------
    def _create_option_widget(self, parent, flag, alias, opt_type, desc):
        frame = ttk.Frame(parent)
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
            combo = ttk.Combobox(frame, textvariable=var, state="readonly", width=35)
            combo['values'] = [f"{n} ({d})" for n, d in self.wiegand_formats]
            combo.pack(side='left', padx=5)
            def on_format_selected(event, v=var):
                sel = v.get()
                if sel:
                    name = sel.split()[0]
                    v.set(name)
                    self.update_manual(self.build_command())
            combo.bind("<<ComboboxSelected>>", on_format_selected)
            type_label = ttk.Label(frame, text="<format>", foreground='gray')
            type_label.pack(side='left', padx=5)
            self.option_widgets[flag] = ('format', var, combo)
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

    def build_command(self):
        if not self.current_command_path:
            return ""
        args = [self.current_command_path]
        for flag, data in self.option_widgets.items():
            kind = data[0]
            var = data[1]
            if kind == 'bool':
                if var.get():
                    args.append(flag)
            else:
                val = var.get().strip()
                if val:
                    if kind == 'format':
                        val = val.split()[0]
                    args.append(flag)
                    args.append(val)
        return ' '.join(args)

    def on_example_double_click(self, event):
        sel = self.examples_tree.selection()
        if not sel:
            return

        for flag, data in self.option_widgets.items():
            kind = data[0]
            var = data[1]
            if kind == 'bool':
                var.set(False)
            else:
                var.set("")
                if kind == 'format' and len(data) > 2 and isinstance(data[2], ttk.Combobox):
                    data[2].set("")

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
                data = self.option_widgets[token]
                kind, var = data[0], data[1]
                if kind == 'bool':
                    var.set(True)
                    i += 1
                else:
                    i += 1
                    if i < len(parts):
                        val = parts[i]
                        if kind == 'format':
                            display_str = next((f"{n} ({d})" for n, d in self.wiegand_formats if n == val), val)
                            if len(data) > 2 and isinstance(data[2], ttk.Combobox):
                                data[2].set(display_str)
                        var.set(val)
                        i += 1
            else:
                i += 1
        self.update_manual(self.build_command())

    def apply_current_options(self):
        if not self.current_command_path:
            return
        current_options = {}
        for flag, data in self.option_widgets.items():
            current_options[flag] = data[1].get()
        self.save_command_options(self.current_command_path, current_options)

    def send_manual_command(self, event=None):
        if self.option_widgets:
            self.apply_current_options()
            cmd = self.cmd_entry.get().strip()
        else:
            cmd = self.cmd_entry.get().strip()
        if cmd:
            self.last_full_command = cmd
            self.save_current_config()
            self.run_manual_command(cmd)

    def _show_error(self, msg):
        if hasattr(self, 'status_label'):
            self.status_label.config(text=msg, foreground='red')

    def update_manual(self, cmd):
        if hasattr(self, 'cmd_entry') and self.cmd_entry.winfo_exists():
            self.cmd_entry.delete(0, tk.END)
            self.cmd_entry.insert(0, cmd)

    def on_tree_select(self, event):
        self.show_manual_input()
        selection = self.tree.selection()
        if selection:
            full_path = selection[0]
            if not self.tree.get_children(selection[0]):
                self.update_manual(full_path)
                self.last_path = full_path
                self.save_current_config()

    def on_tree_double_click(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        full_path = selection[0]
        if self.tree.get_children(selection[0]):
            return
        if not self.connected:
            self.status_queue.put("[WARN] Not connected. Press Connect first.")
            return
        self.load_command_options(full_path)

    def execute_selected_command(self):
        selection = self.tree.selection()
        if not selection:
            return
        full_path = selection[0]
        if self.tree.get_children(selection[0]):
            return
        if not self.connected:
            self.status_queue.put("[WARN] Not connected. Press Connect first.")
            return
        self.load_command_options(full_path)

    def refresh_com_ports(self):
        ports = get_available_com_ports()
        self.com_combo['values'] = ports
        if self.com_port.get() not in ports and ports:
            self.com_port.set(ports[0])
        self.status_queue.put("[INFO] COM port list refreshed.")

    def on_com_port_changed(self, event=None):
        new_port = self.com_port.get()
        self.status_queue.put(f"[INFO] COM port changed to {new_port}.")
        self.save_current_config()
        if self.connected:
            self.status_queue.put("[INFO] Disconnecting and reconnecting with new port...")
            self.disconnect_pty()
            self.connect_pty()

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
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", "Proxmark3 GUI Commander v2\nSingle PTY session."))

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select Proxmark root folder (contains 'client' subfolder)")
        if folder:
            if not os.path.isdir(os.path.join(folder, "client")):
                messagebox.showwarning("Warning", "В выбранной папке нет подпапки 'client'.")
                return
            self.pm3_folder.set(folder)
            self.status_queue.put(f"[INFO] Selected folder: {folder}")
            self.save_current_config()
            if self.connected:
                self.disconnect_pty()
                self.connect_pty()
            else:
                cached = load_tree_cache(folder)
                if cached:
                    self.tree.delete(*self.tree.get_children())
                    self.path_to_iid.clear()
                    self.populate_tree_from_cache(cached)
                    self.status_var.set("Ready (cached tree)")
                    self.restore_last_state()

    def save_current_config(self):
        config = {
            "pm3_folder": self.pm3_folder.get(),
            "com_port": self.com_port.get(),
            "encoding": self.encoding_var.get(),
            "last_path": self.last_path,
            "last_full_command": self.last_full_command,
        }
        save_config(config)

    def on_encoding_changed(self):
        self.status_queue.put(f"[INFO] Encoding changed to {self.encoding_var.get()}. Reconnect to apply.")
        self.save_current_config()
        if self.connected:
            self.disconnect_pty()
            self.connect_pty()

    def clean_text(self, text):
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        text = ansi_escape.sub('', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
        return text

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

    def restore_last_state(self):
        cmd = self.last_full_command.strip() if self.last_full_command else ""
        if cmd:
            self.cmd_entry.delete(0, tk.END)
            self.cmd_entry.insert(0, cmd)
            parts = cmd.split()
            base_path_parts = []
            for part in parts[1:]:
                if part.startswith('-'):
                    break
                base_path_parts.append(part)
            base_path = ' '.join(parts[0:1] + base_path_parts) if base_path_parts else parts[0]
            if base_path in self.path_to_iid:
                iid = self.path_to_iid[base_path]
                self.tree.see(iid)
                self.tree.selection_set(iid)
                self._close_all_except_path(base_path)
        else:
            if self.last_path and self.last_path in self.path_to_iid:
                iid = self.path_to_iid[self.last_path]
                self.tree.see(iid)
                self.tree.selection_set(iid)
                self._close_all_except_path(self.last_path)
                self.cmd_entry.delete(0, tk.END)
                self.cmd_entry.insert(0, self.last_path)

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
        item = self.tree.focus()
        if not item:
            return
        path_parts = []
        current = item
        while current:
            path_parts.insert(0, self.tree.item(current, "text").split(" (")[0])
            current = self.tree.parent(current)
        full_path = " ".join(path_parts)
        self._close_all_except_path(full_path)

    def _close_all_except_path(self, path):
        if not path:
            return
        parts = path.split()
        root_children = self.tree.get_children('')
        first_part = parts[0] if parts else ""
        for child in root_children:
            if self.tree.item(child, "text").split(" (")[0] != first_part:
                self.tree.item(child, open=False)
            else:
                self.tree.item(child, open=True)
        parent = self.path_to_iid.get(first_part, '')
        for part in parts[1:]:
            children = self.tree.get_children(parent)
            found = False
            for child in children:
                if self.tree.item(child, "text").split(" (")[0] == part:
                    self.tree.item(child, open=True)
                    parent = child
                    found = True
                    break
            if not found:
                break
            for child in children:
                if self.tree.item(child, "text").split(" (")[0] != part:
                    self.tree.item(child, open=False)

    def save_command_options(self, command_path, options):
        self.options_cache[command_path] = options
        save_options_cache(self.options_cache)

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)

    def log_message(self, text):
        if hasattr(self, 'log_text') and self.log_text.winfo_exists():
            self.log_text.insert(tk.END, text)
            self.log_text.see(tk.END)

    def status_message(self, text):
        if hasattr(self, 'status_text') and self.status_text.winfo_exists():
            self.status_text.insert('1.0', text + '\n')
            self.status_text.see('1.0')

    def on_closing(self):
        self._closing.set()
        self.disconnect_pty()
        self.last_full_command = self.cmd_entry.get().strip()
        self.save_current_config()
        self.destroy()

if __name__ == '__main__':
    app = ProxmarkApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
