import tkinter as tk
from tkinter import simpledialog, messagebox, Toplevel, Label, Entry, Button
from selenium import webdriver
from selenium.webdriver.common.by import By

# Edge
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from webdriver_manager.microsoft import EdgeChromiumDriverManager

# Chrome
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager

# Firefox
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from webdriver_manager.firefox import GeckoDriverManager
import os
import threading
import time
import re
import traceback
import json
import logging
from datetime import datetime

# --- 配置日志 ---
logging.basicConfig(
    filename='heart_rate_monitor.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

def log(msg):
    logging.info(msg)
    # 依然保留print以便调试，实际运行时如果是pyw则看不到
    print(msg)

class ConfigManager:
    DEFAULT_CONFIG = {
        "widget_url": "https://pulsoid.net/widget/view/9a76934f-2fcd-4c9b-81a2-cd93db30ae9c",
        "font_size": 48,
        "refresh_rate": 0.2,
        "window_x": 100,
        "window_y": 100
    }
    CONFIG_FILE = "config.json"

    @classmethod
    def load_config(cls):
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key, value in cls.DEFAULT_CONFIG.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                log(f"加载配置失败: {e}, 使用默认配置")
                return cls.DEFAULT_CONFIG.copy()
        return cls.DEFAULT_CONFIG.copy()

    @classmethod
    def save_config(cls, config):
        try:
            with open(cls.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            log("配置已保存")
        except Exception as e:
            log(f"保存配置失败: {e}")

class SettingsDialog(Toplevel):
    def __init__(self, parent, config, on_save):
        super().__init__(parent)
        self.title("设置")
        self.geometry("400x250")
        self.config = config
        self.on_save = on_save
        self.transient(parent)
        self.grab_set()

        # URL
        Label(self, text="Pulsoid 组件 URL:").pack(pady=5)
        self.url_entry = Entry(self, width=50)
        self.url_entry.insert(0, config.get("widget_url", ""))
        self.url_entry.pack(pady=5)

        # Font Size
        Label(self, text="字体大小:").pack(pady=5)
        self.font_entry = Entry(self, width=10)
        self.font_entry.insert(0, str(config.get("font_size", 48)))
        self.font_entry.pack(pady=5)

        # Save Button
        Button(self, text="保存并重启", command=self.save).pack(pady=20)

    def save(self):
        new_url = self.url_entry.get().strip()
        try:
            new_font = int(self.font_entry.get().strip())
        except ValueError:
            messagebox.showerror("错误", "字体大小必须是数字")
            return

        if not new_url:
            messagebox.showerror("错误", "URL 不能为空")
            return

        self.config["widget_url"] = new_url
        self.config["font_size"] = new_font
        self.on_save(self.config)
        self.destroy()

class HeartRateOverlay:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.bpm = "--"
        self.running = True
        self.driver = None
        self.font_size = self.config.get("font_size", 48)
        self.retry_count = 0

        log(">>> 程序启动中...")

        try:
            self.init_browser()
            
            # 2. 设置 Tkinter 悬浮窗
            self.root = tk.Tk()
            self.root.title("Heart Rate Monitor")
            self.root.attributes('-topmost', True)
            self.root.overrideredirect(True)
            
            x = self.config.get("window_x", 100)
            y = self.config.get("window_y", 100)
            self.update_geometry(x, y)

            # 设置完全透明背景 (使用纯黑色)
            self.root.configure(bg='#000000')
            self.root.attributes('-transparentcolor', '#000000')

            # 使用 Canvas 绘制文字
            self.canvas = tk.Canvas(
                self.root,
                bg='#000000',
                highlightthickness=0,
                width=self.get_window_width(),
                height=self.get_window_height()
            )
            self.canvas.pack(expand=True, fill='both')

            # 创建文字对象
            self.text_id = self.canvas.create_text(
                self.get_window_width() // 2, 
                self.get_window_height() // 2,
                text="--",
                font=("Lexend", self.font_size),
                fill="#FF6B6B"
            )

            # 绑定拖动事件
            self.canvas.bind("<Button-1>", self.start_move)
            self.canvas.bind("<B1-Motion>", self.do_move)
            self.canvas.bind("<ButtonRelease-1>", self.stop_move)
            
            # 绑定右键菜单
            self.create_context_menu()
            self.canvas.bind("<Button-3>", self.show_context_menu)
            
            # 绑定 Alt+滚轮 调整大小
            self.root.bind_all("<Alt-MouseWheel>", self.resize_font)

            # 启动线程
            self.thread = threading.Thread(target=self.fetch_data_loop)
            self.thread.daemon = True
            self.thread.start()

            log(">>> 初始化完成，开始实时监控...")
            self.update_gui()
            self.root.mainloop()

        except Exception as e:
            log(f"致命错误: {e}")
            messagebox.showerror("启动失败", f"程序无法启动:\n{str(e)}")
            self.quit()

    def get_window_width(self):
        return max(150, self.font_size * 4)
    
    def get_window_height(self):
        return max(60, self.font_size * 2)

    def update_geometry(self, x, y):
        w = self.get_window_width()
        h = self.get_window_height()
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def create_context_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="设置 (Settings)", command=self.open_settings)
        self.menu.add_separator()
        self.menu.add_command(label="退出 (Exit)", command=self.quit)

    def show_context_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def open_settings(self):
        SettingsDialog(self.root, self.config, self.save_settings)

    def save_settings(self, new_config):
        self.config = new_config
        ConfigManager.save_config(self.config)
        log("配置已更新，准备重启...")
        messagebox.showinfo("提示", "配置已保存，程序将重新加载。")
        self.quit()
        # 简单的重启方式：重新运行脚本 (但这在 Threading 下可能复杂，这里选择先退出，用户需手动重启，或者可以做内部重置)
        # 为了更好体验，我们这里只更新参数和重置 Driver
        # 但完全重启比较干净，这里先做退出。
        # FIXME: 理想情况下应该重构让 app 可重入，但现在先这样。
        
    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
        # Update config coords continuously or on release? Better on release.

    def stop_move(self, event):
        self.config["window_x"] = self.root.winfo_x()
        self.config["window_y"] = self.root.winfo_y()
        ConfigManager.save_config(self.config)

    def resize_font(self, event):
        """Alt+滚轮调整字体大小"""
        if event.delta > 0:
            self.font_size = min(120, self.font_size + 4)
        else:
            self.font_size = max(20, self.font_size - 4)
        
        self.config["font_size"] = self.font_size
        ConfigManager.save_config(self.config)
        
        # 更新UI
        self.canvas.itemconfig(self.text_id, font=("Lexend", self.font_size))
        
        # 调整窗口
        w = self.get_window_width()
        h = self.get_window_height()
        # 保持中心点或者左上角？这里保持左上角简单点，或者重新计算居中
        # 重新应用 geometry
        self.canvas.config(width=w, height=h)
        self.canvas.coords(self.text_id, w // 2, h // 2)
        self.root.geometry(f"{w}x{h}") # 可能会轻微错位，暂不处理复杂居中
        
        log(f"字体大小: {self.font_size}")

    # --- 浏览器/驱动逻辑复用原有的，但增加日志 ---
    def init_browser(self):
        # 内部函数：递归查找文件
        def find_file_recursively(root_dir, filename):
            log(f"正在目录 {root_dir} 搜索 {filename} ...")
            for dirpath, dirnames, filenames in os.walk(root_dir):
                if filename in filenames:
                    return os.path.join(dirpath, filename)
            return None

        def find_driver_locally(driver_filename):
            search_paths = [
                os.path.join(os.path.expanduser("~"), ".wdm"),
                os.getcwd(),
                os.path.dirname(os.path.abspath(__file__))
            ]
            for root_dir in list(set(search_paths)):
                if os.path.exists(root_dir):
                    found = find_file_recursively(root_dir, driver_filename)
                    if found: return found
            return None
        
            def find_browser_binary(browser_names):
                """在常用路径查找浏览器可执行文件"""
                common_paths = [
                    os.environ.get("PROGRAMFILES"),
                    os.environ.get("PROGRAMFILES(X86)"),
                    os.environ.get("LOCALAPPDATA"),
                    "C:\\Program Files",
                    "C:\\Program Files (x86)",
                    os.path.expanduser("~\\AppData\\Local")
                ]
                
                for root in common_paths:
                    if not root: continue
                    for name in browser_names:
                        # 常见固定路径
                        candidates = [
                            os.path.join(root, "Microsoft", "Edge", "Application", name),
                            os.path.join(root, "Google", "Chrome", "Application", name),
                            os.path.join(root, "Mozilla Firefox", name),
                            os.path.join(root, name) 
                        ]
                        for path in candidates:
                            if os.path.exists(path): 
                                log(f"Found binary: {path}")
                                return path
                    
                    # 搜索子目录
                    if "edge" in name.lower(): sub = ["Microsoft", "Microsoft\\Edge", "Microsoft\\Edge\\Application"]
                    elif "chrome" in name.lower(): sub = ["Google", "Google\\Chrome", "Google\\Chrome\\Application"]
                    elif "firefox" in name.lower(): sub = ["Mozilla Firefox", "Mozilla"]
                    else: sub = []
                    
                    for sf in sub:
                         sf_path = os.path.join(root, sf)
                         if os.path.exists(sf_path):
                             found = find_file_recursively(sf_path, name)
                             if found: 
                                 log(f"Found binary recursively: {found}")
                                 return found
            return None

        def try_init_edge():
            try:
                log(">>> [Edge] 尝试加载...")
                options = EdgeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--window-size=400,300")
                options.add_argument("--disable-gpu")
                
                service = None
                try:
                    service = EdgeService(EdgeChromiumDriverManager().install())
                except Exception as e:
                    log(f"[Edge] Manager install failed: {e}")
                    driver_path = find_driver_locally("msedgedriver.exe")
                    if driver_path: service = EdgeService(driver_path)

                # 尝试自动定位
                try:
                    return webdriver.Edge(service=service, options=options) if service else webdriver.Edge(options=options)
                except Exception as e:
                    log(f"[Edge] Standard init failed: {e}")
                    # 尝试寻找二进制文件
                    if "binary" in str(e).lower() or "executable" in str(e).lower():
                         bin_path = find_browser_binary(["msedge.exe"])
                         if bin_path:
                             log(f"[Edge] Setting binary location to: {bin_path}")
                             options.binary_location = bin_path
                             return webdriver.Edge(service=service, options=options) if service else webdriver.Edge(options=options)
                    raise e
            except Exception as e:
                log(f"[Edge] 最终失败: {e}")
            return None

        def try_init_chrome():
            try:
                log(">>> [Chrome] 尝试加载...")
                options = ChromeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--window-size=400,300")
                options.add_argument("--disable-gpu")
                
                service = None
                try:
                    service = ChromeService(ChromeDriverManager().install())
                except Exception as e:
                    log(f"[Chrome] Manager install failed: {e}")
                    driver_path = find_driver_locally("chromedriver.exe")
                    if driver_path: service = ChromeService(driver_path)
                
                try:
                    return webdriver.Chrome(service=service, options=options) if service else webdriver.Chrome(options=options)
                except Exception as e:
                    log(f"[Chrome] Standard init failed: {e}")
                    if "binary" in str(e).lower() or "executable" in str(e).lower():
                        bin_path = find_browser_binary(["chrome.exe"])
                        if bin_path:
                            log(f"[Chrome] Setting binary location to: {bin_path}")
                            options.binary_location = bin_path
                            return webdriver.Chrome(service=service, options=options) if service else webdriver.Chrome(options=options)
                    raise e
            except Exception as e:
                log(f"[Chrome] 最终失败: {e}")
            return None

        def try_init_firefox():
            try:
                log(">>> [Firefox] 尝试加载...")
                options = FirefoxOptions()
                options.add_argument("--headless")
                
                service = None
                geckodriver_path = os.path.expanduser("~/.wdm/drivers/geckodriver/win64/v0.36.0/geckodriver.exe")
                if os.path.exists(geckodriver_path):
                    service = FirefoxService(geckodriver_path)
                else:
                    try:
                        service = FirefoxService(GeckoDriverManager().install())
                    except Exception as e:
                        log(f"[Firefox] Manager install failed: {e}")
                        driver_path = find_driver_locally("geckodriver.exe")
                        if driver_path: service = FirefoxService(driver_path)
                
                return webdriver.Firefox(service=service, options=options)
            except Exception as e:
                log(f"[Firefox] 最终失败: {e}")
            return None

        if self.driver is None: self.driver = try_init_edge()
        if self.driver is None: self.driver = try_init_chrome()
        if self.driver is None: self.driver = try_init_firefox()

        if self.driver is None:
            raise Exception("无法启动任何浏览器 (Edge/Chrome/Firefox)。请确保已安装浏览器并可能有对应驱动。")
        
        log(">>> 正在连接 Widget URL...")
        self.driver.get(self.config["widget_url"])

    def fetch_data_loop(self):
        while self.running:
            try:
                if not self.driver: break
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                match = re.search(r'\d+', body_text)
                if match:
                    new_bpm = match.group()
                    if new_bpm != self.bpm:
                        log(f"❤️ 心率更新: {new_bpm}")
                        self.bpm = new_bpm
                else:
                    self.bpm = "--"
            except Exception as e:
                log(f"❌ 读取错误: {e}")
                # 简单重连机制? 暂时忽略，避免死循环重启
            
            time.sleep(self.config.get("refresh_rate", 0.2))

    def update_gui(self):
        if not self.running: return
        try:
            self.canvas.itemconfig(self.text_id, text=self.bpm)
        except Exception:
            pass # 窗口可能已销毁
        self.root.after(100, self.update_gui)

    def quit(self):
        log(">>> 退出程序...")
        self.running = False
        try:
            if self.driver:
                self.driver.quit()
        except: pass
        try:
            self.root.destroy()
        except: pass
        log(">>> 已退出")

if __name__ == "__main__":
    try:
        # 再次确保日志已初始化
        logging.basicConfig(
            filename='heart_rate_monitor.log',
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S',
            force=True
        )
        app = HeartRateOverlay()
    except Exception as e:
        # 最后的兜底，防止直接闪退看不到错误
        # 尝试写入日志
        try:
            logging.critical(f"FATAL CRASH: {e}")
            logging.critical(traceback.format_exc())
        except: pass
        
        # 尝试弹窗
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("程序崩溃 (Fatal Error)", f"发生严重错误，程序无法继续运行：\n\n{str(e)}\n\n详情请查看 heart_rate_monitor.log")
            root.destroy()
        except Exception as e2:
            # 如果连Tkinter都起不来，那真的没办法了，只能写文件
            with open("panic.log", "w") as f:
                f.write(f"Crash: {e}\n{traceback.format_exc()}\nPopup failed: {e2}")

