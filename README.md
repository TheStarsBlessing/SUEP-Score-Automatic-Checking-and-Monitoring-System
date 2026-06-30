# SUEP 成绩自动查询与监控系统

本项目是一个用于 **上海电力大学（SUEP）** 学生的成绩自动查询与变动监控工具，支持 **桌面 GUI** 和 **Web 管理界面** 两种运行模式。  
它能够定时抓取教务系统的成绩数据，检测新增或修改的课程，并通过多种方式（邮件、Pushplus、Server酱、企业微信、Telegram、Bark、桌面弹窗等）向用户发送通知，让你第一时间掌握成绩动态。

> **⚠️ 重要提示**  
> 本工具仅供个人学习与参考，**请勿用于任何商业或非法用途**。使用前请确保你已阅读并遵守上海电力大学的相关网络与信息系统使用规定。

---

## ✨ 功能特点

- 🔐 **自动登录**：通过学校统一身份认证（IDS）自动登录，支持 Cookie 缓存。
- 📊 **成绩抓取**：解析教务系统成绩页面，结构化提取课程信息（学分、成绩、绩点等）。
- 🔄 **变动检测**：对比历史成绩，自动识别 **新增课程** 和 **成绩修改**。
- 📨 **多渠道通知**：支持 **邮件、Pushplus、Server酱、企业微信、Telegram、Bark、桌面弹窗** 等多种通知方式，可同时启用多个。
- 🖥️ **桌面 GUI**：基于 Tkinter 的图形界面，适合个人电脑直接操作。
- 🌐 **Web 管理界面**：基于 Flask 的网页控制台，支持远程访问、查看日志、手动查询、配置修改。
- ⏱️ **定时监控**：可自定义查询间隔，后台自动运行，异常时自动重试并发送错误提醒。
- 📁 **历史数据持久化**：成绩数据保存在 `grade_data.txt`，配置保存在 `config.json`。
- 🐳 **Docker 一键部署**：支持 Docker Compose 快速运行，便于长期稳定监控，与 EasyConnect 容器无缝配合。

---

## 🖥️ 界面参考

 - 桌面版
<img width="1667" height="1194" alt="2026-07-01_011842" src="https://github.com/user-attachments/assets/270b9fe2-4c32-4b7c-b83f-3d6cd0935b02" />

 - 网页版
<img width="3000" height="4361" alt="Screenshot_20260630_194221_com_trim_app_MainActivity" src="https://github.com/user-attachments/assets/d4c4a941-e75d-4a01-ab64-e1081278e362" />


## 🚀 快速开始

### 1. 环境要求

- Python 3.8 或更高版本（传统运行方式）
- 网络环境可访问上海电力大学教务系统（通常需校内网络或 VPN）
- （可选）Docker 和 Docker Compose（推荐用于服务化部署）

### 2. 克隆项目

```bash
git clone https://github.com/your-username/SUEP-grade-monitor.git
cd SUEP-grade-monitor
```

### 3. 安装依赖（传统方式）

**桌面版（GUI）**：

```bash
pip install -r requirements.txt
```

**Web 版**（若同时使用两个版本，可合并安装）：

```bash
pip install -r requirements-web.txt   # 或直接 pip install Flask APScheduler requests lxml
```

> **注意**：桌面版依赖 `win10toast` 用于桌面弹窗（仅 Windows）。若你不需要桌面通知，可忽略该依赖。

### 4. 配置

首次运行会自动生成 `config.json`，你需要**填写自己的学号和密码**以及其他通知渠道的凭证。

| 配置项 | 说明 |
|--------|------|
| `username` | 学号 |
| `password` | 统一身份认证密码 |
| `semester_str` | 要查询的学期，格式如 `"2025-2026.2"`（学年-学年.学期，1或2） |
| `query_interval` | 定时查询间隔（秒），默认 300 |
| `retry_interval` | 查询失败后的重试间隔（秒），默认 60 |
| `max_retries` | 最大重试次数，默认 2 |
| `notification_methods` | 启用的通知方式列表，如 `["email", "pushplus"]` |
| 各通知方式的专用配置 | 如 `smtp_server`, `pushplus_token` 等，按需填写 |

> 你可以在桌面 GUI 的“设置”界面或 Web 界面的设置表单中修改这些配置。

### 5. 运行

#### 桌面 GUI 版

```bash
python grade_gui.py
```

或双击 `start.bat`（Windows）。

#### Web 版（传统方式）

```bash
python app.py
```

默认监听 `0.0.0.0:15029`，打开浏览器访问 `http://127.0.0.1:15029` 即可使用。

