"""auth.models —— 用户 / 角色 / 公司访问范围（方案 5.4 + 阶段 5 权限隔离）。

三种角色：
- ADMIN：全权限（读写任意公司、管理审计）；
- ANALYST：可读写其 company_scope 内的公司；
- VIEWER：只读其 company_scope 内的公司。

company_scope 语义：None 表示全部公司可访问；具体列表表示仅这些公司；
空列表 [] 表示无任何公司可访问。ADMIN 的 scope 恒为 None（全部）。

权限是「可选增强」：can_read / can_write 在 user=None 时返回 True（未启用权限
模式，行为与接入前一致）；一旦传入 user，即按角色 + 范围隔离。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Role(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class User(BaseModel):
    """系统用户。company_scope=None 表示全部公司；具体列表为可访问公司集合。"""

    user_id: str = Field(description="用户唯一 ID")
    name: str = Field(default="", description="显示名")
    role: Role = Role.VIEWER
    company_scope: list[str] | None = Field(
        default=None, description="可访问公司名列表；None=全部，[]=无"
    )

    def in_scope(self, company_name: str) -> bool:
        """判断某公司是否在用户可访问范围内。"""
        if self.role is Role.ADMIN or self.company_scope is None:
            return True
        return company_name in self.company_scope


class AccessPolicy:
    """访问策略：读 / 写权限判定。user=None 视为未启用权限（放行）。"""

    def can_read(self, user: User | None, company_name: str | None) -> bool:
        """读权限：能否查看某公司的数据。company_name=None 表示不针对具体公司。"""
        if user is None or company_name is None:
            return True
        return user.in_scope(company_name)

    def can_write(self, user: User | None, company_name: str | None) -> bool:
        """写权限：能否上传/修改某公司的数据。VIEWER 只读，不可写。"""
        if user is None or company_name is None:
            return True
        if user.role is Role.VIEWER:
            return False
        return user.in_scope(company_name)
