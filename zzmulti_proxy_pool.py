# zz_multi_proxy_pool_final_v5_api_fix.py
import configparser
import time
import itertools
import logging
import threading
import multiprocessing
import socket
import struct
import json
import os 
import sys 
from http.server import HTTPServer, BaseHTTPRequestHandler
import socketserver
import requests
import socks
import select
from urllib.parse import urlparse
import concurrent.futures 
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# --- 0. 配置日志 ---
def setup_logging(log_file="log.txt", console_level=logging.INFO, file_level=logging.DEBUG):
    root_logger = logging.getLogger()
    root_logger.setLevel(file_level) 
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] (%(processName)s) %(message)s', datefmt='%H:%M:%S')

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(file_level) 
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

# --- 1. 代理池管理类 (最终修复版本 - 集成 API 解析和逻辑分离) ---
class ProxyPoolManager:
    def __init__(self, config_file='config.ini'):
        self._load_config(config_file)
        
        self.all_proxies = [] 
        self.available_proxies = [] 
        self.lock = threading.Lock() 
        
        self.current_proxy = None
        self.last_switch_time = time.time()
        self.request_counter = 0
        
        self.state_file = 'proxy_state.json'
        self.last_state_mtime = 0
        self.process_name = multiprocessing.current_process().name

    def _load_config(self, file_path):
        config = configparser.ConfigParser()
        try:
            config.read(file_path, encoding='utf-8') 
            self.http_port = config.getint('ProxyPool', 'HTTP_LISTEN_PORT', fallback=8888)
            self.socks5_port = config.getint('ProxyPool', 'SOCKS5_LISTEN_PORT', fallback=10808)
            self.switch_mode = config.get('ProxyPool', 'SWITCH_MODE', fallback='TIME').upper()
            self.time_interval = config.getint('ProxyPool', 'TIME_INTERVAL_SECONDS', fallback=300)
            self.request_limit = config.getint('ProxyPool', 'REQUEST_COUNT_LIMIT', fallback=100)
            self.proxy_file = config.get('ProxyPool', 'PROXY_FILE', fallback='proxies.txt')
            
            # --- API 抓取配置 ---
            self.api_url = config.get('ProxyPool', 'API_URL', fallback='')
            
            self.availability_check_url = config.get('HealthCheck', 'AVAILABILITY_CHECK_URL', fallback='https://www.baidu.com')
            self.health_check_interval = config.getint('HealthCheck', 'HEALTH_CHECK_INTERVAL_SECONDS', fallback=120)
            
            # --- API 抓取间隔 ---
            fetch_config = config['ProxyFetch'] if 'ProxyFetch' in config else config['HealthCheck']
            self.fetch_proxy_interval = fetch_config.getint('FETCH_PROXY_INTERVAL_SECONDS', fallback=120)
            
        except Exception as e:
            logging.warning(f"配置文件读取部分失败，使用默认值: {e}")

    def _load_proxies_from_file(self):
        """只从文件加载，用于初始化和更新 all_proxies"""
        proxies = []
        if os.path.exists(self.proxy_file):
            try:
                with open(self.proxy_file, 'r', encoding='utf-8') as f:
                    proxies = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
                
            except Exception as e:
                logging.error(f"[Init] 读取代理文件失败: {e}")
        
        with self.lock:
             self.all_proxies = proxies
        if proxies:
             logging.info(f"[Init] 从 {self.proxy_file} 加载了 {len(proxies)} 个代理。")
        return bool(proxies)

    def _save_proxies_to_file(self, proxies_list):
        """将最新的代理列表写入文件"""
        try:
            with open(self.proxy_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(sorted(list(set(proxies_list)))))
            logging.info(f"[Save] 成功将 {len(proxies_list)} 个代理写入 {self.proxy_file}")
        except Exception as e:
            logging.error(f"[Save] 写入代理文件失败: {e}")

    # --- 状态同步（保持不变） ---
    def _save_state(self):
        try:
            state = {
                'current_proxy': self.current_proxy,
                'timestamp': time.time(),
                'pid': os.getpid()
            }
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state, f)
            os.replace(temp_file, self.state_file)
        except Exception as e:
            logging.error(f"[State] 保存状态失败: {e}")

    def _sync_state(self):
        if not os.path.exists(self.state_file):
            return

        try:
            mtime = os.path.getmtime(self.state_file)
            if mtime > self.last_state_mtime:
                self.last_state_mtime = mtime
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                
                external_proxy = state.get('current_proxy')
                if external_proxy and external_proxy != self.current_proxy:
                    logging.info(f"[Sync] 同步到外部进程的新代理: {external_proxy}")
                    with self.lock:
                        self.current_proxy = external_proxy
                        self.last_switch_time = time.time()
                        self.request_counter = 0
        except Exception:
            pass

    def _check_proxy_availability(self, proxy_url):
        """核心检查逻辑：使用严格的超时控制"""
        parsed = urlparse(proxy_url)
        scheme = parsed.scheme.lower()
        test_url = self.availability_check_url
        
        proxies = {}
        try:
            # 修正 SOCKS 代理的请求格式
            if scheme == 'socks5':
                proxies = {'http': f'socks5h://{parsed.hostname}:{parsed.port}', 
                           'https': f'socks5h://{parsed.hostname}:{parsed.port}'}
            elif scheme == 'socks4':
                proxies = {'http': f'socks4a://{parsed.hostname}:{parsed.port}', 
                           'https': f'socks4a://{parsed.hostname}:{parsed.port}'}
            else:
                proxies = {'http': proxy_url, 'https': proxy_url}
            
            # 使用更严格的超时控制 (3秒连接，7秒读取)，防止代理长期阻塞
            logging.debug(f"[Check] 正在测试代理: {proxy_url}")
            resp = requests.get(test_url, proxies=proxies, timeout=(3, 7), verify=False)
            logging.debug(f"[Check] 代理 {proxy_url} 测试通过, 状态码: {resp.status_code}")
            return resp.status_code >= 200 and resp.status_code < 400
        except Exception as e:
            logging.debug(f"[Check] 代理 {proxy_url} 测试失败: {e}")
            return False

    def _run_health_check(self):
        """使用线程池并发执行健康检查，防止卡顿"""
        # --- 检查源：仅从 self.all_proxies (本地文件) 加载 ---
        self._load_proxies_from_file() 
        check_list = list(self.all_proxies)
        
        if not check_list:
            logging.warning("[Health] 总代理池为空，无法进行健康检查。")
            return

        logging.info(f"[Health] 开始健康检查... 总数: {len(check_list)}")
        valid_proxies = []
        
        # 配置线程池参数
        MAX_WORKERS = 10 
        # 优化：总超时时间缩短为 5 秒，快速跳过卡顿代理
        TOTAL_CHECK_TIMEOUT = 5 

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_proxy = {executor.submit(self._check_proxy_availability, p): p for p in check_list}
            
            try:
                for future in concurrent.futures.as_completed(future_to_proxy, timeout=TOTAL_CHECK_TIMEOUT):
                    proxy = future_to_proxy[future]
                    try:
                        is_available = future.result()
                        if is_available:
                            valid_proxies.append(proxy)
                    except Exception as exc:
                        logging.debug(f"[Check] 代理 {proxy} 检查抛出异常: {exc}")

            except concurrent.futures.TimeoutError:
                 logging.warning(f"[Health] 健康检查超时 ({TOTAL_CHECK_TIMEOUT}秒)，跳过剩余检查。")
            except Exception as e:
                 logging.error(f"[Health] 线程池执行异常: {e}")

        with self.lock:
            self.available_proxies = valid_proxies
            if not self.available_proxies:
                logging.warning("[Health] 无可用代理，进入兜底模式（使用全列表）。")
            else:
                logging.info(f"[Health] 检查完成，可用: {len(self.available_proxies)}/{len(self.all_proxies)}")

    def _health_check_loop(self):
        while True:
            try:
                self._run_health_check()
            except Exception as e:
                logging.error(f"[Health] Loop error: {e}")
            time.sleep(self.health_check_interval)

    # --- 修复：API 抓取逻辑（处理 JSON 嵌套结构） ---
    def _fetch_proxies_from_api(self):
        """从配置的 API 地址拉取新代理，解析嵌套 JSON，并保存到文件"""
        if not self.api_url:
            return

        logging.info("[Fetch] 正在从 API 拉取新代理...")
        try:
            resp = requests.get(self.api_url, timeout=10)
            resp.raise_for_status() 

            new_proxies_list = []
            
            # --- 核心解析逻辑修复 ---
            try:
                data = resp.json()
                # 检查您提供的结构: {"success":true,"free":{"proxies": [...]}}
                if isinstance(data, dict) and data.get('success') and 'free' in data:
                    proxy_container = data['free']
                    if isinstance(proxy_container, dict) and 'proxies' in proxy_container:
                        for p in proxy_container['proxies']:
                            # 格式化为 scheme://ip:port
                            if all(key in p for key in ['ip', 'port', 'scheme']):
                                new_proxies_list.append(f"{p['scheme']}://{p['ip']}:{p['port']}")
                            
            except json.JSONDecodeError:
                # 处理非 JSON 响应，作为文本处理
                new_proxies_list = [p.strip() for p in resp.text.splitlines() if p.strip()]

            if new_proxies_list:
                
                # 1. 读取当前文件中的代理
                current_proxies = set(self.all_proxies) 
                
                # 2. 合并去重
                old_count = len(current_proxies)
                current_proxies.update(new_proxies_list)
                
                # 3. 保存到文件
                self._save_proxies_to_file(list(current_proxies))
                
                added_count = len(current_proxies) - old_count
                
                if added_count > 0:
                    logging.info(f"[Fetch] 成功解析 {len(new_proxies_list)} 个代理，新增 {added_count} 个，总池更新为 {len(current_proxies)} 个。")
                else:
                    logging.info("[Fetch] API 代理已存在或无新增。")

            # 4. 抓取完成后，更新内存中的 self.all_proxies
            self._load_proxies_from_file()

        except requests.RequestException as e:
            logging.error(f"[Fetch] 从 API 拉取代理失败: {e}")

    def _fetch_proxy_loop(self):
        if not self.api_url:
            return
            
        while True:
            try:
                self._fetch_proxies_from_api()
            except Exception as e:
                logging.error(f"[Fetch] Loop error: {e}")
            time.sleep(self.fetch_proxy_interval)

    def initial_setup(self):
        # 1. 启动时加载本地代理文件
        self._load_proxies_from_file()
        
        if not self.all_proxies and not self.api_url:
            logging.error("没有代理可用，请检查 proxies.txt 或配置 API_URL")
            return

        # 2. 启动健康检查线程 (检查频率由 HEALTH_CHECK_INTERVAL_SECONDS 控制)
        t1 = threading.Thread(target=self._health_check_loop, daemon=True)
        t1.start()
        
        # 3. 启动 API 抓取线程 (如果配置了 API)
        if self.api_url:
            self._fetch_proxies_from_api() # 首次立即拉取
            t2 = threading.Thread(target=self._fetch_proxy_loop, daemon=True)
            t2.start()
        
        # 4. 首次健康检查（使用已加载/抓取/合并后的代理列表）
        # 依赖 _run_health_check 中 TOTAL_CHECK_TIMEOUT=5 的保护
        self._run_health_check()

    # --- 代理切换和计数逻辑（保持不变） ---
    def get_current_proxy(self):
        self._sync_state()

        with self.lock:
            if not self.current_proxy:
                self._pick_next_proxy()
            
            if self.switch_mode == 'TIME' and (time.time() - self.last_switch_time > self.time_interval):
                logging.info("触发时间切换")
                self._pick_next_proxy()
            elif self.switch_mode == 'REQUEST' and (self.request_counter >= self.request_limit):
                logging.info("触发请求次数切换")
                self._pick_next_proxy()
                
            return self.current_proxy
    
    def _pick_next_proxy(self):
        pool = self.available_proxies if self.available_proxies else self.all_proxies
        if not pool: return

        prev = self.current_proxy
        next_proxy = None

        try:
            current_index = -1
            if self.current_proxy and self.current_proxy in pool:
                current_index = pool.index(self.current_proxy)
            
            next_index = (current_index + 1) % len(pool)
            next_proxy = pool[next_index]
        except Exception:
            next_proxy = pool[0]

        self.current_proxy = next_proxy
        
        if self.current_proxy != prev:
            logging.info(f"🔄 切换代理: {prev} -> {self.current_proxy}")
            self.last_switch_time = time.time()
            self.request_counter = 0
            self._save_state()

    def switch_proxy(self, failed_proxy=None):
        with self.lock:
            if failed_proxy and failed_proxy in self.available_proxies:
                self.available_proxies.remove(failed_proxy)
                logging.warning(f"❌ 代理失败，从可用池中临时移除: {failed_proxy}")
            
            if failed_proxy == self.current_proxy:
                self.current_proxy = None
            
            self._pick_next_proxy()

    def increment_counter(self):
        with self.lock:
            self.request_counter += 1

    def get_proxy_parts(self, proxy_url):
        if not proxy_url: return None, None, None
        try:
            p = urlparse(proxy_url)
            return p.hostname, p.port, p.scheme
        except:
            return None, None, None

