# Commander 开发者上手清单

## 目标

帮助新维护者在 30 分钟内理解架构、跑通链路、定位改动落点。

## 1. 先读文档顺序

1. `ARCHITECTURE.md`：看分层与依赖方向。
2. `README.md`：看运行方式与数据文件约定。
3. `commander.py`：看入口装配（不深入业务细节）。

## 2. 本地启动最短路径
 
在仓库根目录执行：

```bash
source .venv/bin/activate
cd soldier && python soldier.py listen
# 新终端
cd commander && python commander.py
```

观察日志：

```bash
tail -f commander/logs/commander_*.log
```

## 3. 核心模块怎么分工

- `commander.py`：入口与线程启动。
- `scanner_service.py`：扫描主流程（按角色循环、指针推进、分发调用）。
- `policies.py`：决定下一条 pending 任务。
- `repository.py`：任务文件读写 + FileLock + 状态落盘。
- `domain.py`：状态迁移合法性检查。
- `role_file_service.py`：每日任务文件确保/修复/生成。
- `dispatch_client.py`：通过 `dispatch.py` 触发实际下发。

## 4. 改需求时应该改哪层

- 变更“挑哪条任务先执行”：改 `policies.py`。
- 变更“扫描流程步骤”：改 `scanner_service.py`。
- 变更“文件字段或落盘行为”：改 `repository.py`。
- 变更“状态机规则”：改 `domain.py`。
- 变更“生成/修复规则”：改 `role_file_service.py`。
- 变更“下发通道实现”：改 `dispatch_client.py`。

## 5. 常见调试场景

### 场景 A：重启后首任务被跳过

看 `policies.py` 的 pending 判定是否覆盖：`is_load=true 且 task_id 为空`。

### 场景 B：角色互相阻塞

确认 waiting 判断在 `repository.py` 是按角色检查，不是全局检查。

### 场景 C：报告回写失败

看 `repository.py` 的 `update_task_report` 返回错误信息，重点核对 `task_ref` 与状态迁移。

## 6. 回归检查（每次改动后）

```bash
/home/ethan/桌面/holy-framework/.venv/bin/python -m unittest tests/test_commander_refactor.py
```

建议至少确认：

1. pending 选择规则没有回退。
2. waiting 状态绑定正常。
3. 报告状态迁移合法。

## 7. 代码变更守则

1. 入口层尽量只做参数解析和依赖装配。
2. 不要把业务规则塞回 `commander.py`。
3. 新规则优先放策略层或领域层。
4. 涉及状态变更，必须经过 `domain.py` 规则。
5. 文件写入必须经过 `repository.py`。
