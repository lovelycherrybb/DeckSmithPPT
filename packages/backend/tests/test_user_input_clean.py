"""用户信息文本字段入库前的清洗口径。

钉住两件事：

1. 昵称带前导空格会让界面头像取到空格、渲染成空白圆（实测三个账号如此），用户名旁边
   还多一段空隙。手机号、用户名同属"空白无业务含义"的文本，一律剥首尾空白。

2. 只剥离、不把空串折成 None。改资料接口靠 None 区分"这个字段没传"与"要清空它"
   （仓储 update 是 `if value is not None` 才赋值），折成 None 会把用户的清空动作
   悄悄吞掉——这条是我自己先写错、复查时才抓到的，所以单独钉一个用例。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.core.schemas import (  # noqa: E402
    ChangePasswordRequest,
    LoginRequest,
    UserCreate,
    UserUpdate,
)


# ── 该洗的洗掉 ──

def test_新建用户时三个文本字段都剥首尾空白():
    body = UserCreate.model_validate({
        "username": "  wang  ", "nickname": "\n刘婷 ", "phone": " 13800000000\t",
    })
    assert body.username == "wang"
    assert body.nickname == "刘婷"
    assert body.phone == "13800000000"


def test_改资料时同样剥空白():
    body = UserUpdate.model_validate({"nickname": " 石莉 ", "phone": " 13911112222 "})
    assert body.nickname == "石莉"
    assert body.phone == "13911112222"


# ── 不能把「清空」吞成「没改」 ──

def test_传空白昵称仍然是空串而不是没传():
    """仓储 update 见 None 就不赋值。折成 None 会让用户清空昵称的操作静默失效。"""
    body = UserUpdate.model_validate({"nickname": "   "})
    assert body.nickname == "", "空串必须原样保留，才能表达『把这个字段清空』"


def test_传空白手机号同样保留为空串():
    assert UserUpdate.model_validate({"phone": "  "}).phone == ""


def test_没传字段保持没传():
    """与上一条配对：没传是 None，传了空白是空串，两种意图都不能被清洗混为一谈。"""
    assert UserCreate.model_validate({"username": "qian"}).nickname is None
    assert UserUpdate.model_validate({"is_vip": True}).nickname is None
    assert UserUpdate.model_validate({"is_vip": True}).phone is None


# ── 不该动的别动 ──

def test_正常值一字不改():
    body = UserCreate.model_validate({"username": "sun", "nickname": "孙策"})
    assert (body.username, body.nickname) == ("sun", "孙策")


def test_清洗不放宽原有的必填约束():
    with pytest.raises(ValidationError):
        UserCreate.model_validate({"nickname": "无用户名"})


def test_密码绝不参与清洗():
    """密码里的空格是内容。登录与改密两个模型必须保持原样，不能被顺手挂上剥离。"""
    login = LoginRequest.model_validate({"username": " manager ", "password": " pa ss "})
    assert login.password == " pa ss "

    change = ChangePasswordRequest.model_validate({"old_password": " a b ", "new_password": "  c"})
    assert change.old_password == " a b "
    assert change.new_password == "  c"
