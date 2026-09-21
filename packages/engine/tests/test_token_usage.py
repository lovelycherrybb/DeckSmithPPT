"""ADR-016 token 消耗归集单测：取值、累加、落 job.json 三层。

不发子进程、不碰网络：result 事件全部由用例伪造注入。

钉住四件事：
1. engine.core.usage 的三条不变式（分项 int|None、四项全缺=None、None 按单位元累加且被已知值覆盖）；
2. ppt_master / ppt_master_v6 的 _pump_run_stream 接住 result.usage 写 job.json，
   多个 result 分项**累加**不覆盖（这是 resume 场景少算钱的根源）；
3. claude_draft 的 JSON 输出解析：草稿正文与用量同时取回，解析不动时退回整段 stdout、
   用量按未记录处理——宁可少个数字，不能把已生成的草稿判成失败。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # packages/ 供 import engine

from engine.core.usage import USAGE_PARTS, add_usage, usage_from_result  # noqa: E402
from engine.impl.ppt_master.claude_draft import ClaudeDraftEngine  # noqa: E402
from engine.impl.ppt_master.engine import PptMasterEngine  # noqa: E402
from engine.impl.ppt_master_v6.engine import PptMasterV6Engine  # noqa: E402


def _usage(**parts) -> dict:
    return {"type": "result", "subtype": "success", "usage": parts}


# ── usage_from_result：防御性归一 ──
def test_四项齐报_总数为四项之和():
    u = usage_from_result(
        _usage(input_tokens=10, cache_creation_input_tokens=20,
               cache_read_input_tokens=30, output_tokens=40)
    )
    assert u == {"input_tokens": 10, "cache_creation_input_tokens": 20,
                 "cache_read_input_tokens": 30, "output_tokens": 40,
                 "total_tokens": 100}


def test_网关只报两项_cache为null不进总数():
    """本机第三方兼容网关的实测形态：cache_* 为 null，只有 input/output。"""
    u = usage_from_result(
        _usage(input_tokens=100, cache_creation_input_tokens=None,
               cache_read_input_tokens=None, output_tokens=50)
    )
    assert u["cache_read_input_tokens"] is None  # 未回报保持 None，不许冒 0
    assert u["total_tokens"] == 150


def test_四项全缺整体为None_绝不产出total0():
    assert usage_from_result(_usage()) is None
    assert usage_from_result({"type": "result"}) is None            # 没有 usage 键
    assert usage_from_result({"type": "result", "usage": "oops"}) is None  # usage 非 dict


def test_脏值按未回报处理_不被带偏总数():
    """bool/负数/非整 float/非数字串都不配进账；数字串与整 float 是网关序列化差异，认。"""
    u = usage_from_result(
        _usage(input_tokens=True, cache_creation_input_tokens=-5,
               cache_read_input_tokens=1.5, output_tokens="77")
    )
    assert u == {"input_tokens": None, "cache_creation_input_tokens": None,
                 "cache_read_input_tokens": None, "output_tokens": 77,
                 "total_tokens": 77}
    assert usage_from_result(_usage(input_tokens=12.0))["input_tokens"] == 12


# ── add_usage：跨 result 的分项累加 ──
def test_incoming为None保持已有账_首报None被续报覆盖():
    total = {"input_tokens": 10, "cache_creation_input_tokens": None,
             "cache_read_input_tokens": None, "output_tokens": 5, "total_tokens": 15}
    assert add_usage(total, None) is total  # 该次未上报：原样返回，不抹数字

    merged = add_usage(total, {"input_tokens": None, "cache_creation_input_tokens": 8,
                               "cache_read_input_tokens": 100, "output_tokens": 5})
    assert merged["input_tokens"] == 10            # None 不覆盖已知值
    assert merged["cache_read_input_tokens"] == 100  # 已知值覆盖 None（续跑报出来了）
    assert merged["output_tokens"] == 10           # 双已知才求和
    assert merged["total_tokens"] == 128


def test_total为None时取incoming副本_不共享引用():
    # 真实 incoming 恒为 usage_from_result 产物（必带派生 total_tokens）
    incoming = usage_from_result(_usage(input_tokens=1, output_tokens=2))
    out = add_usage(None, incoming)
    assert out["total_tokens"] == 3
    out["input_tokens"] = 99
    assert incoming["input_tokens"] == 1  # 是副本不是别名


def test_全None分项相加仍是None_而不是0():
    merged = add_usage({k: None for k in USAGE_PARTS}, {k: None for k in USAGE_PARTS})
    assert merged is None  # 四项全 None → 整体未记录


# ── pump 事件流：result.usage → job.json（两引擎同一段逻辑，参数化覆盖）──
@pytest.mark.parametrize("engine_cls", [PptMasterEngine, PptMasterV6Engine])
def test_pump把用量写进job_json且多result累加(tmp_path, engine_cls):
    eng = engine_cls(jobs_root=tmp_path / "jobs")
    job_id = "usagejob"
    eng._job_dir(job_id).mkdir(parents=True)  # 真实链路里 submit 建目录，_write_job 不做 mkdir  # noqa: SLF001
    eng._write_job(job_id, status="running")  # noqa: SLF001

    lines = "\n".join(
        json.dumps(x)
        for x in [
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "排版中"}}},
            _usage(input_tokens=100, cache_creation_input_tokens=10,
                   cache_read_input_tokens=0, output_tokens=40),
            # 第二个 result（--resume 续跑形态）：cache_read 这次才报出来
            _usage(input_tokens=50, cache_creation_input_tokens=None,
                   cache_read_input_tokens=999, output_tokens=20),
        ]
    )
    proc = SimpleNamespace(stdout=io.StringIO(lines + "\n"))

    class _NoClose(io.StringIO):  # pump 的 finally 会 close 日志流，留个尾巴让断言读得到
        def close(self): pass

    log = _NoClose()
    eng._pump_run_stream(job_id, proc, log)  # noqa: SLF001

    u = eng._read_job(job_id)["token_usage"]  # noqa: SLF001
    assert u["input_tokens"] == 150
    assert u["cache_creation_input_tokens"] == 10
    assert u["cache_read_input_tokens"] == 999  # None 被续报的已知值覆盖，不是 999+0
    assert u["output_tokens"] == 60
    assert u["total_tokens"] == 150 + 10 + 999 + 60
    # 日志即时行按**当段** result 计：首段 100+10+0+40=150；续跑段 50+999+20=1,069（千分位）
    assert "📊 token 消耗 150\n" in log.getvalue()
    assert "📊 token 消耗 1,069" in log.getvalue()
    assert "✅ 引擎执行完成" in log.getvalue()


@pytest.mark.parametrize("engine_cls", [PptMasterEngine, PptMasterV6Engine])
def test_pump遇到无用量result_不落token_usage键(tmp_path, engine_cls):
    """引擎/网关没报 usage：job.json 里不写 token_usage 键（未记录 ≠ 0）。"""
    eng = engine_cls(jobs_root=tmp_path / "jobs")
    job_id = "nousage"
    eng._job_dir(job_id).mkdir(parents=True)  # noqa: SLF001
    eng._write_job(job_id, status="running")  # noqa: SLF001
    proc = SimpleNamespace(stdout=io.StringIO(json.dumps({"type": "result", "subtype": "success"}) + "\n"))
    eng._pump_run_stream(job_id, proc, io.StringIO())  # noqa: SLF001
    assert "token_usage" not in eng._read_job(job_id)  # noqa: SLF001


def test_to_job带出用量_没记录时为None(tmp_path):
    eng = PptMasterEngine(jobs_root=tmp_path / "jobs")
    job_id = "jobout"
    eng._job_dir(job_id).mkdir(parents=True)  # noqa: SLF001
    usage = usage_from_result(_usage(input_tokens=3, output_tokens=4))
    eng._write_job(job_id, status="completed", title="t", token_usage=usage)  # noqa: SLF001
    assert eng._to_job(job_id, eng._read_job(job_id)).token_usage == usage  # noqa: SLF001
    # 未写过用量的任务：引擎 Job 上就是 None（未记录），调用方不得据此填 0
    eng._write_job(job_id, token_usage=None)  # noqa: SLF001
    assert eng._to_job(job_id, eng._read_job(job_id)).token_usage is None  # noqa: SLF001


# ── claude_draft：--output-format json 的正文/用量双取 ──
def test_草稿JSON输出_正文与用量同时取回():
    stdout = json.dumps(
        {"type": "result", "subtype": "success", "result": "# 标题\n正文",
         "usage": {"input_tokens": 7, "cache_creation_input_tokens": None,
                   "cache_read_input_tokens": 90, "output_tokens": 3}}
    )
    body, usage = ClaudeDraftEngine._parse_json_result(stdout)  # noqa: SLF001
    assert body == "# 标题\n正文"
    assert usage["total_tokens"] == 100 and usage["cache_creation_input_tokens"] is None


def test_草稿输出解析不动_退回整段stdout且用量未记录():
    """CLI 版本差异/网关改写输出：宁可少个用量数字，不能把已生成的草稿判成失败。"""
    plain = "# 直接就是 markdown\n正文"
    assert ClaudeDraftEngine._parse_json_result(plain) == (plain, None)  # noqa: SLF001
    # result 不是字符串：正文退回整段 stdout 老口径；但 usage 是解析成功的真数据，留账不丢
    weird = json.dumps({"result": {"不是": "字符串"}, "usage": {"input_tokens": 1}})
    body, usage = ClaudeDraftEngine._parse_json_result(weird)  # noqa: SLF001
    assert body == weird and usage == {"input_tokens": 1, "cache_creation_input_tokens": None,
                                       "cache_read_input_tokens": None, "output_tokens": None,
                                       "total_tokens": 1}


def test_草稿空stdout不炸():
    assert ClaudeDraftEngine._parse_json_result("") == ("", None)  # noqa: SLF001
