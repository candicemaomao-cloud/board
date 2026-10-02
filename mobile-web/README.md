# P&L Board Mobile

独立的 React 手机网页，共用现有 FastAPI 后端。桌面 Vue 前端不受影响。

```bash
npm install
npm run dev
```

`npm run dev` 会先检查 `8001` 端口：FastAPI 已运行时直接复用，否则自动启动现有后端；随后在 `0.0.0.0:5174` 启动 React。手机与电脑连接同一局域网后，用终端显示的 Network 地址访问。

项目要求 Node.js 20.19 以上。启动脚本会自动使用本机 `~/.nvm` 中已安装的新版本，避免旧版 Node 导致 Vite 启动后立即退出。

如只需启动手机前端，使用 `npm run dev:web`。开发服务器会把 `/api` 代理到 `http://127.0.0.1:8001`。远程开发可设置：

```bash
VITE_API_PROXY=https://api.example.com npm run dev
```

生产环境若前端与 API 不同域，构建时设置 `VITE_API_BASE=https://api.example.com`，并在 FastAPI/Nginx 配置对应 CORS。推荐生产环境仍由反向代理把 `/api` 转发至 FastAPI，以保持同源。

`../packages/api-client` 是 React Web 与未来 React Native 共享的数据层。RN 接入时只需把浏览器 TokenStore 替换成 SecureStore 适配器。
