#!/usr/bin/env python3
"""
Archive.org Downloader GUI
A production-grade graphical interface for downloading items from Archive.org.
Features a Producer-Consumer thread architecture, dynamic global bandwidth 
rate limiting (Token Bucket), Exponential Moving Average (EMA) speed tracking,
accurate ETA calculations, deep directory structure parsing, and session persistence.

Version: 1.3.0
License: MIT
"""

import os
import json
import uuid
import time
import queue
import hashlib
import logging
import requests
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from urllib.parse import urlparse, parse_qs

__version__ = "1.3.0"

ARCHIVE_API_BASE = "https://archive.org"
CONFIG_FILE = "config.json"
LOG_FILE = "archive_downloader.log"
CHUNK_SIZE = 8192
MAX_PATH_LENGTH = 150


def format_size(bytes_val):
    """
    Utility function to format raw byte counts into human-readable strings.
    Utilizes binary prefixes (1024) which is the standard OS convention.
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"


def format_time(seconds):
    """
    Utility function to format an ETA in seconds into a standard mm:ss or hh:mm:ss format.
    """
    if seconds == float('inf') or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class ConfigManager:
    """
    Handles persistence of user settings and session state across app restarts.
    Ensures that corrupted or missing configuration files do not crash the application.
    """
    def __init__(self):
        self.config_file = CONFIG_FILE
        self.default_config = {
            "download_folder": os.getcwd(),
            "allowed_extensions": "",
            "parallel_downloads": 3,
            "bandwidth_value": "0",
            "bandwidth_unit": "MB/s",
            "download_description": True,
            "last_urls": ""
        }

    def load(self):
        """Loads configuration from disk, falling back to defaults if necessary."""
        if not os.path.exists(self.config_file):
            return self.default_config.copy()
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config = self.default_config.copy()
                config.update(loaded)
                return config
        except Exception:
            return self.default_config.copy()

    def save(self, config_dict):
        """Safely writes the current configuration state to disk."""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=4)
        except Exception:
            pass


class RateLimiter:
    """
    Thread-safe global bandwidth throttler implementing the Token Bucket algorithm.
    Allows multiple worker threads to safely consume bandwidth tokens, ensuring 
    the overall network speed does not exceed the user-defined limit.
    """
    def __init__(self, max_bytes_per_second):
        self.rate = max_bytes_per_second
        self.allowance = max_bytes_per_second
        self.last_check = time.monotonic()
        self.lock = threading.Lock()

    def set_rate(self, new_rate):
        """Dynamically updates the bandwidth limit without stopping active downloads."""
        with self.lock:
            self.rate = new_rate
            self.allowance = new_rate
            self.last_check = time.monotonic()

    def consume(self, amount):
        """
        Consumes bytes from the token bucket. If the bucket is empty, the calling 
        thread is put to sleep until enough tokens are replenished.
        """
        if self.rate <= 0:
            return  # Unlimited bandwidth mode
        
        sleep_time = 0
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_check
            self.last_check = now
            
            self.allowance += elapsed * self.rate
            if self.allowance > self.rate:
                self.allowance = self.rate

            if amount > self.allowance:
                sleep_time = (amount - self.allowance) / self.rate
                self.allowance -= amount 
            else:
                self.allowance -= amount
                
        if sleep_time > 0:
            time.sleep(sleep_time)


class DownloadTask:
    """
    Data Transfer Object (DTO) representing a single file download task.
    Maintains independent cancellation states and runtime metrics.
    """
    def __init__(self, url, filepath, expected_size, item_identifier, item_title, md5=None, sha1=None):
        self.task_id = str(uuid.uuid4())
        self.url = url
        self.filepath = filepath
        self.expected_size = expected_size
        self.item_identifier = item_identifier
        self.item_title = item_title
        self.md5 = md5
        self.sha1 = sha1
        
        # Thread-safe event flag for granular cancellation
        self.cancel_event = threading.Event()
        
        # Runtime metrics for calculating speed and ETA
        self.start_time = 0
        self.bytes_since_last_check = 0
        self.current_speed_ema = 0
        self.current_bytes = 0

    def reset(self):
        """Clears cancellation flags and resets dynamic tracking metrics for a fresh start."""
        self.cancel_event.clear()
        self.current_speed_ema = 0
        self.bytes_since_last_check = 0
        self.current_bytes = 0


class ScrollableFrame(ttk.Frame):
    """
    A robust scrollable container using the tk.Canvas widget.
    Necessary for dynamic UI elements like individual download progress bars.
    """
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, height=200)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_frame = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_frame, width=e.width))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")


class ArchiveEngine:
    """
    The core backend engine implementing the Producer-Consumer pattern.
    Manages a thread pool of background workers, a task queue, and HTTP network requests,
    keeping the main GUI thread completely unblocked.
    """
    def __init__(self, message_queue, max_workers, bandwidth_limit=0):
        self.queue = message_queue
        self.task_queue = queue.Queue()
        self.active_tasks = {}
        
        # Lifecycle flags
        self.is_shutdown = False
        self.is_discovery_aborted = False
        
        self._session = requests.Session()
        self.lock = threading.Lock()
        
        # Global networking metrics
        self.rate_limiter = RateLimiter(bandwidth_limit)
        self.global_bytes_counter = 0
        self.global_speed_ema = 0
        
        # Initialize the persistent worker thread pool
        self.workers = []
        for _ in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self.workers.append(t)
            
        # Start a daemon thread specifically for calculating global metrics
        threading.Thread(target=self._monitor_global_speed, daemon=True).start()

    def set_bandwidth_limit(self, bytes_per_sec):
        self.rate_limiter.set_rate(bytes_per_sec)

    def _monitor_global_speed(self):
        """Calculates global Exponential Moving Average (EMA) speed and ETA deterministically."""
        while not self.is_shutdown:
            time.sleep(1.0)
            with self.lock:
                recent_bytes = self.global_bytes_counter
                self.global_bytes_counter = 0
                
                # Deterministic calculation based on currently active task sums
                global_total = sum(t.expected_size for t in self.active_tasks.values())
                global_completed = sum(t.current_bytes for t in self.active_tasks.values())
                
            # EMA formula: 70% historical data, 30% recent data (prevents extreme jitter)
            self.global_speed_ema = (self.global_speed_ema * 0.7) + (recent_bytes * 0.3)
            
            if self.active_tasks:
                remaining_bytes = max(0, global_total - global_completed)
                global_eta = remaining_bytes / self.global_speed_ema if self.global_speed_ema > 0 else float('inf')
                
                self.queue.put({
                    'type': 'global_speed_update',
                    'speed_bps': self.global_speed_ema,
                    'eta_seconds': global_eta
                })

    def submit_task(self, task):
        """Pushes a new DownloadTask to the end of the consumer queue."""
        with self.lock:
            self.active_tasks[task.task_id] = task
        self.task_queue.put(task)

    def cancel_task(self, task_id):
        """Flags a specific task for immediate cancellation."""
        with self.lock:
            if task_id in self.active_tasks:
                self.active_tasks[task_id].cancel_event.set()

    def cancel_all(self):
        """Safely drains the pending task queue and cancels currently active network streams."""
        self.is_discovery_aborted = True
        
        # Empty the queue immediately without processing
        while True:
            try:
                task = self.task_queue.get_nowait()
                if task is not None:
                    task.cancel_event.set()
                    self.send_task_status(task.task_id, 'cancelled')
                self.task_queue.task_done()
            except queue.Empty:
                break
                
        # Send cancellation signals to all running threads
        with self.lock:
            for task in self.active_tasks.values():
                task.cancel_event.set()

    def restart_task(self, task_id):
        """Clears error/cancellation flags and pushes the task back to the queue."""
        with self.lock:
            if task_id in self.active_tasks:
                task = self.active_tasks[task_id]
                task.reset()
                self.task_queue.put(task)
                
                filename = os.path.basename(task.filepath)
                self.send_log("INFO", f"[{task.item_identifier}] Restarted download: {filename}")

    def shutdown(self):
        """Dismantles the thread pool. Strictly used during application exit."""
        self.is_shutdown = True
        self.is_discovery_aborted = True
        for _ in self.workers:
            self.task_queue.put(None)

    def send_log(self, level, message):
        """Pushes a log message to the UI event queue."""
        self.queue.put({'type': 'log', 'level': level, 'message': message})

    def send_task_status(self, task_id, status, **kwargs):
        """Pushes a task state update to the UI event queue."""
        payload = {'type': 'task_status', 'task_id': task_id, 'status': status}
        payload.update(kwargs)
        self.queue.put(payload)

    def _worker_loop(self):
        """Daemon worker loop constantly polling the queue for download tasks."""
        while True:
            task = self.task_queue.get()
            
            # Poison pill condition for clean shutdown
            if task is None:
                self.task_queue.task_done()
                break
                
            if self.is_shutdown:
                self.task_queue.task_done()
                continue
                
            try:
                self._execute_download(task)
            except Exception as e:
                self.send_log("ERROR", f"Worker fault on {task.filepath}: {e}")
                self.send_task_status(task.task_id, 'error', error_msg=str(e))
            finally:
                self.task_queue.task_done()

    def parse_url(self, url):
        """
        Parses user input URLs robustly, eliminating stray whitespace and 
        properly aggregating multiple Elasticsearch query arrays (`and[]`).
        """
        try:
            # Absolute whitespace removal to prevent hidden formatting errors
            clean_url = "".join(str(url).split())
            if not clean_url:
                return None
                
            parsed = urlparse(clean_url)
            if '/search' in parsed.path:
                qs = parse_qs(parsed.query)
                query_parts = []
                
                if 'query' in qs:
                    query_parts.extend(qs['query'])
                if 'and[]' in qs:
                    query_parts.extend(qs['and[]'])
                    
                if not query_parts:
                    return None
                    
                # Reconstruct the precise Elasticsearch query expected by the API
                full_query = " AND ".join(query_parts)
                return {'type': 'search', 'query': full_query}
                
            elif '/details/' in parsed.path:
                path_parts = parsed.path.split('/details/')[-1].split('/')
                identifier = path_parts[0] if path_parts else None
                if identifier:
                    return {'type': 'details', 'identifier': identifier}
        except Exception: 
            pass
        return None

    def search_items(self, query):
        """Uses the advancedsearch API to resolve a query into a list of item identifiers."""
        api_url = f"{ARCHIVE_API_BASE}/advancedsearch.php"
        params = {'q': query, 'fl[]': ['identifier'], 'rows': 10000, 'page': 1, 'output': 'json'}
        try:
            response = self._session.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            return [item['identifier'] for item in response.json().get('response', {}).get('docs', [])]
        except Exception as e:
            self.send_log("ERROR", f"Search failed: {e}")
            return []

    def sanitize_filename(self, filename):
        """Standard filename sanitization for root folders."""
        filename = str(filename)
        for char in '<>:"|?*/\\': 
            filename = filename.replace(char, '_')
        return filename.strip('. ')[:MAX_PATH_LENGTH] or "unnamed"

    def sanitize_relative_path(self, path_string):
        """
        Safely parses relative paths returned by the API.
        Crucial for mitigating Directory Traversal vulnerabilities (e.g., '../../file.exe')
        and managing OS-level illegal characters per sub-directory.
        """
        normalized = str(path_string).replace('\\', '/')
        components = normalized.split('/')
        safe_components = []
        
        for component in components:
            if component in ('', '.', '..'):
                continue
            
            safe_comp = component
            for char in '<>:"|?*': 
                safe_comp = safe_comp.replace(char, '_')
            
            safe_comp = safe_comp.strip('. ')
            
            if safe_comp:
                safe_components.append(safe_comp[:100])
                
        return os.path.join(*safe_components) if safe_components else "unnamed_file"

    def verify_integrity(self, filepath, task):
        """Verifies file against Archive.org MD5/SHA1 metadata to prevent data corruption."""
        if not os.path.exists(filepath): 
            return False
            
        target_hash = task.sha1 or task.md5
        if not target_hash: 
            return True
            
        hasher = hashlib.sha1() if task.sha1 else hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    if self.is_shutdown or task.cancel_event.is_set(): 
                        return False
                    hasher.update(chunk)
            return hasher.hexdigest().lower() == target_hash.lower()
        except OSError: 
            return False

    def _execute_download(self, task):
        """
        The core robust download function. Supports HTTP Range Requests (resume capability),
        integrity verification, and real-time cancellation.
        """
        filename = os.path.basename(task.filepath)
        if self.is_shutdown or task.cancel_event.is_set():
            self.send_task_status(task.task_id, 'cancelled')
            return

        self.send_task_status(task.task_id, 'started', filename=filename, total=task.expected_size)

        headers = {}
        resume_size = 0
        mode = 'wb'

        # Pre-download check: Validate existing files to skip or resume
        if os.path.exists(task.filepath):
            actual_size = os.path.getsize(task.filepath)
            if actual_size == task.expected_size:
                if self.verify_integrity(task.filepath, task):
                    task.current_bytes = actual_size
                    self.send_log("SUCCESS", f"[{task.item_identifier}] Verified existing: {filename}")
                    self.send_task_status(task.task_id, 'progress', current=actual_size, speed_bps=0, eta_seconds=0)
                    self.send_task_status(task.task_id, 'done')
                    return
                else: 
                    os.remove(task.filepath)
            elif actual_size < task.expected_size:
                headers['Range'] = f"bytes={actual_size}-"
                resume_size = actual_size
                mode = 'ab'
            else: 
                os.remove(task.filepath)

        try:
            # Tupled timeout (connect, read) prevents deep socket hangs on unstable networks
            with self._session.get(task.url, headers=headers, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                
                # Verify if the server actually accepted the Range request
                if response.status_code != 206 and resume_size > 0:
                    mode = 'wb'
                    resume_size = 0
                
                downloaded_bytes = resume_size
                task.current_bytes = downloaded_bytes
                task.start_time = time.monotonic()
                last_ui_update = task.start_time

                with open(task.filepath, mode) as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        # Intercept cancellation commands instantly between IO chunks
                        if self.is_shutdown or task.cancel_event.is_set():
                            self.send_log("WARNING", f"[{task.item_identifier}] Cancelled: {filename}")
                            self.send_task_status(task.task_id, 'cancelled')
                            return
                            
                        if chunk:
                            # Apply the central bandwidth limitation logic
                            self.rate_limiter.consume(len(chunk))
                            
                            f.write(chunk)
                            chunk_len = len(chunk)
                            downloaded_bytes += chunk_len
                            task.current_bytes = downloaded_bytes
                            task.bytes_since_last_check += chunk_len
                            
                            with self.lock:
                                self.global_bytes_counter += chunk_len
                            
                            # Periodically update UI (approx every 0.5 seconds) to avoid CPU bottlenecking
                            now = time.monotonic()
                            if now - last_ui_update >= 0.5:
                                elapsed = now - last_ui_update
                                instant_speed = task.bytes_since_last_check / elapsed
                                task.current_speed_ema = (task.current_speed_ema * 0.5) + (instant_speed * 0.5)
                                
                                remaining = max(0, task.expected_size - downloaded_bytes)
                                eta = remaining / task.current_speed_ema if task.current_speed_ema > 0 else float('inf')
                                
                                self.send_task_status(task.task_id, 'progress', 
                                                      current=downloaded_bytes, 
                                                      speed_bps=task.current_speed_ema,
                                                      eta_seconds=eta)
                                
                                task.bytes_since_last_check = 0
                                last_ui_update = now

            # Post-download integrity verification
            if os.path.getsize(task.filepath) != task.expected_size:
                raise ValueError("Size mismatch after download.")
            if not self.verify_integrity(task.filepath, task):
                os.remove(task.filepath)
                raise ValueError("Integrity check failed.")

            self.send_log("SUCCESS", f"[{task.item_identifier}] Completed: {filename}")
            self.send_task_status(task.task_id, 'done')

        except Exception as e:
            if not self.is_shutdown and not task.cancel_event.is_set():
                self.send_log("ERROR", f"[{task.item_identifier}] Failed {filename}: {e}")
                self.send_task_status(task.task_id, 'error', error_msg=str(e))


class DownloaderApp(tk.Tk):
    """
    Main Tkinter GUI Class.
    Follows strict single-threaded UI rules by polling the engine's message queue 
    for updates, avoiding race conditions and application freezes.
    """
    def __init__(self):
        super().__init__()
        self.title("Archive.org Open-Source Downloader")
        self.geometry("900x800")
        self.minsize(800, 750)
        
        self.config_manager = ConfigManager()
        self.app_config = self.config_manager.load()
        
        self._setup_logging()
        
        self.message_queue = queue.Queue()
        self.engine = None
        self.active_tasks_ui = {}
        
        # Ensures statistical parity between engine states and UI rendering
        self.stats = {'total': 0, 'completed': 0, 'success': 0, 'error': 0, 'cancelled': 0}
        
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Start the non-blocking polling mechanism
        self.after(100, self._process_queue)

    def _setup_logging(self):
        """Initializes a thread-safe, external standard file logger."""
        self.file_logger = logging.getLogger("ArchiveDownloaderFileLog")
        self.file_logger.setLevel(logging.INFO)
        
        if not self.file_logger.handlers:
            fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
            formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
            fh.setFormatter(formatter)
            self.file_logger.addHandler(fh)

    def _build_ui(self):
        """Constructs and packs all GUI elements and initializes them with loaded config."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Archive.org URLs (One per line):").pack(anchor=tk.W)
        self.text_urls = tk.Text(main_frame, height=4, width=50)
        self.text_urls.pack(fill=tk.X, pady=(0, 10))
        
        # Restore previously unfinished session URLs
        self.text_urls.insert("1.0", self.app_config.get("last_urls", ""))

        config_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        config_frame.pack(fill=tk.X, pady=(0, 10))

        # Row 0: Folder Selection
        ttk.Label(config_frame, text="Download Folder:").grid(row=0, column=0, sticky=tk.W)
        self.var_folder = tk.StringVar(value=self.app_config["download_folder"])
        ttk.Entry(config_frame, textvariable=self.var_folder, state='readonly').grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=5)
        ttk.Button(config_frame, text="Browse...", command=self._browse_folder).grid(row=0, column=3)

        # Row 1: Extensions Filtering
        ttk.Label(config_frame, text="Allowed Extensions:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.var_exts = tk.StringVar(value=self.app_config["allowed_extensions"])
        ttk.Entry(config_frame, textvariable=self.var_exts).grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=5)
        ttk.Label(config_frame, text="(e.g. .mp3, .pdf | Empty = all)", foreground="gray").grid(row=1, column=3, sticky=tk.W)

        # Row 2: Concurrency & Dynamic Rate Limiting
        ttk.Label(config_frame, text="Parallel Downloads:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_workers = tk.IntVar(value=self.app_config["parallel_downloads"])
        ttk.Spinbox(config_frame, from_=1, to=20, textvariable=self.var_workers, width=5).grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="Speed Limit (0 = Max):").grid(row=2, column=2, sticky=tk.E, padx=5, pady=5)
        
        bw_frame = ttk.Frame(config_frame)
        bw_frame.grid(row=2, column=3, sticky=tk.W, pady=5)
        
        self.var_bw_val = tk.StringVar(value=self.app_config["bandwidth_value"])
        ttk.Entry(bw_frame, textvariable=self.var_bw_val, width=8).pack(side=tk.LEFT)
        
        self.var_bw_unit = tk.StringVar(value=self.app_config["bandwidth_unit"])
        cb_unit = ttk.Combobox(bw_frame, textvariable=self.var_bw_unit, values=["KB/s", "MB/s"], state="readonly", width=6)
        cb_unit.pack(side=tk.LEFT, padx=(5, 0))
        
        self.var_bw_val.trace_add("write", self._on_bandwidth_change)
        self.var_bw_unit.trace_add("write", self._on_bandwidth_change)

        # Row 3: HTML Description Checkbox
        self.var_download_desc = tk.BooleanVar(value=self.app_config["download_description"])
        cb_desc = ttk.Checkbutton(config_frame, text="Download Description (HTML)", variable=self.var_download_desc)
        cb_desc.grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=5)
        
        config_frame.columnconfigure(1, weight=1)

        # Action Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.btn_start = ttk.Button(btn_frame, text="Start / Queue Downloads", command=self._start_process)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = ttk.Button(btn_frame, text="Cancel All & Stop", command=self._stop_process, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        # Overall Progress & Speed Indicators
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill=tk.X)
        self.lbl_overall = ttk.Label(info_frame, text="Overall Progress: 0/0 files")
        self.lbl_overall.pack(side=tk.LEFT)
        self.lbl_global_speed = ttk.Label(info_frame, text="Total Speed: 0 B/s | ETA: --:--", foreground="#0066cc")
        self.lbl_global_speed.pack(side=tk.RIGHT)
        
        self.prog_overall = ttk.Progressbar(main_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.prog_overall.pack(fill=tk.X, pady=(5, 10))

        # Active Tasks Area
        ttk.Label(main_frame, text="Active Downloads:").pack(anchor=tk.W)
        self.scroll_frame = ScrollableFrame(main_frame)
        self.scroll_frame.pack(fill=tk.X, pady=(0, 10))

        # Logs Console
        ttk.Label(main_frame, text="Activity Log:").pack(anchor=tk.W)
        self.log_area = scrolledtext.ScrolledText(main_frame, height=8, state=tk.DISABLED, bg="#1e1e1e", fg="#cccccc", font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)
        self.log_area.tag_config("INFO", foreground="#4fc1ff")
        self.log_area.tag_config("SUCCESS", foreground="#4ebd5e")
        self.log_area.tag_config("WARNING", foreground="#d7ba7d")
        self.log_area.tag_config("ERROR", foreground="#f44747")

    def _browse_folder(self):
        folder = filedialog.askdirectory(initialdir=self.var_folder.get())
        if folder: 
            self.var_folder.set(folder)

    def _get_current_bandwidth_limit(self):
        """Safely parses textual bandwidth input to bytes per second."""
        try:
            val_str = self.var_bw_val.get().strip().replace(',', '.')
            if not val_str: 
                return 0
            val = float(val_str)
            if val <= 0: 
                return 0
            
            unit = self.var_bw_unit.get()
            multiplier = 1024 * 1024 if unit == "MB/s" else 1024
            return int(val * multiplier)
        except ValueError:
            return 0

    def _on_bandwidth_change(self, *args):
        if self.engine:
            limit = self._get_current_bandwidth_limit()
            self.engine.set_bandwidth_limit(limit)

    def _log_to_gui(self, level, message):
        """Thread-safe logging utility routing to both UI and disk."""
        # File logging
        if level == "ERROR":
            self.file_logger.error(message)
        elif level == "WARNING":
            self.file_logger.warning(message)
        else:
            # Map logical SUCCESS to standard INFO level in files
            prefix = "[SUCCESS] " if level == "SUCCESS" else ""
            self.file_logger.info(f"{prefix}{message}")

        # GUI logging
        self.log_area.config(state=tk.NORMAL)
        self.log_area.insert(tk.END, f"[{level}] {message}\n", level)
        self.log_area.see(tk.END)
        self.log_area.config(state=tk.DISABLED)

    def _print_final_summary(self):
        """Generates a highly visible summary block in the log upon full completion."""
        summary = (
            f"\n{'='*55}\n"
            f"✅ BATCH EXECUTION FINISHED\n"
            f"{'='*55}\n"
            f"Total Processed: {self.stats['total']}\n"
            f"Success:         {self.stats['success']}\n"
            f"Errors:          {self.stats['error']}\n"
            f"Cancelled:       {self.stats['cancelled']}\n"
            f"{'='*55}"
        )
        self._log_to_gui("SUCCESS", summary)
        self.btn_stop.config(state=tk.DISABLED)

    def _create_task_ui(self, task_id, filename, total_size):
        """Generates dynamic row elements for actively downloading files."""
        if task_id in self.active_tasks_ui: 
            return

        row_frame = ttk.Frame(self.scroll_frame.scrollable_frame)
        row_frame.pack(fill=tk.X, pady=2, padx=5)
        
        lbl = ttk.Label(row_frame, text=filename, width=30, anchor="w")
        lbl.pack(side=tk.LEFT, padx=(0, 10))
        
        prog = ttk.Progressbar(row_frame, orient=tk.HORIZONTAL, mode='determinate', maximum=total_size)
        prog.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        pct_lbl = ttk.Label(row_frame, text="0%", width=6, anchor="e")
        pct_lbl.pack(side=tk.LEFT, padx=(5, 5))
        
        spd_lbl = ttk.Label(row_frame, text="0 B/s | --:--", width=18, anchor="e", foreground="gray")
        spd_lbl.pack(side=tk.LEFT, padx=(0, 10))

        btn_action = ttk.Button(row_frame, text="Cancel", width=8, command=lambda: self._cancel_single_task(task_id))
        btn_action.pack(side=tk.LEFT)

        self.active_tasks_ui[task_id] = {
            'frame': row_frame,
            'prog': prog,
            'pct_lbl': pct_lbl,
            'spd_lbl': spd_lbl,
            'btn': btn_action,
            'last_status': 'started',
            'total': total_size
        }

    def _update_task_ui(self, task_id, current_bytes, speed_bps=0, eta_seconds=-1):
        """Updates values for dynamic UI rows."""
        if task_id in self.active_tasks_ui:
            ui = self.active_tasks_ui[task_id]
            ui['prog']['value'] = current_bytes
            if ui['total'] > 0:
                percent = int((current_bytes / ui['total']) * 100)
                ui['pct_lbl'].config(text=f"{percent}%", foreground="")
            if speed_bps >= 0 and eta_seconds >= 0:
                ui['spd_lbl'].config(text=f"{format_size(speed_bps)}/s | {format_time(eta_seconds)}")

    def _set_task_ui_state(self, task_id, lbl_text, btn_text, btn_cmd, color=""):
        """Modifies button configuration for specific states (e.g. Cancel -> Restart)."""
        if task_id in self.active_tasks_ui:
            ui = self.active_tasks_ui[task_id]
            ui['pct_lbl'].config(text=lbl_text, foreground=color)
            ui['spd_lbl'].config(text="")
            ui['btn'].config(text=btn_text, command=lambda: btn_cmd(task_id))

    def _remove_task_ui(self, task_id):
        """Cleans up UI elements to prevent memory leaks after completion."""
        if task_id in self.active_tasks_ui:
            self.active_tasks_ui[task_id]['frame'].destroy()
            del self.active_tasks_ui[task_id]

    def _cancel_single_task(self, task_id):
        if self.engine: 
            self.engine.cancel_task(task_id)

    def _restart_single_task(self, task_id):
        """Safely decrements failure statistics and returns task to the queue."""
        if self.engine and task_id in self.active_tasks_ui:
            last_status = self.active_tasks_ui[task_id].get('last_status')
            
            # Revert stats to correctly reflect the new active state
            if last_status == 'cancelled': 
                self.stats['cancelled'] = max(0, self.stats['cancelled'] - 1)
            elif last_status == 'error': 
                self.stats['error'] = max(0, self.stats['error'] - 1)
                
            self.stats['completed'] = max(0, self.stats['completed'] - 1)
            self._update_overall_progress()
            
            self._set_task_ui_state(task_id, "0%", "Cancel", self._cancel_single_task)
            self.active_tasks_ui[task_id]['prog']['value'] = 0
            self.active_tasks_ui[task_id]['last_status'] = 'started'
            self.engine.restart_task(task_id)

    def _update_overall_progress(self):
        """Synchronizes the visual progress bar with the statistical model."""
        self.lbl_overall.config(text=f"Overall Progress: {self.stats['completed']}/{self.stats['total']} files")
        self.prog_overall['maximum'] = self.stats['total']
        self.prog_overall['value'] = self.stats['completed']

    def _process_queue(self):
        """
        The polling mechanism. Reads messages dispatched by background workers 
        and updates the Tkinter UI. It executes on the main thread safely.
        """
        try:
            while True:
                msg = self.message_queue.get_nowait()
                msg_type = msg.get('type')

                if msg_type == 'log':
                    self._log_to_gui(msg['level'], msg['message'])
                
                elif msg_type == 'tasks_added':
                    # Incremental total update directly from the discovery thread
                    self.stats['total'] += msg['count']
                    self._update_overall_progress()

                elif msg_type == 'task_status':
                    status = msg['status']
                    task_id = msg['task_id']
                    
                    if status == 'started':
                        self._create_task_ui(task_id, msg['filename'], msg['total'])
                    elif status == 'progress':
                        self._update_task_ui(task_id, msg['current'], msg.get('speed_bps', -1), msg.get('eta_seconds', -1))
                    elif status in ('done', 'cancelled', 'error'):
                        # Aggregate terminal states to logically advance the progress bar
                        self.stats['completed'] += 1
                        
                        if status == 'done': self.stats['success'] += 1
                        elif status == 'error': self.stats['error'] += 1
                        elif status == 'cancelled': self.stats['cancelled'] += 1
                        
                        self._update_overall_progress()
                        
                        if status == 'done':
                            self._remove_task_ui(task_id)
                        else:
                            # Keep failed/cancelled items visible for restart actions
                            if task_id in self.active_tasks_ui:
                                self.active_tasks_ui[task_id]['last_status'] = status
                                if status == 'cancelled':
                                    self._set_task_ui_state(task_id, "Cancelled", "Restart", self._restart_single_task, "orange")
                                elif status == 'error':
                                    self._set_task_ui_state(task_id, "Error", "Restart", self._restart_single_task, "red")

                        # Deterministic Check: Triggers summary exactly once when all queued items finish
                        if self.stats['total'] > 0 and self.stats['completed'] == self.stats['total']:
                            self._print_final_summary()
                
                elif msg_type == 'global_speed_update':
                    if self.stats['completed'] < self.stats['total']:
                        spd_str = format_size(msg['speed_bps'])
                        eta_str = format_time(msg['eta_seconds'])
                        self.lbl_global_speed.config(text=f"Total Speed: {spd_str}/s | ETA: {eta_str}")
                    else:
                        self.lbl_global_speed.config(text="Total Speed: 0 B/s | ETA: --:--")
                
                elif msg_type == 'process_finished':
                    # Allows users to continuously feed new URLs into the engine
                    self.btn_start.config(state=tk.NORMAL)

                self.message_queue.task_done()
        except queue.Empty:
            pass
        finally:
            self.after(50, self._process_queue)

    def _start_process(self):
        urls = [u.strip() for u in self.text_urls.get("1.0", tk.END).splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("Input Error", "Please enter at least one URL.")
            return

        base_path = self.var_folder.get()
        if not os.path.exists(base_path):
            try: 
                os.makedirs(base_path)
            except OSError as e:
                messagebox.showerror("Path Error", f"Cannot access output folder:\n{e}")
                return

        exts_raw = self.var_exts.get()
        allowed_exts = [ext if ext.startswith('.') else f".{ext}" for ext in (exts_raw.split(',') if exts_raw else []) if ext.strip()]

        # Clean slate logic: if system is completely idle from a previous run, reset UI
        if self.stats['total'] > 0 and self.stats['completed'] == self.stats['total']:
            self.stats = {'total': 0, 'completed': 0, 'success': 0, 'error': 0, 'cancelled': 0}
            for widget in self.scroll_frame.scrollable_frame.winfo_children():
                widget.destroy()
            self.active_tasks_ui.clear()
            self._update_overall_progress()
            self._log_to_gui("INFO", "--- New Download Session ---")

        # Thread-safe extraction of UI boolean flag
        download_desc_flag = self.var_download_desc.get()

        if self.engine is None:
            try:
                workers = self.var_workers.get()
            except tk.TclError:
                workers = 3 # Fallback if user left it empty or typed a char
                self.var_workers.set(3)
            bw_limit = self._get_current_bandwidth_limit()
            self.engine = ArchiveEngine(self.message_queue, workers, bw_limit)
        
        self.engine.is_discovery_aborted = False
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_start.config(state=tk.DISABLED)
        
        # Offload URL discovery to a background thread to prevent GUI freezing
        threading.Thread(
            target=self._discovery_flow, 
            args=(urls, base_path, allowed_exts, download_desc_flag), 
            daemon=True
        ).start()

    def _discovery_flow(self, urls, base_path, allowed_exts, download_desc_flag):
        """Background process that queries Archive.org API to construct task manifests."""
        try:
            self.engine.send_log("INFO", "Initializing discovery sequence...")
            all_identifiers = []

            for url in urls:
                if self.engine.is_discovery_aborted: 
                    return
                parsed = self.engine.parse_url(url)
                if not parsed: 
                    self.engine.send_log("WARNING", f"Could not parse URL: {url}")
                    continue
                
                if parsed['type'] == 'search':
                    all_identifiers.extend(self.engine.search_items(parsed['query']))
                elif parsed['type'] == 'details':
                    all_identifiers.append(parsed['identifier'])

            all_identifiers = list(dict.fromkeys(all_identifiers))
            if not all_identifiers:
                self.engine.send_log("WARNING", "No valid items found from provided URLs.")
                return

            for identifier in all_identifiers:
                if self.engine.is_discovery_aborted: 
                    return
                self.engine.send_log("INFO", f"Fetching metadata for '{identifier}'...")
                
                api_url = f"{ARCHIVE_API_BASE}/metadata/{identifier}"
                try: 
                    response = self.engine._session.get(api_url, timeout=30)
                    response.raise_for_status()
                    metadata = response.json()
                except Exception as api_err: 
                    self.engine.send_log("ERROR", f"[{identifier}] API connection failed: {api_err}")
                    continue

                # Robust JSON traversal avoiding NoneType exceptions
                meta_dict = metadata.get('metadata') or {}
                title = meta_dict.get('title', identifier)
                title = title[0] if isinstance(title, list) else str(title)
                item_dir = os.path.join(base_path, self.engine.sanitize_filename(title))
                
                try:
                    os.makedirs(item_dir, exist_ok=True)
                except OSError as os_err:
                    self.engine.send_log("ERROR", f"[{identifier}] Failed to create directory '{item_dir}': {os_err}")
                    continue
                
                # Fast, zero-overhead HTML Description generation from memory
                if download_desc_flag:
                    description = meta_dict.get('description')
                    if description:
                        desc_text = description[0] if isinstance(description, list) else str(description)
                        desc_file_path = os.path.join(item_dir, "description.html")
                        try:
                            with open(desc_file_path, "w", encoding="utf-8") as html_file:
                                html_file.write(f"<!DOCTYPE html>\n<html>\n<head>\n<meta charset='utf-8'>\n<title>{title}</title>\n</head>\n<body>\n{desc_text}\n</body>\n</html>")
                            self.engine.send_log("SUCCESS", f"[{identifier}] Saved description to description.html")
                        except OSError as os_err:
                            self.engine.send_log("ERROR", f"[{identifier}] Failed to save description.html: {os_err}")

                files_list = metadata.get('files') or []
                item_tasks_added = 0
                
                for file_info in files_list:
                    if not isinstance(file_info, dict):
                        continue
                        
                    filename_raw = file_info.get('name')
                    if not filename_raw or str(filename_raw).endswith(('_meta.xml', '_files.xml')): 
                        continue
                        
                    filename_raw = str(filename_raw)
                    if allowed_exts and not any(filename_raw.lower().endswith(ext) for ext in allowed_exts): 
                        continue

                    safe_rel_path = self.engine.sanitize_relative_path(filename_raw)
                    full_filepath = os.path.join(item_dir, safe_rel_path)
                    
                    try:
                        # Ensures deep directory structures are created before queueing
                        os.makedirs(os.path.dirname(full_filepath), exist_ok=True)
                    except OSError as os_err:
                        self.engine.send_log("ERROR", f"[{identifier}] Failed to create sub-directory: {os_err}")
                        continue

                    task = DownloadTask(
                        url=f"{ARCHIVE_API_BASE}/download/{identifier}/{filename_raw}",
                        filepath=full_filepath,
                        expected_size=int(file_info.get('size', 0)),
                        item_identifier=identifier,
                        item_title=title,
                        md5=file_info.get('md5'),
                        sha1=file_info.get('sha1')
                    )
                    self.engine.submit_task(task)
                    item_tasks_added += 1

                # Inform the UI to increment the maximum bar progressively
                if item_tasks_added > 0:
                    self.message_queue.put({'type': 'tasks_added', 'count': item_tasks_added})
            
        except Exception as critical_err:
            # Prevents silent background thread crashes (Anti-pattern mitigation)
            self.engine.send_log("ERROR", f"Critical failure in discovery thread: {critical_err}")
        finally:
            # Guaranteed to release UI locks regardless of errors
            self.message_queue.put({'type': 'process_finished'})

    def _stop_process(self):
        """Triggers global cancellation without dismantling the engine."""
        if self.engine:
            self._log_to_gui("WARNING", "Global cancellation requested. Cancelling active tasks...")
            self.engine.cancel_all()
            self.btn_stop.config(state=tk.DISABLED)
            self.btn_start.config(state=tk.NORMAL)
            self.lbl_global_speed.config(text="Total Speed: 0 B/s | ETA: --:--")

    def _on_close(self):
        """Handles application shutdown gracefully and saves session configurations."""
        try:
            current_urls = self.text_urls.get("1.0", tk.END).strip()
            
            # Logic: If tasks were queued and ALL of them successfully finished, clear URLs.
            if self.stats['total'] > 0 and self.stats['success'] == self.stats['total']:
                urls_to_save = ""
            else:
                # If interrupted, cancelled, or never started, retain current URLs.
                urls_to_save = current_urls
                
            self.app_config.update({
                "download_folder": self.var_folder.get(),
                "allowed_extensions": self.var_exts.get(),
                "parallel_downloads": self.var_workers.get(),
                "bandwidth_value": self.var_bw_val.get(),
                "bandwidth_unit": self.var_bw_unit.get(),
                "download_description": self.var_download_desc.get(),
                "last_urls": urls_to_save
            })
            self.config_manager.save(self.app_config)
        except Exception:
            pass # Failsafe: Ensures UI state reading errors do not block app shutdown
            
        if self.engine and not self.engine.is_shutdown:
            if messagebox.askokcancel("Quit", "Are you sure you want to exit the application?"):
                self.engine.shutdown()
                self.destroy()
        else: 
            self.destroy()


if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()