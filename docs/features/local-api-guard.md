# 本机接口来源校验（只允许本机页面调用）

## 一句话

API 只监听 `127.0.0.1` **不等于**只有本机能调用。所有 `/api` 路由（健康检查除外）
在进入业务逻辑前统一校验请求来源，挡住跨站 simple request 与 DNS rebinding。

## 代码入口

| 职责 | 位置 |
|---|---|
| 守卫实现（Host / Origin / Sec-Fetch-Site 三重判断） | `api/routers/local_guard.py` 的 `local_request` |
| 各路由挂载 | `api/routers/{search,library,history,spaces,trending,reading,research,extensions}.py` 的 `APIRouter(dependencies=[...])` |
| 回归测试 | `tests/test_local_origin_guard.py` |
| 测试进程的白名单 | 根目录 `conftest.py` 设置 `MC_API_ALLOWED_HOSTS` |

## 关键决定

- **为什么服务端必须自己判断**：CORS 限制的是浏览器**读响应**，不是发请求。
  任意网页都能发 `Content-Type: text/plain` 的 simple request（不触发预检），
  请求照发、副作用照发生 —— 删收藏、删空间、清登录态都会真的执行。
  DNS rebinding 更彻底：攻击者把 `evil.example` 解析到 127.0.0.1 后，
  页面与 API 同源，连响应都能读。
- **为什么校验 `Host` 而不是 `request.url.hostname`**：rebinding 场景下两者
  都来自攻击者域名，但 `Host` 是请求头、语义明确指向"你请求的是哪个主机"，
  且不受代理/反代改写路径影响。回环主机名清单见 `LOOPBACK_HOSTNAMES`。
- **Origin 缺失视为放行**：浏览器扩展的 Service Worker 请求不带 `Origin`，
  curl / 测试客户端也不带。带 `Origin` 且不是本机来源才拒绝。
- **`/api/health` 刻意不守卫**：启动器与托盘要在后端就绪前探测它。
- **`MC_ALLOW_NONLOCAL_API=1` 可整体关闭**（等价于允许 rebinding，仅调试用）；
  `MC_API_ALLOWED_HOSTS` 用来放行额外主机名（测试客户端默认 `testserver`）。

## 已知坑 / 边界

- 新增路由若自己 `APIRouter(...)` 而没挂 `Depends(local_request)`，就等于开了个后门。
  `tests/test_local_origin_guard.py::test_every_api_route_is_guarded` 会逐个断言
  （`/api/health` 显式豁免），漏挂会直接红。
- 守卫只在**路由级**生效；`api/main.py` 的静态文件兜底 `serve_frontend_path`
  不在此列 —— 它只返回构建产物，没有副作用。
- 打包版若将来改成监听 `0.0.0.0`，这套判断仍然成立（Host 白名单会先挡下外部域名），
  但届时还应叠加鉴权或绑定回环。

## 测试怎么跑

```shell
.venv/Scripts/python.exe -m pytest tests/test_local_origin_guard.py -q --basetemp=.tmp_pytest_guard
```

该文件锁三件事：所有 `/api` 路由都挂了守卫（健康检查显式豁免）、攻击形态
（外部 `Host`、外部 `Origin`、`Sec-Fetch-Site: cross-site`）一律 403、
正常形态（本机页面、Vite dev server、扩展 Service Worker 不带 `Origin`）放行。

`tests/test_research.py` 与 `tests/test_extensions.py` 里另有针对研究/扩展路由的
来源断言，走的是同一份实现。
