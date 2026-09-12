# shot-api

把 [shot-scraper](https://shot-scraper.datasette.io/) 封装成 HTTP API：截图 → 上传 Cloudflare R2 → 返回公开 URL。

## 接口

### `POST /shot`

Header: `Authorization: Bearer ***

`url`、`width`、`height` 为必填字段。

```json
{
  "url": "https://example.com",
  "format": "png",          // png | jpeg | pdf
  "width": 1280,            // 必填，100–4000
  "height": 800,            // 必填，100–20000
  "selectors": ["#main"],   // 只截取匹配元素的区域
  "selector_all": ".card",
  "js": "document.body.style.background='pink'",
  "wait": 2000,             // 页面 load 后再等 2000ms
  "wait_for": "document.querySelector('#content')",
  "quality": 80,            // jpeg
  "retina": true,           // 2x
  "scale_factor": 1.5,
  "user_agent": "...",
  "timeout": 30            // 秒
}
```

响应：

```json
{
  "url": "https://cdn.example.com/shot_api/2025/01/15/ab12cd.png",
  "key": "shot_api/2025/01/15/ab12cd.png",
  "content_type": "image/png",
  "size_bytes": 84213,
  "duration_ms": 2100
}
```

### `GET /healthz`

健康检查，无需鉴权。

## 环境变量

见 `.env.example`。部署时写入服务器（例如 `/etc/shot-api.env`），uvicorn 启动前 source 或 systemd `EnvironmentFile=`。

## 本地开发

```bash
pip install -r requirements.txt
shot-scraper install   # 安装 Chromium
set -a; source .env; set +a
uvicorn main:app --reload
```

## Docker

```bash
docker build -t shot-api .
docker run --env-file .env -p 8000:8000 shot-api
```

## 说明

- **并发**：`MAX_CONCURRENCY` 信号量限制同时运行的 Chromium 数量，超出的请求排队。
- **尺寸**：`width` / `height` 必填（视口尺寸即截图尺寸），不再支持省略后全页截图；缺少时返回 422。
- **SSRF**：默认拒绝解析到私有/保留 IP 的域名；`ALLOW_PRIVATE_IPS=true` 关闭（不建议）。
- **R2 清理**：截图默认永久保存，建议在 R2 bucket 配 lifecycle 规则自动删除（如 7 天）。
- **Caddy**：可反代到本服务，例如 `shot.example.com { reverse_proxy 127.0.0.1:8000 }`。
