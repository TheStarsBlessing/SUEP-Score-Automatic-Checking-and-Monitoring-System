# SUEP 成绩自动查询与监控系统

本项目是一个用于 **上海电力大学（SUEP）** 学生的成绩自动查询与变动监控工具，支持 **桌面 GUI**、**Web 管理界面** 和 **Docker 长期服务** 三种运行方式。

它能够定时抓取教务系统的成绩数据，检测新增或修改的课程，并通过多种方式（邮件、Pushplus、Server酱、企业微信、Telegram、Bark、桌面弹窗等）向用户发送通知，让你第一时间掌握成绩动态。

> **⚠️ 重要提示**
> 本工具仅供个人学习与参考，**请勿用于任何商业或非法用途**。使用前请确保你已阅读并遵守上海电力大学的相关网络与信息系统使用规定。

> **🔐 安全提示（务必先读）**
> 1. `config.json` 里要填你的**统一身份认证账号密码**，属于机密。本仓库只保留**空模板**，
>    本地运行后它会被写成真实值，**提交前请确认机密字段是空的**（`.gitignore` 已忽略
>    cookie / 成绩 / 日志，但按项目约定 `config.json` 仍受版本控制）。
> 2. Web 界面默认**没有任何鉴权**，任何能访问该端口的人都能读到你的学号、密码和推送 Token。
>    请在设置里填写 **Web 访问口令**，或让 `web_host` 只监听 `127.0.0.1`，并配合反向代理 + HTTPS。
> 3. 历史版本曾把真实账号密码、CAS 会话票据和完整成绩单提交进本仓库，历史已被重写清理。
>    **如果你的密码曾经出现在这里，请立即去教务系统修改密码，并重置各推送服务的 Token。**

---

## ✨ 功能特点

- 🔐 **自动登录**：通过学校统一身份认证（CAS）自动登录，支持 Cookie 缓存与失效自动重登。
- 📊 **成绩抓取**：解析教务系统成绩页面，结构化提取课程信息（学分、成绩、绩点等）。
- 🗓️ **学期列表联网获取**：从教务系统爬取全部可选学年学期（含真实 `semesterId`），
  在界面里以**下拉列表**选择，不再依赖写死的换算公式。
- 🔄 **变动检测**：对比历史成绩，自动识别 **新增课程**、**成绩修改**，并按
  「课程代码 + 课程序号 + 学期」区分重修等重复课程。
- 📨 **多渠道通知**：邮件、Pushplus、Server酱、企业微信、Telegram、Bark、桌面弹窗，可同时启用。
- 🖥️ **桌面 GUI**：Tkinter 图形界面；网络请求全部在后台线程，界面不会卡死；
  设置窗口支持滚动条与自由缩放。
- 🌐 **Web 管理界面**：Flask 网页控制台，支持远程访问、查看日志、手动查询、配置修改、可选的访问口令。
- ⏱️ **定时监控**：自定义查询间隔，后台自动运行，异常时重试并发送错误提醒。
- 🐳 **Docker 一键部署**：支持 Docker Compose，与 EasyConnect 容器配合实现校外访问。

---

## 🖥️ 界面参考

- 桌面版
<img width="1667" height="1194" alt="2026-07-01_011842" src="https://github.com/user-attachments/assets/270b9fe2-4c32-4b7c-b83f-3d6cd0935b02" />

- 网页版
<img width="3000" height="4361" alt="Screenshot_20260630_194221_com_trim_app_MainActivity" src="https://github.com/user-attachments/assets/d4c4a941-e75d-4a01-ab64-e1081278e362" />

---

## 📁 目录结构

三个版本各自独立、可以单独拷走部署；`ids.py` / `grade_fetcher.py` / `notifier.py` /
`config_manager.py` 在三个目录里是**同一份代码**（只有 `config_manager.py` 顶部的
`_SUBDIR` 一行不同：docker 版是 `"data"`，另两版是 `""`），改一处请三处同步。

