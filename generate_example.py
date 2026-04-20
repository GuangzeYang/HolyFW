#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from common import normalize_role_tasks
import json

# Provide some example task descriptions for each role
# We'll use the fallback tasks and repeat them to reach 18
data = {
    "hr": [
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，查收并分类员工咨询邮件，整理成待处理清单"},
        {"task": "使用playwright-browser skill，打开浏览器，登录OA系统核对当日人事审批流状态"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，发送邮件给相关部门，确认招聘流程节点进度"},
        {"task": "使用smb-access skill，访问共享目录\\\\resource\\HR，归档当日人事文档"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，核对新员工入职材料完整性并发送补件提醒"},
    ],
    "accountancy": [
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，查收银行通知邮件并核对到账信息"},
        {"task": "使用playwright-browser skill，打开浏览器，登录OA系统复核报销审批状态并记录差异"},
        {"task": "使用smb-access skill，访问共享目录\\\\resource\\Finance，更新付款计划表"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，发送邮件给业务部门确认发票与合同匹配情况"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，核对本日应收应付变动并整理汇总邮件"},
    ],
    "manager": [
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，查收管理层汇报邮件并标记优先处理事项"},
        {"task": "使用playwright-browser skill，打开浏览器，登录OA系统查看关键审批与风险提醒"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，发送邮件给部门负责人确认当日重点任务进展"},
        {"task": "使用smb-access skill，访问共享目录\\\\resource\\Executive，查看经营数据看板"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，回复跨部门协调邮件并明确执行时间点"},
    ],
    "programmer": [
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，查收团队邮件并更新当日开发任务优先级"},
        {"task": "使用smb-access skill，访问共享目录\\\\resource\\Developer，拉取开发文档与脚本"},
        {"task": "使用playwright-browser skill，打开浏览器，登录代码平台查看待处理Merge Request与评论"},
        {"task": "使用playwright-browser和exchange-use skill，打开浏览器，登录Exchange邮箱，查收测试反馈邮件并补充缺陷复现记录"},
        {"task": "使用playwright-browser skill，打开浏览器，登录OA系统更新研发工作记录与进展说明"},
    ]
}

# Normalize tasks (will expand to 18 each with generated times) for only four roles
normalized = normalize_role_tasks(data, min_tasks_per_role=18, roles=["hr", "accountancy", "manager", "programmer"])
print(json.dumps(normalized, ensure_ascii=False, indent=2))