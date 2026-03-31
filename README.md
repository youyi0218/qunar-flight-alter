# qunar-flight-alter

去哪儿机票价格监控脚本。

## 功能

- 读取 `cookie.json` 抓取去哪儿单程机票页
- 识别航司、航班号、出发/到达时间、跨天到达、机场、总耗时、价格
- 价格按从低到高排序
- 每条行程一条通知
- 命中预期价时：推送该行程下所有命中的机票
- 未命中预期价时：推送提醒文案，并附带该行程最低价的 3 张机票
- 支持 PushPlus HTML 推送 + Resend 邮件推送
- 支持定时服务模式

## 准备

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 准备浏览器  
   支持系统已安装的 Chromium / Chrome，也可在 `config.json -> browser.executable_path` 指定路径。

3. 导出 cookie  
   `cookie.json` 使用浏览器插件 **Cookie-Editor** 导出。

4. 复制配置

```bash
cp config.example.json config.json
```

## 配置

主要修改：

- `pushplus.token`
- `email.api_key`
- `email.from`
- `email.to`
- `service.schedule_times`
- `routes`

标题格式：

```text
当日日期 航程 机票日期
```

例如：

```text
03月31日 武汉→海口 2026-05-05
```

## 运行

单次执行：

```bash
python flight_monitor.py
```

仅抓取不推送：

```bash
python flight_monitor.py --dry-run --dump-json
```

定时服务：

```bash
python flight_monitor.py --service
```

## Docker

### 本地构建

```bash
docker build -t qunar-flight-alter .
docker run --rm \
  --user root \
  -v $(pwd):/app \
  qunar-flight-alter
```

### 使用 GitHub Action 构建好的镜像部署

工作流会构建并可推送镜像到：

```text
ghcr.io/youyi0218/qunar-flight-alter:latest
```

拉取并运行：

```bash
docker pull ghcr.io/youyi0218/qunar-flight-alter:latest
docker run -d \
  --name qunar-flight-alter \
  --restart unless-stopped \
  --user root \
  -v $(pwd):/app \
  ghcr.io/youyi0218/qunar-flight-alter:latest
```

### Docker Compose 部署

仓库已提供 `docker-compose.yml`，会把**当前项目目录**整体映射到容器 `/app`。

因此这些文件都会直接在项目根目录中读写，不再单独使用 `data/`：

- `config.json`
- `cookie.json`
- `.flight_monitor_history.json`
- `.flight_monitor_state.json`

首次部署：

```bash
cp config.example.json config.json
# 再把你自己的 cookie.json 放到当前目录
docker login ghcr.io -u youyi0218
docker compose pull
docker compose up -d
```

查看日志：

```bash
docker compose logs -f
```

停止：

```bash
docker compose down
```

如果你的环境使用旧版命令，也可以把 `docker compose` 改成 `docker-compose`。

容器默认启动命令：

```bash
python /app/flight_monitor.py --service
```

## GitHub Actions

已提供 Docker 构建工作流：

- `push`
- `workflow_dispatch`
