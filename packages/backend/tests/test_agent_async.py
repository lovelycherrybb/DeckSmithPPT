"""测试 Agent 异步接口。"""
import pytest
import asyncio
from fastapi.testclient import TestClient
import sys
import os

# 确保导入正确的 app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from app.main import app

client = TestClient(app)

def test_agent_execute_async():
    """测试异步执行接口。"""
    # 创建测试文件
    import tempfile
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("这是一个测试文件，用于测试 Agent 异步接口。")
        temp_path = f.name
    
    try:
        payload = {
            "kind": "suggest_title",
            "files": [
                {"filename": "test.txt", "path": temp_path}
            ]
        }
        
        # 打印可用路由
        print("可用路由:")
        for route in app.routes:
            if hasattr(route, 'path'):
                print(f"  {route.path}")
        
        response = client.post("/api/agent/execute-async", json=payload)
        
        print(f"响应状态码: {response.status_code}")
        print(f"响应内容: {response.text}")
        
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "agent_type" in data
        assert data["agent_type"] == "claude"
        
        session_id = data["session_id"]
        
        # 测试查询会话状态
        response = client.get(f"/api/agent/sessions/{session_id}")
        assert response.status_code == 200
        status_data = response.json()
        assert "status" in status_data
        assert status_data["status"] in ["running", "completed", "failed"]
        
        # 测试流式输出接口
        response = client.get(f"/api/agent/sessions/{session_id}/stream")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        
    finally:
        # 清理临时文件
        os.unlink(temp_path)

def test_agent_execute_async_invalid_kind():
    """测试无效的 kind。"""
    payload = {
        "kind": "invalid_kind",
        "files": []
    }
    
    response = client.post("/api/agent/execute-async", json=payload)
    assert response.status_code == 400

def test_agent_session_not_found():
    """测试不存在的会话。"""
    response = client.get("/api/agent/sessions/nonexistent")
    assert response.status_code == 404

def test_agent_session_stream_not_found():
    """测试不存在的会话流式输出。"""
    response = client.get("/api/agent/sessions/nonexistent/stream")
    assert response.status_code == 404


def test_取消会话_置已取消且幂等_不存在404():
    """取消端点：running 会话 → cancelled + failed/已取消；重复取消幂等；无此会话 404。"""
    from app.agents.session_manager import session_manager

    s = session_manager.create_session("claude", "revise_pages")
    r = client.post(f"/api/agent/sessions/{s.session_id}/cancel")
    assert r.status_code == 200 and r.json() == {"ok": True}
    got = session_manager.get_session(s.session_id)
    assert got.cancelled is True
    assert got.status == "failed" and got.error == "已取消"

    # 幂等：对已取消会话再取消仍是 200、状态不变
    assert client.post(f"/api/agent/sessions/{s.session_id}/cancel").status_code == 200
    assert session_manager.get_session(s.session_id).status == "failed"

    assert client.post("/api/agent/sessions/nonexistent/cancel").status_code == 404
