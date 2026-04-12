# Holy Framework

分布式任务管理系统，包含 **commander**（任务编排）和 **soldier**（任务执行）两个组件。

## 架构

- `commander/` - 任务管理：生成计划、分发任务、接收结果
- `soldier/` - 任务执行：接收任务、执行命令、报告结果
- `common.py` - 共享工具（任务引用解析、JSON文件处理、文件锁）
- `domain_resource.md` - 业务上下文（角色、资源、技能）

## 命令

```bash
# 启动 commander（自动生成任务 + 监听报告）
python commander/commander.py

# 启动 soldier（监听模式）
python soldier/soldier.py listen

# 手动分发任务
python commander/dispatch.py --target=hr --command='opencode run "任务描述"'

# 生成角色任务文件
python commander/generate_role_task.py
```

## 关键约定

- **端口**：commander=38471，soldier=38472
- **角色**（JSON中）：`HR`、`财务`、`总经理`、`程序员`
- **角色**（配置文件/任务中）：小写（`hr`、`finance`、`ceo`、`developer`）
- **task_ref 格式**：`YYYY-MM-DD_role_taskId`（例：`2026-04-11_hr_a1b2c3d4e5f67890`）
- **task_id**：UUID十六进制无连字符，8-32字符
- **任务文件**：`data/tasks/tasks_MM-DD.json`
- **角色任务文件**：`data/role_tasks/MM-DD-Role.json`

## 依赖

```bash
pip install filelock>=3.13.0
```

需要 `opencode` CLI 在 PATH 中。Linux检查路径：`/usr/local/bin/opencode`、`/usr/bin/opencode`、`~/.npm/bin/opencode`、`~/.local/bin/opencode`