#### <!-- NEW --> Docker Compose（推荐用于长期服务）

如果你希望将监控程序作为后台服务运行，并自动处理网络代理（如 EasyConnect），可以使用 Docker Compose。

```bash
# 启动监控服务（需先配置好代理，详见下文“Docker部署”）
docker-compose up -d
```

访问 `http://localhost:15029` 进行配置和监控。

---

## 📖 使用说明

### 桌面 GUI

- 启动后程序会自动尝试登录（使用 Cookie 缓存或账号密码）。
- 主界面显示当前学期成绩表格，右侧有日志输出。
- **开始监控**：启动定时查询，检测到变动后自动发送通知。
- **立即查询**：手动执行一次成绩抓取。
- **设置**：可修改账号、学期、通知方式等所有配置。

### Web 管理界面

- 顶部状态栏显示当前监控状态、成绩总数。
- **开始监控 / 停止**：控制后台定时任务。
- **立即查询**：手动触发一次查询。
- **测试通知**：发送测试消息验证通知配置。
- **设置区域**：可修改所有配置项，点击“保存设置”即刻生效（监控间隔等需重启监控）。
- 成绩表格和日志实时刷新（每 5 秒）。

---

## <!-- NEW --> 🐳 Docker 部署详解

### 前提条件

- 安装 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)（V2 或 V3 均可）。
- 确保宿主机网络能访问教务系统（若在校外，需使用 VPN 代理，如 EasyConnect）。

### 文件说明

项目中提供了两个 Compose 文件：

