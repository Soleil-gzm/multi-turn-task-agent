"""极简 `.env` 加载器（不引入 python-dotenv 依赖）。

约定：
- 读取仓库根目录 `.env`（UTF-8），只补齐当前进程未设置的变量，**不覆盖**已有环境变量
- 支持 `KEY=VALUE`、`export KEY=VALUE`、`#` 注释、空行、单/双引号包裹的值
- 解析失败的行直接忽略；不会让启动因为你手写的一行笔误而失败

凭证只应放在 `.env`（已在 .gitignore 忽略）或真实环境变量中，不写入 yaml / 报告 / 仓库。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_LOADED: set[Path] = set()


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """加载 `.env` 并返回实际生效的键值（已存在于环境中的不覆盖）。"""
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return {}
    resolved = env_path.resolve()
    if resolved in _LOADED:
        return {}
    _LOADED.add(resolved)

    applied: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, raw = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _parse_value(raw)
        applied[key] = os.environ[key]
    return applied