```
.
├── README.md
├── LICENSE
├── Attention                        # 首次使用注意事项
├── .gitignore
└── 上海电力大学成绩查询和通知系统/
    ├── 桌面版/                       # Tkinter 桌面程序（数据就在本目录）
    │   ├── SUEP成绩监控.exe          #   ★ 已打包好的 Windows 可执行文件，双击即用
    │   ├── grade_gui.py             #   源码入口：python grade_gui.py
    │   ├── ids.py                   #   CAS 认证
    │   ├── grade_fetcher.py         #   抓取 / 解析 / 落盘 / 变动检测 / 学期列表
    │   ├── notifier.py              #   多渠道通知
    │   ├── config_manager.py        #   配置与路径（_SUBDIR = ""）
    │   ├── config.json              #   配置模板（机密字段为空）
    │   ├── icon.ico                 #   图标
    │   ├── build_exe.bat            #   重新打包 exe 的脚本
    │   ├── requirements.txt
    │   └── start.bat
    ├── 网页版/                       # Flask 网页版（数据就在本目录）
    │   ├── app.py                   #   入口：python app.py，默认 0.0.0.0:15029
    │   ├── templates/index.html
    │   ├── config.json
    │   ├── requirements.txt
    │   └── start.bat
    └── docker版/                     # 容器长期服务（数据在 data/）
        ├── app.py                   #   与网页版同一份代码
        ├── config_manager.py        #   _SUBDIR = "data"
        ├── data/config.json         #   实际读取的配置（模板，机密字段为空）
        ├── config.json              #   仅作参考的模板（程序不读它）
        ├── Dockerfile
        ├── docker-compose.yml
        ├── docker-compose-easyconnect.yml
        ├── templates/index.html
        └── requirements.txt
```

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.8 或更高版本
- 网络可访问上海电力大学教务系统（`ids.shiep.edu.cn`、`jw.shiep.edu.cn`，校外需 VPN）
- （可选）Docker 与 Docker Compose

### 2. 克隆项目

```bash
git clone https://github.com/TheStarsBlessing/SUEP-Score-Automatic-Checking-and-Monitoring-System.git
cd SUEP-Score-Automatic-Checking-and-Monitoring-System/上海电力大学成绩查询和通知系统
```

### 3. 安装依赖

每个版本都有自己的 `requirements.txt`（内容基本一致）：

```bash
cd 桌面版   && pip install -r requirements.txt    # 桌面弹窗另需 win10toast
cd 网页版   && pip install -r requirements.txt
cd docker版 && pip install -r requirements.txt
```

> `pysocks` 是使用 SOCKS5 代理（EasyConnect）时的必需依赖。

### 4. 配置

首次运行会自动生成 `config.json`（统一用 UTF-8 写入）。最少只需要填两项：

| 配置项 | 说明 |
|--------|------|
| `username` | 学号 |
| `password` | 统一身份认证密码 |

其余保持默认即可，也可以之后在桌面「设置」或网页「设置」里改。

### 5. 运行

**桌面版（推荐：直接跑打包好的 exe，不需要装 Python）**

```text
把 桌面版/SUEP成绩监控.exe 放到一个**可写目录**（不要放 Program Files），双击运行。
首次运行会在 exe 旁边自动生成 config.json，填上学号密码再重启即可。
```

> * 所有数据（`config.json` / `cookies.txt` / `grade_data_*.txt` / `logs/`）都写在
>   **exe 所在目录**，删掉 exe 不会连带删数据，换目录时把整个文件夹一起搬。
> * 单文件 exe 每次启动会把运行库解包到 `%TEMP%`，某些杀毒软件可能**误报**，
>   加白名单即可；介意的话可以用源码运行或自行用 `桌面版/build_exe.bat` 重新打包。
> * 无界面自检（结果写进 exe 旁边的 `selftest_result.txt`，排查环境问题很有用）：
>   ```text
>   SUEP成绩监控.exe --selftest
>   ```

**桌面版（源码方式）**

```bash
python 桌面版/grade_gui.py
```

**网页版**（默认 `0.0.0.0:15029`）与 **Docker**

```bash
python 网页版/app.py
cd docker版 && docker-compose up -d          # 再访问 http://localhost:15029
```