- `docker-compose.yml`：定义 `grade-monitor` 服务，构建应用镜像并运行。
- `docker-compose-easyconnect.yml`：定义 `easyconnect` 服务（基于 [hagb/docker-easyconnect](https://hub.docker.com/r/hagb/docker-easyconnect)），提供 SOCKS5 代理（端口 `1080`），方便校外访问。

你可以根据需要单独使用 `grade-monitor`（若已在校内网或已有代理），或两个一起使用。

### 快速启动（含 EasyConnect 代理）

1. **启动 EasyConnect 容器**（若你需要 VPN 代理）：
   ```bash
   docker-compose -f docker-compose-easyconnect.yml up -d
   ```
   该容器会暴露 SOCKS5 代理于 `1080` 端口，并可通过 VNC（端口 `5910`）进行登录操作。  
   首次启动后，需要访问 `http://宿主机IP:5910` 通过 noVNC 登录你的 VPN 账号（密码可在环境变量中预设，详见镜像文档）。

2. **启动 grade-monitor 容器**：
   ```bash
   docker-compose up -d
   ```

   此时，`grade-monitor` 会通过环境变量 `PROXY_URL=socks5://host.docker.internal:1080` 自动使用 EasyConnect 代理。  
   如果 EasyConnect 运行在另一台机器或容器名称不同，可修改 `PROXY_URL` 指向正确的地址。

3. **访问 Web 界面**：`http://localhost:15029`，首次进入后请配置学号、密码和通知方式。

### 配置与环境变量

`grade-monitor` 容器支持通过环境变量覆盖 `config.json` 中的部分配置，方便无交互部署：

| 环境变量 | 对应配置项 | 说明 |
|----------|------------|------|
| `PROXY_ENABLED` | `proxy_enabled` | `true` 或 `false`，是否启用代理 |
| `PROXY_URL` | `proxy_url` | 代理地址，如 `socks5://host.docker.internal:1080` |
| `QUERY_INTERVAL` | `query_interval` | 查询间隔（秒） |
| `SEMESTER_STR` | `semester_str` | 学期字符串，如 `"2025-2026.2"` |

你还可以挂载外部配置文件或通过 Web 界面修改，优先级为：**环境变量 > Web 界面保存 > 默认值**。

### 数据持久化

Compose 中通过 volumes 挂载了以下目录：

- `./data:/app/data`：存放 `config.json`、`grade_data.txt`、`cookies.txt` 等持久化数据。
- `./logs:/app/logs`：存放 `web.log` 日志文件。

这些目录在宿主机上可见，即使容器重建也不会丢失数据。

### 构建镜像

如果你需要自定义 Dockerfile，可参考以下示例（项目根目录已提供 `Dockerfile`）：

```dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY . .

EXPOSE 15029
CMD ["python", "app.py"]
```

若未提供 `requirements-web.txt`，可直接使用 `requirements.txt`（但需确保包含 `Flask` 和 `APScheduler`）。

### 与 EasyConnect 容器网络互通

`docker-compose.yml` 中已添加 `extra_hosts` 配置，使容器能通过 `host.docker.internal` 访问宿主机。若 EasyConnect 容器与 grade-monitor 在同一宿主机，且代理端口映射到宿主机 `1080`，则 `PROXY_URL=socks5://host.docker.internal:1080` 即可生效。

如果 EasyConnect 是单独的 Compose 项目，建议将其网络设置为与 grade-monitor 共享网络（如使用 `network_mode: "service:easyconnect"`），或使用容器名称作为主机名（需自定义网络）。

### 停止和清理

```bash
docker-compose down          # 停止并移除容器
docker-compose down -v       # 同时移除数据卷（慎用）
```

---

## 📨 通知方式详解

在 `notification_methods` 列表中填入对应的标识符即可启用，各方式所需配置字段如下：

| 方式 | 标识符 | 所需配置字段 |
|------|--------|--------------|
| 邮件 | `email` | `smtp_server`, `smtp_port`, `sender_email`, `sender_password`, `receiver_email` |
| Server酱 | `serverchan` | `serverchan_token`（SendKey） |
| Pushplus | `pushplus` | `pushplus_token` |
| 企业微信机器人 | `wework` | `wework_webhook`（群机器人 Webhook 地址） |
| 桌面弹窗（Windows） | `desktop` | 无需额外配置，需安装 `win10toast` |
| Telegram | `telegram` | `telegram_bot_token`, `telegram_chat_id` |
| Bark（iOS） | `bark` | `bark_key`（Bark 推送 Key） |

> 你可以同时启用多个方式，程序会依次尝试发送，任一失败不影响其他方式。

---

## 📁 文件结构说明

```
.
├── grade_gui.py          # 桌面 GUI 主程序
├── app.py                # Web 版主程序
├── ids.py                # 统一身份认证模块
├── grade_fetcher.py      # 成绩抓取与变动检测
├── notifier.py           # 通知发送模块
├── config_manager.py     # 配置加载与保存
├── envconfig.py          # 配置导入（供其他模块使用）
├── config.json           # 配置文件（自动生成）
├── grade_data.txt        # 成绩数据文件（历史记录）
├── cookies.txt           # Cookie 缓存（自动生成）
├── requirements.txt      # 桌面版依赖
├── requirements-web.txt  # Web 版依赖（需自行创建，内容参考“安装依赖”）
├── start.bat             # Windows 启动脚本（桌面版）
├── Dockerfile            # Docker 镜像构建文件（建议添加）
├── docker-compose.yml    # grade-monitor 服务编排
├── docker-compose-easyconnect.yml # EasyConnect 服务编排（可选）
├── templates/
│   └── index.html        # Web 版前端页面
└── logs/
    └── web.log           # Web 版运行日志（自动生成）
```

---

## ❓ 常见问题

**Q：登录失败怎么办？**  
A：检查用户名密码是否正确，确认网络能访问 `ids.shiep.edu.cn` 和 `jw.shiep.edu.cn`。若 Cookie 过期，程序会自动尝试重新登录。在 Docker 部署中，请确保代理配置正确。

**Q：成绩抓取不到或解析错误？**  
A：可能是教务系统页面结构发生变化。请检查 `grade_fetcher.py` 中的 XPath 是否仍然有效，必要时更新解析逻辑。

**Q：Web 版如何后台运行？**  
A：可使用 `nohup python app.py &`（Linux）或将其注册为系统服务。注意修改 `app.run(debug=False)` 以避免调试模式。使用 Docker 方式可直接以后台服务运行。

**Q：如何更改监听端口？**  
A：修改 `app.py` 末尾的 `port` 参数，或在 Docker 中修改 `ports` 映射。

**Q：通知没有收到？**  
A：先点击“测试通知”验证配置是否正确；检查对应服务的 Token/Key 是否有效；查看程序日志是否有错误输出。

**Q：Docker 容器中无法访问宿主机代理？**  
A：确保 `docker-compose.yml` 中包含 `extra_hosts` 配置，并确认代理服务（如 EasyConnect）已正确映射端口到宿主机。若使用容器名称，可改用 `--network` 参数共享网络。

---

## 🙏 致谢

- 本项目参考了 [TeamSUEP/SUEP-course-elect](https://github.com/TeamSUEP/SUEP-course-elect) 的认证与请求处理思路。
- 由 **DeepSeek** 辅助生成代码与文档，感谢 AI 的贡献。
- 如有任何问题或改进建议，欢迎提交 Issue 或 Pull Request。

---

## 📄 许可证

本项目采用 **MIT License**，详情请见 [LICENSE](LICENSE) 文件。  
使用本工具即表示您已理解并同意自行承担所有风险，作者不对因使用本工具造成的任何后果负责。
