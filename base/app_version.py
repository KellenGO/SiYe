# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
# This file is part of MediaCrawler project.
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""应用版本号的单一来源。

历史上一版号写在五处（`pyproject.toml`、`api/main.py` 两处、
`api/services/environment_health.py`、`webui/package.json`），发版时改一个漏一个。
这里只留一处 Python 常量，另外两处由 `tests/test_repo_hygiene.py` 盯着对齐：

- ``pyproject.toml`` —— 打包脚本读取它并写入 ``RELEASE_VERSION``，安装器再读取该文件；
- ``webui/package.json`` —— 前端版本，用于前后端版本匹配检查。

改版本的顺序是：**先改这三处 → 提交 → 再打 tag**。`scripts/build_exe.ps1`
会在 tag 构建时校验 tag 与 `pyproject.toml` 一致，顺序反了会白跑一轮 CI。
"""

from __future__ import annotations

#: 当前版本号（与 pyproject.toml、webui/package.json 保持一致）。
APP_VERSION = "1.0.0"