---

## ⚙️ 配置项说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `username` / `password` | `""` | 学号 / 统一身份认证密码 |
| `semester_str` | `2025-2026.2` | 要监控的学期，格式 `学年-学年.学期`。建议用界面里的**学期下拉框**从教务系统选择 |
| `query_interval` | `300` | 定时查询间隔（秒），最小 30 |
| `retry_interval` | `60` | 单次查询失败后的重试间隔（秒） |
| `max_retries` | `2` | 失败重试次数；会话过期时会额外多给一次重登机会 |
| `connect_timeout` | `8` | TCP 连接超时（秒） |
| `read_timeout` | `20` | 读取超时（秒） |
| `ssl_verify` | `false` | 是否校验 TLS 证书。学校证书链通常不在系统信任库里，默认关闭 |
| `ca_bundle` | `""` | 指定 CA 文件/目录后**自动开启**证书校验（比 `ssl_verify` 优先） |
| `proxy_enabled` / `proxy_url` | `false` / `""` | 是否使用代理，如 `socks5://127.0.0.1:1080`（校外/docker 常用） |
| `web_host` / `web_port` | `0.0.0.0` / `15029` | 网页版与 docker 版的监听地址/端口 |
| `web_password` | `""` | Web 访问口令（HTTP Basic）。**留空 = 完全不鉴权**，强烈建议设置 |
| `log_retention_days` | `7` | 日志保留天数，超期自动清理（每天 03:00） |
| `notification_methods` | `[]` | 启用的通知方式列表，如 `["pushplus", "email"]` |
| 各通知方式专用配置 | | 见下表 |

### 环境变量覆盖（网页版 / docker 版）

| 环境变量 | 对应配置项 |
|----------|------------|
| `PROXY_ENABLED` / `PROXY_URL` | `proxy_enabled` / `proxy_url` |
| `QUERY_INTERVAL` / `RETRY_INTERVAL` / `MAX_RETRIES` | 同名配置项 |
| `SEMESTER_STR` | `semester_str` |
| `WEB_HOST` / `WEB_PORT` / `WEB_PASSWORD` | 同名配置项 |
| `LOG_RETENTION_DAYS` | `log_retention_days` |

优先级：**环境变量 > 界面保存的值 > 默认值**。环境变量只作用于本次运行，不会写回 `config.json`。

---

## 📨 通知方式详解

在 `notification_methods` 列表里填入标识符即可启用（可多选，任一失败不影响其它）：

| 方式 | 标识符 | 所需配置字段 |
|------|--------|--------------|
| 邮件 | `email` | `smtp_server`, `smtp_port`, `sender_email`, `sender_password`, `receiver_email` |
| Server酱 | `serverchan` | `serverchan_token`（SendKey） |
| Pushplus | `pushplus` | `pushplus_token` |
| 企业微信机器人 | `wework` | `wework_webhook` |
| 桌面弹窗 | `desktop` | 无需额外配置，需 `win10toast`（服务器上建议不要启用） |
| Telegram | `telegram` | `telegram_bot_token`, `telegram_chat_id` |
| Bark（iOS） | `bark` | `bark_key`（可选 `bark_server` 自建服务地址） |

> 网页/桌面界面上都有「测试通知」按钮，可以先用它验证配置。
> 通知失败的原因会写进程序日志（不再是只 print 到控制台）。

---

## 🐳 Docker 部署

### 文件说明

