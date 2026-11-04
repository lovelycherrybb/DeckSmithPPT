"""失败句分流的自证：三类故障必须给三种说法，且每一条都要留下能做的下一步。

起因（2026-11-04 实测）：开发机的 AI 服务地址留着占位值，任务根本连不上，
界面却显示"生成中断，未得到结果文件，请重试"。用户除了反复点重新生成没有任何线索，
等于把配置故障伪装成偶发失败。这里钉住三件事：
  1. 连不上服务、模型名不对、其它中断——三种情况说法不同；
  2. 日志缺失时不许抛异常（状态查询本身不能因为读不到日志而失败）；
  3. 任何一条句子都必须带下一步动作（重试或找管理员），不许只报结果。
"""
from engine.core import failure_message as fm


def _write_log(tmp_path, text):
    d = tmp_path / "job-1"
    d.mkdir()
    (d / "run.log").write_text(text, encoding="utf-8")
    return d


def test_连不上服务要说检查网络而不是万用地重试(tmp_path):
    d = _write_log(tmp_path, '❌ API Error: Can\'t reach the API server — check your internet or DNS (ENOTFOUND)')
    assert fm.no_result_reason(d) == fm.UNREACHABLE


def test_模型名不认识要说找管理员(tmp_path):
    d = _write_log(tmp_path, '[claude-code:unrecognized_model] {"model":"glm-4.6"}')
    assert fm.no_result_reason(d) == fm.MODEL_NAME


def test_判不出类型时仍给兜底句(tmp_path):
    d = _write_log(tmp_path, '=== svg_to_pptx ===\nsome unrelated output')
    assert fm.no_result_reason(d) == fm.GENERIC


def test_日志缺失不抛异常(tmp_path):
    """读不到 run.log 时必须回兜底句——状态查询不能因为日志问题整个炸掉。"""
    assert fm.no_result_reason(tmp_path / "不存在的目录") == fm.GENERIC


def test_每一条句子都带着下一步动作():
    """三句都必须给出可执行的下一步，不许只报"失败"了事。"""
    for sentence in (fm.GENERIC, fm.MODEL_NAME, fm.UNREACHABLE):
        assert ("重试" in sentence) or ("联系管理员" in sentence), f"这句没给下一步：{sentence}"


def test_对照_模型名问题不会被误判成网络问题(tmp_path):
    """两条判据不能互相吞：日志里只有模型名标志时，不许说成"检查网络"。"""
    d = _write_log(tmp_path, 'unrecognized_model')
    assert fm.no_result_reason(d) == fm.MODEL_NAME
