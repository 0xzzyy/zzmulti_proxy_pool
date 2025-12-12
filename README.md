🚀 动态多进程代理池 (zzmulti_proxy_pool)这是一个基于 Python 多进程、多线程和 文件同步 的高性能动态代理池系统。
    这是一个基于 Python **多进程**、**多线程**和 **文件同步** 的高性能动态代理池工具。自己用来做渗透测试时的代理小工具，可以自定义的灵活切换代理，支持按时间间隔或按请求次数自动切换代理，代理池取自悠悠免费代理，可自定义抓取免费代理的时间间隔。

## ✨ 核心特性

| 特性 | 描述 | 状态 |
| :--- | :--- | :--- |
| **多协议支持** | 同时启动两个独立进程，提供 **HTTP/HTTPS 代理** 和 **SOCKS5 代理** 服务。 | ✅ 稳定 |
| **智能 API 抓取** | 周期性地从 `API_URL` 拉取数据，并支持解析如 `{"free":{"proxies": [...]}}` 的复杂 JSON 嵌套结构。 | ✅ 修复 |
| **高并发检查 (防卡顿)** | 使用 **线程池** (ThreadPoolExecutor) 并发检查代理可用性，并设置严格的 **5 秒总超时**，彻底解决因慢速代理导致的进程阻塞。 | ✅ 修复 |
| **文件同步机制** | 抓取到的代理、代理状态（`proxy_state.json`）均通过文件同步，保证两个独立进程的数据一致性。 | ✅ 稳定 |
| **灵活切换机制** | 支持按 **时间 (`TIME`)** 或按 **请求次数 (`REQUEST`)** 自动切换上游代理。 | ✅ 稳定 |
| **故障即时移除** | 当上游代理连接失败时，会立即将其从可用池中临时移除并切换到下一个代理。 | ✅ 稳定 |

## 🛠️ 快速开始

### 1. 准备工作

请确保您已安装 Python 3.x 环境，并安装所需的第三方库：

```bash
pip install -r requirements.txt
```

### 2. 配置文件 (config.ini)
请在脚本同级目录下创建或修改 config.ini 文件，以下是默认配置和说明：
```bash
[ProxyPool]
# HTTP代理监听端口
HTTP_LISTEN_PORT = 1233

# SOCKS5代理监听端口  
SOCKS5_LISTEN_PORT = 1234

# 代理切换模式: TIME(时间) 或 REQUEST(请求次数)
SWITCH_MODE = TIME

# 时间模式下的切换间隔(秒)
TIME_INTERVAL_SECONDS = 60

# 请求次数模式下的切换限制
REQUEST_COUNT_LIMIT = 100

# 代理API地址
API_URL = https://uu-proxy.com/api/free

# 本地代理缓存文件
PROXY_FILE = proxies.txt

# 可用性检查URL
AVAILABILITY_CHECK_URL = https://www.baidu.com

# 是否启用匿名性检查
ANONYMITY_CHECK_ENABLED = false

# 健康检查间隔(秒)
HEALTH_CHECK_INTERVAL_SECONDS = 120

[ProxyFetch]
# 代理抓取间隔(秒)
FETCH_PROXY_INTERVAL_SECONDS = 120
```
### 3. 运行服务
直接运行主脚本即可：
Bashpython zz_multi_proxy_pool.py
### 4. 代理使用服务启动后，您可以通过以下地址使用代理：
```bash
http://127.0.0.1:1233
socks5://127.0.0.1:1234
```
<img width="2188" height="1130" alt="image" src="https://github.com/user-attachments/assets/24508d5c-d65b-487e-b2b5-0b62d2b26489" />

### 5. 代理测试和使用
将配置文件中代理切换模式更改为以下配置，意味着请求一次会切换一次代理ip
```bash
SWITCH_MODE = REQUEST
REQUEST_COUNT_LIMIT = 1
```
http://current.ip.16yun.cn:802访问该网站可以显示自己当前ip，使用以下命令进行多次访问测试
```bash
curl --proxy http://127.0.0.1:1233 http://current.ip.16yun.cn:802
```
通过截图可知；能成功请求一次会切换一次代理
<img width="1694" height="392" alt="image" src="https://github.com/user-attachments/assets/1eb507c7-29ab-49d2-a53d-03444c904730" />

接下来我们就可以进行测试啦，over~


