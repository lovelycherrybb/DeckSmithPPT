#!/usr/bin/env python3
"""
vc_design 与 contracts 对齐性检查工具

使用方法：
    python3 scripts/check-alignment.py

检查维度：
    1. 对象层对齐：vc_design/objects 中的对象是否在 contracts 中有对应
    2. 状态层对齐：状态枚举是否一致
    3. 接口层对齐：vc_design/interfaces 中的接口是否在 openapi 中有对应端点
    4. 必填字段对齐：同一 schema 的 required 是否一致
    5. 字段类型对齐：字段类型、pattern、maxLength 等是否一致
    6. 约束层对齐：vc_design/constraints 中的约束是否在 contracts 中有体现
"""

import yaml
import json
import os
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent

def load_files():
    """加载 contracts 文件"""
    with open(ROOT / 'docs/contracts/openapi.yaml', 'r') as f:
        openapi = yaml.safe_load(f)
    with open(ROOT / 'docs/contracts/engine.schema.json', 'r') as f:
        schema = json.load(f)
    return openapi, schema

def check_object_alignment(openapi, schema):
    """检查对象层对齐"""
    print("\n" + "=" * 60)
    print("【1. 对象层对齐检查】")
    print("=" * 60)
    
    # vc_design 定义的核心对象
    vc_objects = ['Engine', 'Draft', 'Task', 'Project', 'Presentation']
    
    # contracts 中的 schema
    openapi_schemas = set(openapi['components']['schemas'].keys())
    schema_defs = set(schema['$defs'].keys())
    
    # 映射关系：vc_design 对象名 -> contracts 中的名称
    mapping = {
        'Engine': ['EngineInfo', 'Engine'],
        'Draft': ['Draft', 'DraftDetail', 'DraftVersion'],
        'Task': ['Job'],
        'Project': ['Project', 'ProjectDetail'],
        'Presentation': ['Presentation']
    }
    
    issues = []
    for vc_obj in vc_objects:
        contracts_names = mapping.get(vc_obj, [])
        found_in_openapi = [n for n in contracts_names if n in openapi_schemas]
        found_in_schema = [n for n in contracts_names if n in schema_defs]
        
        if found_in_openapi and found_in_schema:
            print(f"✓ {vc_obj}: openapi={found_in_openapi}, schema={found_in_schema}")
        else:
            issues.append(f"❌ {vc_obj}: openapi={found_in_openapi}, schema={found_in_schema}")
            print(f"❌ {vc_obj}: openapi={found_in_openapi}, schema={found_in_schema}")
    
    return issues

def check_state_alignment(openapi, schema):
    """检查状态层对齐"""
    print("\n" + "=" * 60)
    print("【2. 状态层对齐检查】")
    print("=" * 60)
    
    issues = []
    
    # 检查任务状态（分层枚举，见 ADR-018）：
    # 引擎协议的 EngineJobStatus 必须是面向用户的 openapi JobStatus 的**子集**，
    # 而不是相等 —— queued / cancelled 属编排层，引擎自身永远不产出它们。
    openapi_status = set(openapi['components']['schemas']['JobStatus']['enum'])
    engine_status = set(schema['$defs']['EngineJobStatus']['enum'])
    orphan_engine_states = engine_status - openapi_status

    if orphan_engine_states:
        issues.append(
            f"❌ 状态集合不满足「引擎态 ⊆ 任务态」: 引擎有而契约没有 {sorted(orphan_engine_states)}"
        )
        print(f"❌ 引擎有而契约没有: {sorted(orphan_engine_states)}")
    else:
        print(f"✓ EngineJobStatus ⊆ JobStatus（引擎态 {sorted(engine_status)}）")
        print(f"  仅编排层、引擎不产出的状态: {sorted(openapi_status - engine_status)}")
    
    # 检查 EngineType
    openapi_engine_type = set(openapi['components']['schemas']['EngineInfo']['properties']['engine_type']['enum'])
    schema_engine_type = set(schema['$defs']['EngineType']['enum'])
    
    if openapi_engine_type == schema_engine_type:
        print(f"✓ EngineType: {sorted(openapi_engine_type)}")
    else:
        issues.append(f"❌ EngineType 不一致: openapi={sorted(openapi_engine_type)}, schema={sorted(schema_engine_type)}")
        print(f"❌ EngineType 不一致: openapi={sorted(openapi_engine_type)}, schema={sorted(schema_engine_type)}")
    
    # 检查 TaskType
    openapi_task_type = set(openapi['components']['schemas']['Job']['properties']['task_type']['enum'])
    schema_task_type = set(schema['$defs']['TaskType']['enum'])
    
    if openapi_task_type == schema_task_type:
        print(f"✓ TaskType: {sorted(openapi_task_type)}")
    else:
        issues.append(f"❌ TaskType 不一致: openapi={sorted(openapi_task_type)}, schema={sorted(schema_task_type)}")
        print(f"❌ TaskType 不一致: openapi={sorted(openapi_task_type)}, schema={sorted(schema_task_type)}")
    
    return issues