# --- 2. HTTP 服务器（保持不变） ---
class HTTP_ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _get_upstream_proxy(self):
        manager = getattr(self.server, 'manager', None)
        if manager: return manager.get_current_proxy()
        return None

    def do_CONNECT(self):
        proxy_url = self._get_upstream_proxy()
        if not proxy_url:
            self.send_error(503, "No Proxy Available")
            return

        host, port, scheme = getattr(self.server, 'manager').get_proxy_parts(proxy_url)
        target_host, target_port = self.path.split(':')
        target_port = int(target_port)

        try:
            s = socks.socksocket()
            if scheme == 'socks5':
                s.set_proxy(socks.SOCKS5, host, port, rdns=True)
            elif scheme == 'socks4':
                s.set_proxy(socks.SOCKS4, host, port, rdns=True)
            
            s.settimeout(10)
            s.connect((target_host, target_port))

            self.send_response(200, 'Connection Established')
            self.end_headers()
            self._relay(self.connection, s)
        except Exception as e:
            logging.debug(f"Tunnel failed: {e}")
            self.server.manager.switch_proxy(proxy_url)
            self.send_error(502)

    def do_GET(self): self._handle_http()
    def do_POST(self): self._handle_http()

    def _handle_http(self):
        proxy_url = self._get_upstream_proxy()
        if not proxy_url:
            self.send_error(503)
            return
            
        url = self.path
        if not url.startswith('http'):
            url = f"http://{self.headers.get('Host')}{url}"

        try:
            req_proxies = {}
            if 'socks5' in proxy_url:
                fixed_url = proxy_url.replace('socks5://', 'socks5h://')
                req_proxies = {'http': fixed_url, 'https': fixed_url}
            elif 'socks4' in proxy_url:
                fixed_url = proxy_url.replace('socks4://', 'socks4a://')
                req_proxies = {'http': fixed_url, 'https': fixed_url}
            else:
                req_proxies = {'http': proxy_url, 'https': proxy_url}

            headers = dict(self.headers)
            if 'Proxy-Connection' in headers: del headers['Proxy-Connection']
            
            body = None
            if 'Content-Length' in headers:
                body = self.rfile.read(int(headers['Content-Length']))

            resp = requests.request(
                self.command, url, headers=headers, data=body,
                proxies=req_proxies, verify=False, allow_redirects=False,
                timeout=10, stream=True
            )

            self.send_response(resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in ['transfer-encoding', 'content-encoding', 'connection']:
                    self.send_header(k, v)
            self.end_headers()
            
            for chunk in resp.iter_content(8192):
                self.wfile.write(chunk)
            self.server.manager.increment_counter()
            
        except Exception as e:
            logging.debug(f"HTTP Request failed: {e}")
            self.server.manager.switch_proxy(proxy_url)
            self.send_error(502)

    def _relay(self, client, remote):
        sockets = [client, remote]
        try:
            while True:
                r, _, _ = select.select(sockets, [], sockets, 30)
                if not r: break
                for s in r:
                    data = s.recv(8192)
                    if not data: return
                    if s is client: remote.sendall(data)
                    else: client.sendall(data)
        except: pass
        finally:
            try: client.close()
            except: pass
            try: remote.close()
            except: pass

class ThreadingHTTPServer(HTTPServer):
    def __init__(self, addr, handler, manager):
        super().__init__(addr, handler)
        self.manager = manager

# --- 3. SOCKS5 服务器（保持不变） ---
class SOCKS5_Handler(socketserver.BaseRequestHandler):
    def handle(self):
        manager = self.server.manager
        client = self.request
        current_proxy = None 
        
        try:
            current_proxy = manager.get_current_proxy()
            if not current_proxy: 
                client.close()
                return

            client.recv(262)
            client.send(b"\x05\x00")
            
            data = client.recv(4)
            if not data or data[1] != 1: return
            
            addr_type = data[3]
            if addr_type == 1:
                addr = socket.inet_ntoa(client.recv(4))
            elif addr_type == 3:
                addr = client.recv(ord(client.recv(1))).decode()
            else: return
            
            port = struct.unpack('>H', client.recv(2))[0]
            
            phost, pport, pscheme = manager.get_proxy_parts(current_proxy)
            
            remote = socks.socksocket()
            if 'socks5' in pscheme:
                remote.set_proxy(socks.SOCKS5, phost, pport, rdns=True)
            elif 'socks4' in pscheme:
                remote.set_proxy(socks.SOCKS4, phost, pport, rdns=True)
                
            remote.settimeout(10)
            remote.connect((addr, port))
            
            client.send(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + struct.pack(">H", 0))
            
            manager.increment_counter()
            self._relay(client, remote)
        except Exception as e:
            logging.debug(f"SOCKS5 Error: {e}")
            if current_proxy:
                manager.switch_proxy(current_proxy)
            client.close()

    def _relay(self, client, remote):
        sockets = [client, remote]
        try:
            while True:
                r, _, _ = select.select(sockets, [], sockets, 30)
                if not r: break
                for s in r:
                    data = s.recv(8192)
                    if not data: return
                    if s is client: remote.sendall(data)
                    else: client.sendall(data)
        except: pass
        finally:
            remote.close()

class ThreadingSocksServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    def __init__(self, addr, handler, manager):
        super().__init__(addr, handler)
        self.manager = manager

# --- 4. 全局进程启动函数（保持不变） ---
def run_proxy_service(service_type):
    setup_logging()
    
    manager = ProxyPoolManager()
    manager.initial_setup()

    if service_type == 'http':
        logging.info(f"🚀 HTTP Proxy 启动，监听端口: {manager.http_port}")
        server = ThreadingHTTPServer(('0.0.0.0', manager.http_port), HTTP_ProxyHandler, manager)
        server.serve_forever()
    elif service_type == 'socks':
        logging.info(f"🚀 SOCKS5 Proxy 启动，监听端口: {manager.socks5_port}")
        server = ThreadingSocksServer(('0.0.0.0', manager.socks5_port), SOCKS5_Handler, manager)
        server.serve_forever()

# --- 5. 主程序入口（保持不变） ---
if __name__ == '__main__':
    setup_logging()
    logging.info("=== 动态代理池服务启动 ===")
    
    if os.path.exists('proxy_state.json'):
        try: os.remove('proxy_state.json')
        except: pass
    
    p1 = multiprocessing.Process(target=run_proxy_service, args=('http',), name="HTTP_Process")
    p2 = multiprocessing.Process(target=run_proxy_service, args=('socks',), name="SOCKS_Process")

    p1.start()
    p2.start()
    
    try:
        p1.join()
        p2.join()
    except KeyboardInterrupt:
        logging.info("Stopping services...")
        p1.terminate()
        p2.terminate()
        p1.join()
        p2.join()