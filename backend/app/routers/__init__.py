"""业务路由汇总：专用流程路由在前，通用资源路由在后（避免动态路径抢匹配）。"""
from __future__ import annotations

from app.routers import resource, rework

ROUTERS = [rework, resource]