- `docker-compose.yml`：构建并运行 `grade-monitor`（Web 界面 + 定时监控）。
- `docker-compose-easyconnect.yml`：运行 [hagb/docker-easyconnect](https://hub.docker.com/r/hagb/docker-easyconnect)，
  提供 SOCKS5 代理（`1080`）与 noVNC（`5910`），供校外访问教务系统。

### 快速启动（含 EasyConnect 代理）

```bash
# 1. 先起 VPN 容器，然后通过 http://宿主机IP:5910 登录 VPN 账号
docker-compose -f docker-compose-easyconnect.yml up -d

# 2. 再起监控容器（compose 里已设 PROXY_ENABLED=true / PROXY_URL=socks5://host.docker.internal:1080）
docker-compose up -d
```

访问 `http://localhost:15029`，在界面里填学号、密码、通知方式，**并把「Web 访问口令」设上**。

> 使用前请把 `docker-compose-easyconnect.yml` 里的 `PASSWORD=1234` 改成你自己的
> noVNC 密码，并把卷路径 `/vol2/...` 改成你自己宿主机上的目录。

### 数据持久化

- `./data` → `/app/data`：`config.json`、`cookies.txt`、`grade_data_*.txt`
- `./logs` → `/app/logs`：`web.log`

---

## 🔧 常见问题

**Q：登录失败怎么办？**
A：确认账号密码正确、网络能访问 `ids.shiep.edu.cn` 与 `jw.shiep.edu.cn`（校外需 VPN）。
程序会区分「账号密码错误」和「流程本身走不通」，具体原因写在日志里。
若认证页面改版（例如启用前端密码加密 `pwdDefaultEncryptSalt`），程序会明确报错而不是假装失败。

**Q：查询成功但一条成绩都没有？**
A：教务系统对「本学期确实没有成绩」和「semesterId 不存在」返回的是**同一个提示页**，
程序按「暂无成绩」处理并会在日志里记下页面提示。请先用学期下拉框确认学期选对了。

**Q：会话过期了会怎样？**
A：教务系统在未登录时返回的是 HTTP 200 的登录页。旧版本会把它当成「查询成功但没成绩」，
于是**永远**查不到成绩也不重登；现在会识别出来、自动重新登录后重试。

**Q：如何让 Web 界面安全一点？**
A：设置 `web_password`（浏览器会弹登录框）；或把 `web_host` 改成 `127.0.0.1` 只允许本机访问；
跨网络访问请套反向代理并启用 HTTPS（HTTP Basic 在明文 HTTP 上可被抓包）。

**Q：桌面版查询时界面会卡住吗？**
A：不会。所有网络请求与重试等待都在后台线程，界面只通过队列更新。

**Q：成绩解析不到 / 解析错了？**
A：可能是教务系统页面结构变化。程序会区分「页面结构变化」（报错）与「真的没有成绩」，
请把日志里的报错信息提 Issue。

**Q：如何更改监听端口？**
A：改配置项 `web_port`（或环境变量 `WEB_PORT`），或在 Docker 里改端口映射。

---

## 📝 更新记录

### 2026-09-19

**新增**

- **桌面版打包成 exe**：`桌面版/SUEP成绩监控.exe`（单文件、带图标、双击即用，不需要装 Python）。
  同时提供 `桌面版/build_exe.bat` 可随时重新打包。
- 桌面版新增**无界面自检**：`SUEP成绩监控.exe --selftest`，把登录/学期/成绩/落盘结果写进
  `selftest_result.txt`，方便排查"换台机器就跑不起来"这类环境问题。
- **学年学期列表从教务系统爬取**，界面里以**下拉列表**选择（详见下面"功能缺陷"第 3 条）。
- 桌面版设置窗口加**滚动条**并可自由缩放；窗口/列宽/行高按系统缩放比自适应。
- Web 界面新增可选**访问口令**（HTTP Basic），未设置时启动日志会明确告警。

**安全**

- Web 界面 `debug=True` → `debug=False`。Werkzeug 调试器暴露在 `0.0.0.0` 上等同于开放远程代码执行。
- 新增可选 **Web 访问口令**（HTTP Basic）。
- `SECRET_KEY` 不再硬编码，改为每次启动随机生成。
- 所有校园网请求加上超时（此前网络卡死会永久挂起调用线程，网页版甚至会卡在启动阶段）。
- 支持通过 `ca_bundle` 恢复 TLS 证书校验（此前一律 `verify=False`）。
- 清理仓库内泄露的 `cookies.txt`（CAS 票据）、`grade_data*.txt`（完整成绩单）、
  `logs/`、`__pycache__/`，并新增 `.gitignore`；`config.json` 清空为模板。

**功能缺陷**

- 修复**登录失败**：组装 CAS 表单时把没有 `value` 的复选框写成了空字符串，
  于是多提交了 `rememberMe=`，学校 CAS 会直接拒绝登录。现在保留 `None` 让 requests 丢弃该字段。
- 修复**会话过期被当成「查询成功但无成绩」**：未登录时教务系统返回 HTTP 200 的登录页。
  现在按最终 URL / 页面特征识别为会话过期并自动重登。
- 修复 **`semesterId` 换算公式错误**：实测教务系统的权威学期列表显示，
  `2024-2025.2=364 每学期+20` 只对部分学期成立（2023-2024.1 真实 284 而公式给 304、
  2023-2024.2 真实 304 而公式给 324、2019-2020.2 真实 163 而公式给 164）。
  现在改为**从教务系统爬取学期列表取真实 ID**，公式仅作离线兜底，并会用返回行的
  「学年学期」列反查一致性。
- 修复**打包成 exe 后数据写错位置**：PyInstaller 单文件模式把代码解包到 `%TEMP%\_MEIxxxx`，
  此时 `__file__` 指向临时目录，配置/Cookie/成绩会被写进去并在退出时丢失。
  现在冻结运行时改用 **exe 所在目录** 作为数据目录。
- 修复网页版 `POST /api/config` 的 `NameError`（引用了未导入的 `config_manager`），
  提交未知字段时保存设置会 500。
- 修复桌面版**查询时界面卡死**：网络请求与 `time.sleep` 移出 Tk 主线程。
- 修复桌面版把「良 / 通过 / 优秀 / 合格」这类非数字成绩误报为「补考要加油」。
- 修复 `IdsAuth` 的 Session / Cookie 是**类属性**导致实例间共享（「删除 Cookie 再重登」实际没清空）。
- 修复 Cookie 文件解析遇到含 `=` 的值（base64/JWT）会 `IndexError` 并静默退化。
- 修复变动检测主键漏掉「课程序号」，导致同学期重修的两条记录互相覆盖。
- 修复 `detect_changes` 不处理删除、桌面版与网页版成绩文件名不一致（并存兼容旧命名）。
- 修复并发问题：手动查询与定时任务现在串行执行（此前共用 Session、同时写同一文件）。
- 修复环境变量启动时被写回 `config.json` 永久覆盖用户设置。
- 成绩文件改为原子写入（临时文件 + `os.replace`），避免崩溃时丢历史。

**界面**

- 桌面版设置窗口加滚动条、支持自由缩放（原固定 520x780，高 DPI/小屏下按钮会被挤出屏幕）。
- 桌面版与网页版的学年学期改为**从教务系统获取的学期下拉列表**。
- 修复**高 DPI 下成绩表格行高不足、文字被裁**：开启 DPI 感知后字体按真实 DPI 放大，
  但 `ttk.Treeview` 的默认行高仍是未缩放的固定像素。现在行高按字体实际行高计算
  （实测 175% 缩放：字体行高 28px → 行高 42px），窗口/列宽也按缩放比放大并夹在屏幕内。
- 密码框掩码显示；日志自动截断；按钮/状态在后台任务期间正确禁用。

**结构调整**

- 网页版与 docker 版合并为**同一份 `app.py`**，两个模板也统一（保留各自的代理设置项）。
- 删除从未被引用的 `envconfig.py`。

### 2026-07-01

- 修正了一些已知问题：无法访问目标网站时仍能打开管理页面；查询失败或结果为空时不写入文件；
  新增日志清理功能与最新查询时间显示。

---

## 🙏 致谢

- 本项目参考了 [TeamSUEP/SUEP-course-elect](https://github.com/TeamSUEP/SUEP-course-elect) 的认证与请求处理思路。
- 由 **DeepSeek** 辅助生成代码与文档，感谢 AI 的贡献。
- 如有任何问题或改进建议，欢迎提交 Issue 或 Pull Request。

---

## 📄 许可证

本项目采用 **MIT License**，详情请见 [LICENSE](LICENSE) 文件。
使用本工具即表示您已理解并同意自行承担所有风险，作者不对因使用本工具造成的任何后果负责。
