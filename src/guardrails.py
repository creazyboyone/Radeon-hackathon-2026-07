"""
安全护栏 — 风险分级 + 审批门 + dry-run + 审计日志 + 熔断

设计文档第 5/9 节:
  低危(只读)      → 自动执行
  中危(可回滚)    → 执行 + 事后通知
  高危(影响可用性) → dry-run预览 + 审批门 + 熔断检查
  破坏性(不可逆)  → dry-run + 必须审批 + 备份

Console 模式: 高危操作打印警告后自动批准
Web 模式(后续): 高危操作通过 WebSocket 等人工审批
"""
import json
import time
import uuid
import logging

from .tools import execute_tool, TOOL_RISK, SERVICE_MAP

logger = logging.getLogger(__name__)

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_DESTRUCTIVE = "destructive"


class Guardrail:
    """安全护栏 — 包装工具执行，所有高危操作经过审批/熔断/审计"""

    def __init__(self, store, auto_approve=True):
        self.store = store
        self.auto_approve = auto_approve  # console 模式自动批准
        # 熔断状态
        self._failure_counts = {}      # {service: count}
        self._last_failure_ts = {}     # {service: timestamp}
        self.max_failures = 3          # 连续失败阈值
        self.cooldown = 300            # 冷却时间(秒)

    def execute(self, tool_name, args, session_id=""):
        """安全执行工具 — 经过风险分级 / dry-run / 审批 / 审计 / 熔断"""
        risk = self._assess_risk(tool_name, args)

        # 审计: 记录调用意图
        self._audit(session_id, tool_name, args, "requested", risk)
        print(f"  [GUARDRAIL] {tool_name} risk={risk}")

        if risk == RISK_LOW:
            return self._do_execute(tool_name, args, risk, session_id)

        elif risk == RISK_MEDIUM:
            result = self._do_execute(tool_name, args, risk, session_id)
            self._notify(f"[MEDIUM] {tool_name}({args.get('service', '')}) "
                         f"executed, result={result.get('result', 'ok')}")
            return result

        else:  # high or destructive
            svc = args.get("service", "")

            # 1. 熔断检查
            if self._is_circuit_broken(svc):
                msg = (f"熔断: {svc} 连续失败 {self._failure_counts.get(svc, 0)} 次, "
                       f"已升级人工处理")
                self._audit(session_id, tool_name, args, "circuit_broken", risk)
                logger.warning(msg)
                print(f"  [GUARDRAIL] ⚠️ {msg}")
                return {"error": msg, "circuit_broken": True}

            # 2. dry-run 预览
            dry_run = self._dry_run(tool_name, args)
            self._audit(session_id, tool_name, args, "dry_run_preview", risk, dry_run)
            print(f"  [GUARDRAIL] dry-run: {dry_run.get('message', '')}")

            # 3. 审批门
            approval = self._request_approval(
                session_id, tool_name, args, risk, dry_run)
            if approval["status"] != "approved":
                self._audit(session_id, tool_name, args, "rejected", risk,
                            approval)
                print(f"  [GUARDRAIL] 审批未通过")
                return {"error": "审批未通过", "approval": approval}
            print(f"  [GUARDRAIL] 审批通过 ({approval.get('decided_by', '')})")

            # 4. 执行
            result = self._do_execute(tool_name, args, risk, session_id)

            # 5. 熔断计数
            if self._is_failed(result):
                self._increment_failure(svc)
                count = self._failure_counts.get(svc, 0)
                self._notify(f"[HIGH] {tool_name}({svc}) 执行失败, "
                             f"连续失败 {count}/{self.max_failures}")
                if count >= self.max_failures:
                    self._notify(f"[ALERT] 熔断触发: {svc} 连续失败 {count} 次, "
                                 f"后续操作将升级人工处理")
            else:
                self._reset_failure(svc)

            return result

    # ---- 执行 + 审计 ----

    def _do_execute(self, tool_name, args, risk, session_id):
        """执行工具 + 记录审计"""
        result = execute_tool(tool_name, args)
        self._audit(session_id, tool_name, args, "executed", risk, result)
        return result

    # ---- 风险评估 ----

    def _assess_risk(self, tool_name, args):
        """双层风险判定: 静态规则(权威) + 服务属性"""
        base = TOOL_RISK.get(tool_name, RISK_LOW)
        if tool_name == "restart_service":
            svc = args.get("service", "")
            svc_info = SERVICE_MAP.get(svc, {})
            return RISK_HIGH if svc_info.get("core") else RISK_MEDIUM
        return base

    # ---- dry-run ----

    def _dry_run(self, tool_name, args):
        """dry-run 预览: 返回"会发生什么"而不真执行"""
        if tool_name == "restart_service":
            svc = args.get("service", "")
            node = args.get("node", "")
            svc_info = SERVICE_MAP.get(svc, {})
            nodes = [node] if node else svc_info.get("nodes", [])
            risk = RISK_HIGH if svc_info.get("core") else RISK_MEDIUM
            return {
                "tool": tool_name,
                "action": f"CM API commands/start → 启动 {svc}",
                "target_nodes": nodes,
                "risk_level": risk,
                "reversible": True,
                "impact": "仅启动 STOPPED 角色, 不影响运行中的",
                "message": f"将启动 {svc} (nodes={nodes}) via CM API",
            }
        return {
            "tool": tool_name,
            "message": f"dry-run preview for {tool_name}",
        }

    # ---- 审批门 ----

    def _request_approval(self, session_id, tool_name, args,
                          risk, dry_run, timeout=300):
        """请求审批 — 记录到 DB。

        console 模式 (auto_approve=True): 立即自动批准
        Web 模式 (auto_approve=False): 阻塞轮询 DB 等待人工审批, 超时自动拒绝
        """
        approval_id = str(uuid.uuid4())[:8]
        self.store.conn.execute(
            "INSERT INTO approvals(id,session_id,tool_name,args_json,"
            "risk_level,dry_run_json,status,ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (approval_id, session_id, tool_name,
             json.dumps(args, ensure_ascii=False), risk,
             json.dumps(dry_run, ensure_ascii=False),
             "pending", int(time.time()))
        )
        self.store.conn.commit()

        if self.auto_approve:
            self._decide_approval(approval_id, "approved",
                                 "auto-approve(console)")
            return {"id": approval_id, "status": "approved",
                    "decided_by": "auto-approve(console)"}
        else:
            # Web 模式: 阻塞轮询 DB 等待人工审批
            poll_interval = 2
            waited = 0
            while waited < timeout:
                row = self.store.conn.execute(
                    "SELECT status, decided_by FROM approvals WHERE id=?",
                    (approval_id,)
                ).fetchone()
                if row and row[0] != "pending":
                    return {"id": approval_id, "status": row[0],
                            "decided_by": row[1] or ""}
                time.sleep(poll_interval)
                waited += poll_interval
            # 超时自动拒绝
            self._decide_approval(approval_id, "rejected", "timeout")
            return {"id": approval_id, "status": "rejected",
                    "decided_by": "timeout",
                    "message": "审批超时, 已自动拒绝"}

    def _decide_approval(self, approval_id, status, decided_by):
        """更新审批状态"""
        self.store.conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=? "
            "WHERE id=?",
            (status, decided_by, int(time.time()), approval_id)
        )
        self.store.conn.commit()

    # ---- 熔断 ----

    def _is_circuit_broken(self, service):
        """检查是否熔断: 连续失败 >= max_failures 且在冷却期内"""
        count = self._failure_counts.get(service, 0)
        if count >= self.max_failures:
            last_ts = self._last_failure_ts.get(service, 0)
            if time.time() - last_ts < self.cooldown:
                return True
            else:
                # 冷却期过后重置
                self._failure_counts[service] = 0
                self._last_failure_ts.pop(service, None)
        return False

    def _increment_failure(self, service):
        self._failure_counts[service] = \
            self._failure_counts.get(service, 0) + 1
        self._last_failure_ts[service] = time.time()

    def _reset_failure(self, service):
        self._failure_counts.pop(service, None)
        self._last_failure_ts.pop(service, None)

    @staticmethod
    def _is_failed(result):
        if not result:
            return True
        if isinstance(result, dict):
            r = result.get("result", "")
            return r in ("failed", "error", "unsupported")
        return False

    # ---- 审计日志 ----

    def _audit(self, session_id, tool_name, args, status,
               risk, result=None):
        """审计日志 — 每次工具调用写 SQLite"""
        self.store.conn.execute(
            "INSERT INTO audit_log(session_id,tool_name,args_json,"
            "risk_level,status,result_json,ts) VALUES(?,?,?,?,?,?,?)",
            (session_id, tool_name,
             json.dumps(args, ensure_ascii=False), risk, status,
             json.dumps(result, ensure_ascii=False) if result else "",
             int(time.time()))
        )
        self.store.conn.commit()

    # ---- 通知 ----

    @staticmethod
    def _notify(message):
        """通知 — console 打印, 后续可接 webhook"""
        print(f"  [GUARDRAIL] {message}")
