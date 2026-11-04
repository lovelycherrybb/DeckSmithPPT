"""任务失败时给用户的句子（按 run.log 线索分流，不写成一句万用的"请重试"）。

为什么单独成模块：三个引擎都可能撞上同一类故障（AI 服务地址不通、模型名称不认识、
写入中断）。句子在三个引擎里各写一份必然漂移，而这些句子是用户照着决定"再试一次"
还是"去找管理员"的依据——写歪一次，用户就在错方向上白等一轮。

判据来源是 2026-11-04 的一次真实失败：开发机的服务地址还留着占位值，任务根本连不上
AI 服务，界面却显示"生成中断，未得到结果文件，请重试"。用户除了反复点重新生成，
什么也判断不出来。
"""
from pathlib import Path

#: 模型名称不被服务端接受
MODEL_NAME_SIGNS = ("unrecognized_model", "model not found", "does not exist", "invalid model")
#: 连不上服务（地址写错、网络不通、域名解析不了）。
#: 必须查日志、不能只看 AI 返回的状态：上游会在长响应中途掐连接，会话里留下
#: "API Error: terminated"，而 stream 的 result 仍报 subtype=success——只看 result
#: 会把网络故障误判成"AI 没干活"（guizang 实测教训，原 _API_ERROR_MARKERS 的判据）。
CONNECTIVITY_SIGNS = ("can't reach the api server", "enotfound", "econnrefused",
                      "getaddrinfo", "check your internet", "terminated",
                      "socket hang up", "econnreset", "api error")

#: 判不出具体类型时的兜底句：仍然要给可做的动作，不能只说"失败"
GENERIC = "生成中断，未得到结果文件，请重试"
MODEL_NAME = "AI 服务不认识当前配置的模型名称，请联系管理员检查设置"
UNREACHABLE = "无法连接 AI 服务，请检查网络后重试；若反复出现请联系管理员"


def read_run_log(job_dir) -> str:
    """读任务目录里的 run.log；读不到就返回空串，绝不因日志缺失而让状态查询本身失败。"""
    try:
        return Path(job_dir, "run.log").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def no_result_reason(job_dir) -> str:
    """"进程结束但没有结果文件"时该对用户说的那句话。"""
    log = read_run_log(job_dir)
    if any(sign in log for sign in MODEL_NAME_SIGNS):
        return MODEL_NAME
    if any(sign in log for sign in CONNECTIVITY_SIGNS):
        return UNREACHABLE
    return GENERIC