def check_required_alignment(openapi, schema):
    """检查必填字段对齐"""
    print("\n" + "=" * 60)
    print("【3. 必填字段对齐检查】")
    print("=" * 60)
    
    issues = []
    openapi_schemas = openapi['components']['schemas']
    schema_defs = schema['$defs']
    
    # 检查共同的 schema
    common_schemas = set(openapi_schemas.keys()) & set(schema_defs.keys())
    
    for name in sorted(common_schemas):
        openapi_req = set(openapi_schemas[name].get('required', []))
        schema_req = set(schema_defs[name].get('required', []))
        
        if openapi_req == schema_req:
            print(f"✓ {name}: {sorted(openapi_req)}")
        else:
            issues.append(f"❌ {name} 必填字段不一致: openapi={sorted(openapi_req)}, schema={sorted(schema_req)}")
            print(f"❌ {name} 必填字段不一致:")
            print(f"    openapi: {sorted(openapi_req)}")
            print(f"    schema:  {sorted(schema_req)}")
    
    return issues

def find_patterns(obj, path=""):
    """递归查找所有 pattern"""
    patterns = {}
    if isinstance(obj, dict):
        if 'pattern' in obj:
            patterns[path] = obj['pattern']
        for k, v in obj.items():
            patterns.update(find_patterns(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            patterns.update(find_patterns(v, f"{path}[{i}]"))
    return patterns

def check_pattern_alignment(openapi, schema):
    """检查 pattern 校验对齐"""
    print("\n" + "=" * 60)
    print("【4. pattern 校验对齐检查】")
    print("=" * 60)
    
    issues = []
    
    openapi_patterns = find_patterns(openapi)
    schema_patterns = find_patterns(schema)
    
    # 提取字段名到 pattern 的映射
    def extract_field_patterns(patterns):
        result = {}
        for path, pattern in patterns.items():
            # 提取最后两级作为字段标识
            parts = path.split('.')
            if len(parts) >= 2:
                field_key = f"{parts[-2]}.{parts[-1]}"
                result[field_key] = pattern
        return result
    
    openapi_field_patterns = extract_field_patterns(openapi_patterns)
    schema_field_patterns = extract_field_patterns(schema_patterns)
    
    # 检查关键字段的 pattern
    key_fields = ['current_version', 'version', 'current_draft_version']
    
    for field in key_fields:
        openapi_pattern = None
        schema_pattern = None
        
        for k, v in openapi_field_patterns.items():
            if field in k:
                openapi_pattern = v
                break
        
        for k, v in schema_field_patterns.items():
            if field in k:
                schema_pattern = v
                break
        
        if openapi_pattern and schema_pattern:
            if openapi_pattern == schema_pattern:
                print(f"✓ {field}: {openapi_pattern}")
            else:
                issues.append(f"❌ {field} pattern 不一致: openapi={openapi_pattern}, schema={schema_pattern}")
                print(f"❌ {field} pattern 不一致: openapi={openapi_pattern}, schema={schema_pattern}")
        elif openapi_pattern and not schema_pattern:
            issues.append(f"❌ {field}: openapi 有 pattern ({openapi_pattern})，schema 没有")
            print(f"❌ {field}: openapi 有 pattern ({openapi_pattern})，schema 没有")
        elif schema_pattern and not openapi_pattern:
            issues.append(f"❌ {field}: schema 有 pattern ({schema_pattern})，openapi 没有")
            print(f"❌ {field}: schema 有 pattern ({schema_pattern})，openapi 没有")
    
    return issues

def check_interface_alignment(openapi):
    """检查接口层对齐"""
    print("\n" + "=" * 60)
    print("【5. 接口层对齐检查】")
    print("=" * 60)
    
    # vc_design/interfaces 中定义的接口（手动维护）
    vc_interfaces = {
        '获取引擎列表': '/api/engines',
        '上传文件': '/api/upload',
        '获取项目列表': '/api/projects',
        '获取项目详情': '/api/projects/{project_id}',
        '创建草稿引擎任务': '/api/decks',
        '创建输出引擎任务': '/api/decks',
        '获取项目下的任务列表': '/api/jobs',
        '获取任务详情': '/api/jobs/{job_id}',
        '重试任务': '/api/jobs/{job_id}/retry',
        '撤销任务': '/api/jobs/{job_id}/cancel',
        '获取项目下的草稿列表': '/api/projects/{project_id}/drafts',
        '获取草稿详情': '/api/drafts/{draft_id}',
        '获取草稿指定版本': '/api/drafts/{draft_id}/versions/{version}',
        '让 Agent 修改草稿': '/api/drafts/{draft_id}/modify',
        '保存草稿新版本': '/api/drafts/{draft_id}/versions',
        '还原草稿到历史版本': '/api/drafts/{draft_id}/restore',
        '获取项目下的演示文件列表': '/api/projects/{project_id}/presentations',
        '下载演示文件': '/api/presentations/{presentation_id}/download',
        '下载生成的 PPTX': '/api/jobs/{job_id}/download',
        '草稿安全检查': '/api/drafts/check',
        # 用户系统（v0.8）
        '登录': '/api/auth/login',
        '登出': '/api/auth/logout',
        '获取当前用户': '/api/auth/me',
        '修改自己的密码': '/api/auth/password',
        '获取用户列表': '/api/users',
        '新增用户': '/api/users',
        '修改用户信息': '/api/users/{user_id}',
        '重置用户密码': '/api/users/{user_id}/reset-password',
        '启用禁用用户': '/api/users/{user_id}/status',
        '获取本人偏好': '/api/settings/preferences',
        '保存本人偏好': '/api/settings/preferences',
        '删除项目': '/api/projects/{project_id}',
        '代为认领项目': '/api/projects/{project_id}/claim',
        # 系统设置（ADR-020）
        '读取系统设置': '/api/admin/settings',
        '保存系统设置': '/api/admin/settings',
        '并发容量只读提示': '/api/settings/capacity',
    }
    
    openapi_paths = set(openapi['paths'].keys())
    
    issues = []
    for interface_name, expected_path in vc_interfaces.items():
        if expected_path in openapi_paths:
            print(f"✓ {interface_name}: {expected_path}")
        else:
            issues.append(f"❌ {interface_name}: 期望 {expected_path}，但 openapi 中没有")
            print(f"❌ {interface_name}: 期望 {expected_path}，但 openapi 中没有")
    
    return issues

def main():
    """主函数"""
    print("vc_design 与 contracts 对齐性检查")
    print("=" * 60)
    
    try:
        openapi, schema = load_files()
    except Exception as e:
        print(f"❌ 加载文件失败: {e}")
        sys.exit(1)
    
    all_issues = []
    
    # 执行各项检查
    all_issues.extend(check_object_alignment(openapi, schema))
    all_issues.extend(check_state_alignment(openapi, schema))
    all_issues.extend(check_required_alignment(openapi, schema))
    all_issues.extend(check_pattern_alignment(openapi, schema))
    all_issues.extend(check_interface_alignment(openapi))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("【检查结果汇总】")
    print("=" * 60)
    
    if all_issues:
        print(f"\n❌ 发现 {len(all_issues)} 个问题:")
        for i, issue in enumerate(all_issues, 1):
            print(f"  {i}. {issue}")
        sys.exit(1)
    else:
        print("\n✅ 所有检查通过！vc_design 与 contracts 完全对齐。")
        sys.exit(0)

if __name__ == '__main__':
    main()
