"""auth —— 权限控制与审计日志（方案 5.4「校验用户权限、公司和报告范围」+ 阶段 5）。

企业级基础的两块：
- AccessPolicy：用户 / 角色 / 公司访问范围隔离（工具入口层校验）；
- AuditLog：审计日志（谁在何时对什么资源做了什么、结果如何），SQLite 持久化。

设计原则：
- 权限是「可选增强」：不传 user 时行为与启用前完全一致（向后兼容既有
  确定性骨架与测试）；一旦传入 user，即按角色 + 公司范围执行隔离。
- 审计日志与 AnalysisRun 分工不同：AnalysisRun 记录「一次分析怎么算出来的」
  （执行记录），AuditLog 记录「谁在何时访问/操作了什么」（安全审计轨迹）。
"""

from .models import AccessPolicy, Role, User
from .audit import AuditEvent, AuditLog

__all__ = [
    "AccessPolicy",
    "Role",
    "User",
    "AuditEvent",
    "AuditLog",
]
