"""Agent 设置的业务层（ADR-032）。

四件事：档案增删改查、"激活即写配置文件"、配置文件的读（脱敏）与写（护栏）、网关连通探针。

三条不可让的口径：

1. **真源只有一个**：哪套档案生效看档案表的 `is_active`。本层不缓存、不在系统设置里另存一份，
   界面要显示生效哪套，从这里问，别处不许有第二个答案。
2. **明文不出后端**：凭据只在两处存在——库里、写进配置文件那一刻。接口一律脱敏，
   日志与错误信息不得带值。
3. **没有生效档案时一个字都不写**：老部署升级完代码，行为与升级前完全一致（继续走 `.env`）。
   新功能上线不该改变现网的出片路径，这是兼容的底线（ADR-032 决策 6）。
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from ..core import agent_files
from ..core.agent_files import MASK
from ..core.agent_registry import AgentSpec, agent_ids, agent_spec
from ..repository.providers import AgentProviderRepo, decode_values

logger = logging.getLogger("decksmith.agents")

#: 值长度上限：地址和模型名都不该是长篇，超了基本是粘错了东西（把整段文档贴进来了）
MAX_VALUE_LEN = 500

PROBE_TIMEOUT_SECONDS = 20


def _clean(values: Any) -> dict[str, str]:
    """把提交值归一化成 {字段名: 去空白的字符串}，None 视为清空。"""
    out: dict[str, str] = {}
    if not isinstance(values, dict):
        return out
    for key, raw in values.items():
        if raw is None:
            out[str(key)] = ""
            continue
        if isinstance(raw, bool):
            out[str(key)] = "true" if raw else ""
        else:
            out[str(key)] = str(raw).strip()
    return out


class AgentSettingsService:

    def __init__(self) -> None:
        self.repo = AgentProviderRepo()

    # ── 注册表视图（GET /api/admin/agents）──
    def list_agents(self, db) -> dict:
        """整份响应只由注册表遍历生成——本函数里不许出现任何一个写死的 agent 名。"""
        agents = []
        for aid in agent_ids():
            spec = agent_spec(aid)
            active = self.repo.active(db, aid)
            agents.append({
                "id": spec.id,
                "label": spec.label,
                # 版本探不到就回 null，界面显示"未知"；拿构建期参数冒充"当前生效版本"是谎报
                "cli_version": self._detect_cli_version(),
                "active_provider_id": active.provider_id if active else None,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "type": f.type,
                        "env_var": f.env_var,
                        "required_for_gateway": f.required_for_gateway,
                        "secret": f.is_secret,
                        "description": f.description,
                        "restart_required": f.restart_required,
                    }
                    for f in spec.fields
                ],
                "files": [
                    {
                        "name": f.name,
                        "path": str(f.path()),
                        "editable": f.editable,
                        "exists": f.path().exists(),
                    }
                    for f in spec.files
                ],
            })
        return {"agents": agents}

    def _detect_cli_version(self) -> str | None:
        """问 CLI 自己要版本号；问不到就是问不到，不猜。"""
        import shutil
        import subprocess

        binary = shutil.which("claude")
        if not binary:
            return None
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                  timeout=10)
        except (subprocess.SubprocessError, OSError):
            return None
        text = (proc.stdout or proc.stderr).strip()
        return text or None

    # ── 档案 ──
    def list_providers(self, db, agent_id: str | None = None) -> dict:
        rows = self.repo.list(db, agent_id)
        profiles = [self._profile_out(agent_spec(r.agent_id), r) for r in rows]
        active_id = next((p["id"] for p in profiles if p["is_active"]), None)
        return {"profiles": profiles, "active_provider_id": active_id}

    def create_provider(self, db, body: dict, operator_id: str | None) -> dict:
        spec = self._spec_or_400(body.get("agent_id"))
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="请为该配置填写名称，便于切换时识别")
        values = self._validated(spec, _clean(body.get("values")))
        row = self.repo.create(db, spec.id, name, values, operator_id)
        return self._profile_out(spec, row)

    def update_provider(self, db, provider_id: str, body: dict,
                        operator_id: str | None) -> dict:
        row = self.repo.get(db, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到该配置")
        spec = self._spec_or_400(row.agent_id)

        submitted = _clean(body.get("values"))
        merged = {**decode_values(row.values)}
        # 部分提交：只动出现在本次请求里的字段（与系统设置同一口径）
        merged.update(submitted)
        values = self._validated(spec, merged)

        name = body.get("name")
        name = str(name).strip() if name is not None else None
        if name is not None and not name:
            raise HTTPException(status_code=400, detail="请填写配置名称")

        saved = self.repo.save_values(db, provider_id, name, values, operator_id)
        out = self._profile_out(spec, saved)
        # 改的正是生效中的那套 → 立刻重渲染，界面不能说"改了但还没生效"
        if saved.is_active:
            written = self._apply_profile(spec, values)
            out["warnings"] = written["warnings"]
        return out

    def delete_provider(self, db, provider_id: str) -> None:
        row = self.repo.get(db, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到该配置")
        if row.is_active:
            # 删掉正在用的那套 = 把系统推到"没有配置"，那不是管理员点"删除"时想要的东西
            raise HTTPException(
                status_code=409,
                detail=f"「{row.name}」正在使用中，不能删除。请先启用另一套配置，再删除这一套。")
        self.repo.delete(db, provider_id)

    def activate_provider(self, db, provider_id: str, operator_id: str | None) -> dict:
        row = self.repo.get(db, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到该配置")
        spec = self._spec_or_400(row.agent_id)

        values = decode_values(row.values)
        self.repo.activate(db, provider_id, operator_id)
        applied = self._apply_profile(spec, values)

        gaps = [spec.field(k).label if spec.field(k) else k for k in spec.gaps(values)]
        return {
            "active_provider_id": row.provider_id,
            "written_files": applied["written_files"],
            "gaps": gaps,
            "warnings": applied["warnings"],
            # 渲染进配置文件＝下一个任务即生效（CLI 每次启动直接读文件），不许谎报成需重启
            "restart_required": any(f.restart_required for f in spec.fields),
        }

    # ── 配置文件 ──
    def read_file(self, db, agent_id: str, name: str) -> dict:
        spec = self._spec_or_400(agent_id)
        file_spec = self._file_or_400(spec, name)
        path = file_spec.path()
        exists, doc, text, parse_error = agent_files.load_document(path)
        disk_sha = agent_files.sha256_of(text) if text is not None else (
            agent_files.sha256_of(path.read_bytes().decode("utf-8", "replace")) if exists else None)

        managed = list(spec.env_keys) if file_spec.holds_env else []
        masked_keys: list[str] = []
        foreign: list[str] = []
        shadow = self._shadow_keys(spec)
        raw_out = text

        if doc is not None:
            env = doc.get("env") if isinstance(doc.get("env"), dict) else {}
            foreign = sorted(k for k in env if k not in set(managed))
            secret_keys = {f.env_var for f in spec.fields if f.is_secret}
            masked_doc, masked_keys = agent_files.mask_document(doc, secret_keys)
            raw_out = json.dumps(masked_doc, ensure_ascii=False, indent=2)

        return {
            "agent_id": spec.id,
            "name": file_spec.name,
            "path": str(path),
            "exists": exists,
            "raw": raw_out,
            # 视图不等于磁盘：只要掩过码就必须说清楚，否则管理员复制粘贴就毁掉原值
            "masked": bool(masked_keys),
            "masked_keys": masked_keys,
            "managed_keys": managed,
            "foreign_env_keys": foreign,
            "shadow_env_keys": shadow,
            "sha256": disk_sha,
            "parse_error": parse_error,
            "editable": file_spec.editable,
            "note": file_spec.note,
        }

    def write_file(self, db, agent_id: str, name: str, raw: str,
                   expected_sha256: str | None) -> dict:
        spec = self._spec_or_400(agent_id)
        file_spec = self._file_or_400(spec, name)
        if not file_spec.editable:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 不允许在界面修改。{file_spec.note}")

        path = file_spec.path()
        exists, doc, text, parse_error = agent_files.load_document(path)
        if exists and parse_error:
            raise HTTPException(
                status_code=400,
                detail="当前文件内容无法识别，系统不会在损坏的文件上继续写入。"
                       "请先修复该文件或将其改名后重试。")
        current_sha = agent_files.sha256_of(text) if text is not None else None
        if expected_sha256 and current_sha and expected_sha256 != current_sha:
            raise HTTPException(
                status_code=409,
                detail="当前文件内容已与其他修改不同步，可能已被其他入口改过。"
                       "请重新加载后再提交，以免覆盖他人的修改。")

        try:
            submitted = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400,
                               detail=f"提交的内容不是可识别的 JSON 格式，位置约在第 {exc.lineno} 行，请检查后重新提交。")
        if not isinstance(submitted, dict):
            raise HTTPException(status_code=400, detail="文件内容格式不是可识别的设置结构")

        if file_spec.holds_env:
            self._guard_managed_and_restore_secrets(spec, submitted, doc or {})

        bak = agent_files.atomic_write(path, submitted)
        out = self.read_file(db, agent_id, name)
        if bak is not None:
            out["backup"] = str(bak)
        return out

    # ── 网关连通探针（显式动作，会真发一次请求）──
    def probe_provider(self, db, provider_id: str) -> dict:
        row = self.repo.get(db, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到该配置")
        spec = self._spec_or_400(row.agent_id)
        values = decode_values(row.values)

        checks: list[dict] = []
        base_url = str(values.get("base_url") or "").strip().rstrip("/") or "https://api.anthropic.com"
        key_pair = next((f for f in spec.fields if f.is_secret
                         and str(values.get(f.key) or "").strip()), None)

        checks.append({
            "key": "endpoint_shape",
            "label": "服务地址写法",
            "pass": bool(base_url) and base_url.startswith("https://"),
            "detail": "将向该服务地址发起模型调用"
                      + ("" if base_url.startswith("https://") else "，当前地址不是 https 开头，无法发起调用"),
        })
        checks.append({
            "key": "credential_present",
            "label": "密钥是否已填写",
            "pass": key_pair is not None,
            "detail": (f"将使用「{key_pair.label}」发起调用" if key_pair
                       else "两类密钥都未填写，AI 服务不会接受调用"),
        })
        model = str(values.get("model") or "").strip()
        gaps = spec.gaps(values)
        checks.append({
            "key": "model_present",
            "label": "模型名称是否已填写",
            "pass": bool(model),
            "detail": f"模型名称：{model}" if model else "未填写模型名称",
        })
        if gaps:
            checks.append({
                "key": "gateway_model_tiers",
                "label": "第三方服务的模型名称",
                "pass": False,
                "detail": "使用第三方服务时还缺少以下模型名称，用到对应档位时会直接报错："
                          + "、".join(spec.field(k).label for k in gaps if spec.field(k)),
            })

        ok = False
        http_status: int | None = None
        sent = bool(key_pair and model)
        if sent:
            ok, http_status, detail = self._probe_http(base_url, key_pair,
                                                      str(values.get(key_pair.key) or ""), model)
            checks.append({
                "key": "gateway_replied",
                "label": "AI 服务是否返回了正常响应",
                "pass": ok,
                "detail": detail,
            })
        else:
            checks.append({
                "key": "gateway_replied",
                "label": "AI 服务是否返回了正常响应",
                "pass": False,
                "detail": "密钥或模型名称缺失，未发起调用，因此不消耗积分",
            })

        return {
            "ok": ok,
            "checks": checks,
            "http_status": http_status,
            "elapsed_ms": None,
            # 打到网关并拿到响应才可能花钱；连不上（DNS/拒连/超时）一分钱都没消耗。
            # 把"没连上"报成"花了额度"，管理员会以为白烧了一次钱。
            "consumed_quota": sent and http_status is not None,
            # 只证明了网关活着，没证明能出片；ADR-032 的验收硬标准仍然是"真跑出一个成片"
            "note": "本次仅检查了服务地址与密钥是否可用，不代表能够正常生成。",
        }

    @staticmethod
    def _probe_ssl_context():
        """探针用的 TLS 上下文：显式取 certifi 的根证书库。

        macOS 上 python.org 安装的不接管系统钥匙串，默认 TLS 上下文里没有受信任根证书，
        任何 https 请求都会抛 CERTIFICATE_VERIFY_FAILED，于是被误报成"连不上服务"。
        2026-11-04 实测：deepseek 的服务地址与密钥都填对了，测试连接却一直说不通，
        同一请求换用 certifi 证书库后立即返回 401（说明 TLS 已过、请求到了服务端）。
        """
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            # 证书库缺失时退回系统默认，并在句子里如实说明，不再谎报网络问题
            return ssl.create_default_context()

    def _probe_http(self, base_url: str, credential_field, secret: str,
                    model: str) -> tuple[bool, int | None, str]:
        """发一次最小请求。绝不把凭据写进任何返回文案或日志。"""
        payload = json.dumps({
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}],
        }).encode("utf-8")
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        # 认证头由注册表声明（两种凭据发不同的头，发错的表现为 401 而不是"缺 key"）。
        # 声明写成 `x-api-key` 或 `Authorization: Bearer`：冒号左边是头名，右边是 schemes
        # ——这里不写死变量名去比较，否则注册表一改就漏（见 AgentFieldSpec.auth_header）。
        header_name, _, header_scheme = (credential_field.auth_header or "").partition(":")
        header_name = header_name.strip()
        if not header_name:
            return False, None, "配置中缺少该密钥的使用方式说明，无法发起检查，请联系维护者"
        headers[header_name.lower()] = (f"{header_scheme.strip()} {secret}"
                                         if header_scheme.strip() else secret)

        req = urllib.request.Request(f"{base_url}/v1/messages", data=payload,
                                    headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=PROBE_TIMEOUT_SECONDS, context=self._probe_ssl_context()
            ) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            body = exc.read(2048).decode("utf-8", "replace") if hasattr(exc, "read") else ""
            status = exc.code
            if status == 401:
                return False, status, "AI 服务不接受该密钥，或密钥类型与填写方式不一致"
            if status in (404, 400) and "model" in body.lower():
                return False, status, "AI 服务不认识该模型名称，请核对配置中的模型名称"
            return False, status, "AI 服务返回异常，请稍后重试"
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
                return False, None, "本机缺少受信任的根证书，安全检查无法完成；请联系管理员更新运行环境"
            return False, None, "无法连接 AI 服务，请检查服务地址与网络"
        except OSError:
            return False, None, "无法连接 AI 服务，请检查服务地址与网络"

        if status == 200 and '"content"' in body:
            return True, status, "检查通过：服务地址、密钥、模型名称均有效"
        return False, status, "AI 服务返回了异常响应，未包含消息内容，请稍后重试"

    @staticmethod
    def _brief(body: str) -> str:
        text = " ".join(body.split())
        return text[:180] or "（空响应）"

    # ── 内部 ──
    def _spec_or_400(self, agent_id: object) -> AgentSpec:
        try:
            return agent_spec(str(agent_id or ""))
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc.args[0])) from None

    def _file_or_400(self, spec: AgentSpec, name: str) -> Any:
        file_spec = spec.file(str(name or ""))
        if file_spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"{spec.label} 没有名为「{name}」的可读写配置文件")
        return file_spec

    def _validated(self, spec: AgentSpec, values: dict[str, str]) -> dict[str, str]:
        errors: list[str] = []
        unknown = sorted(k for k in values if spec.field(k) is None)
        if unknown:
            errors.append(f"未知字段：{'、'.join(unknown)}，请核对后重新提交")
        clean: dict[str, str] = {}
        for f in spec.fields:
            text = str(values.get(f.key) or "")
            if len(text) > MAX_VALUE_LEN:
                errors.append(f"「{f.label}」过长（超过 {MAX_VALUE_LEN} 字符），"
                              f"该项应填写{'密钥' if f.is_secret else '服务地址或模型名称'}，不应为长文本")
            clean[f.key] = text
        errors.extend(spec.conflicts(clean))
        if errors:
            raise HTTPException(status_code=400, detail="。".join(errors))
        return clean

    def _profile_out(self, spec: AgentSpec, row) -> dict:
        stored = decode_values(row.values)
        values: dict[str, Any] = {}
        secret_states: dict[str, dict] = {}
        gaps = [spec.field(k).label for k in spec.gaps(stored) if spec.field(k)]
        for f in spec.fields:
            raw = stored.get(f.key) or ""
            if f.is_secret:
                # 明文不出后端：值恒 null，只给"配没配"和够确认用的掩码
                values[f.key] = None
                secret_states[f.key] = {"configured": bool(raw),
                                        "masked": self._mask(raw) if raw else None}
            else:
                values[f.key] = raw or None
            # 非凭据字段不进 secret_states：给它们配一个"谈不上配没配"的 null，
            # 等于在接口上造出契约没承诺的形状
        return {
            "id": row.provider_id,
            "agent_id": row.agent_id,
            "name": row.name,
            "is_active": bool(row.is_active),
            "values": values,
            "secret_states": secret_states,
            "gaps": gaps,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "updated_by": row.updated_by,
            "warnings": [],
        }

    @staticmethod
    def _mask(plain: str) -> str:
        if len(plain) <= 8:
            return MASK
        return f"{plain[:3]}…{plain[-4:]}"

    def _shadow_keys(self, spec: AgentSpec) -> list[str]:
        """注册表托管的变量里，哪些在部署环境变量里也有值（仍在被子进程继承）。

        这是龚成 2026-11-02 定档"保留 .env 兼容"的代价：值不会凭空消失，
        那就必须让管理员看见它还在哪儿说话。
        """
        return sorted(k for k in spec.env_keys if os.environ.get(k))

    def _apply_profile(self, spec: AgentSpec, values: dict[str, str]) -> dict:
        """把生效档案渲染进该 agent 的可写配置文件。"""
        written_files: list[dict] = []
        warnings: list[str] = []
        secret_keys = {f.env_var for f in spec.fields if f.is_secret}

        for file_spec in spec.files:
            if not (file_spec.editable and file_spec.holds_env):
                continue
            path = file_spec.path()
            exists, doc, _text, parse_error = agent_files.load_document(path)
            if parse_error:
                # 坏文件不做增量改写：那会把"坏"扩散成"看起来好了但其实更坏"
                raise HTTPException(
                    status_code=400,
                    detail=f"{path.name} 现有内容不是可识别的 JSON，系统不会自动修复。"
                           f"请先将该文件移出，再重新启用配置。")
            new_doc, written, removed = agent_files.render_env(doc, values, spec)
            changed = bool(written or removed) or not exists
            bak = agent_files.atomic_write(path, new_doc) if changed else None

            # 删掉一条原本存在的项，必须说出来：这是"换供应商把认证方式一起换掉"的入口，
            # 两种凭据发的头不等价，删错了不报错、只在任务里以 401 出现（实测踩过）。
            for key in removed:
                label = next((f.label for f in spec.fields if f.env_var == key), key)
                warnings.append(
                    f"配置文件中原本的「{label}」对应项已被清除，因为该配置项为空。"
                    f"若该服务需要它，请回到配置页补充后再启用。")

            env_now = new_doc.get("env") or {}
            for key in sorted(set(self._shadow_keys(spec)) - set(env_now)):
                warnings.append(
                    f"{key} 目前仍由部署时的配置提供。"
                    f"若在此配置中填写同名项，将以这里的值为准。")
            for key in sorted(k for k in env_now if k not in set(spec.env_keys)):
                warnings.append(f"配置文件中存在一项不由本系统管理的设置：{key}。"
                                f"它仍会生效，请确认这是有意保留的。")
            written_files.append({
                "agent_id": spec.id,
                "name": file_spec.name,
                "path": str(path),
                "changed": changed,
                "backup": str(bak) if bak else None,
            })

        if not written_files:
            warnings.append("该生成方式没有可写的配置文件，此处仅保存记录，不会影响实际调用。")
        # 掩码信息不该出现在告警里，这里只可能带键名，不带值
        logger.info("agent %s 配置已生效，写入 %d 个文件（脱敏键 %d 个）",
                    spec.id, len(written_files), len(secret_keys))
        return {"written_files": written_files, "warnings": warnings}

    def _guard_managed_and_restore_secrets(self, spec: AgentSpec, submitted: dict,
                                           on_disk: dict) -> None:
        """原始视图写回时的两条护栏。

        1. 托管键不许在这里改——凭据与模型名只能在档案里改，两个入口写同一个值必然漂移。
        2. 掩码不许落盘：视图里凭据被换成掩码，原样提交回来时必须还原成磁盘上的真值，
           否则一次"我只改了别的键"就会把凭据刷成一串星号。
        """
        managed = set(spec.env_keys)
        sub_env = submitted.get("env") if isinstance(submitted.get("env"), dict) else {}
        disk_env = on_disk.get("env") if isinstance(on_disk.get("env"), dict) else {}

        touched: list[str] = []
        for key, value in list(sub_env.items()):
            if key not in managed:
                continue
            disk_value = disk_env.get(key)
            if str(value or "") == self._mask(str(disk_value)) and disk_value:
                sub_env[key] = disk_value  # 用户没动它，把真值放回去
                continue
            if str(value or "") == MASK and disk_value:
                sub_env[key] = disk_value
                continue
            if str(value or "") != str(disk_value or ""):
                touched.append(key)
        if touched:
            labels = "、".join(sorted(touched))
            raise HTTPException(
                status_code=400,
                detail=f"以下内容只能在配置页修改，不能在此文件中直接改：{labels}。"
                       f"请回到配置页完成修改后重新启用。")

        # 非托管键里如果也有被掩码回来的值，同样还原：一次"我只改了别的键"的保存
        # 不该把界面看不见的凭据刷成一串星号。
        for key in list(sub_env):
            if key in managed:
                continue
            disk_value = disk_env.get(key)
            if disk_value and str(sub_env[key] or "") in (self._mask(str(disk_value)), MASK):
                sub_env[key] = disk_value


agent_settings_service = AgentSettingsService()
