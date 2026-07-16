import { useEffect, useState, useCallback } from 'react'
import { Table, Tag, Button, Space, Card, Empty, message } from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'

interface Approval {
  id: string; session_id: string; tool_name: string; args: any
  risk_level: string; dry_run: any; status: string; decided_by: string; ts: number
}

const RISK_COLORS: Record<string, string> = {
  high: 'red', medium: 'orange', low: 'green', destructive: 'volcano',
}
const RISK_LABELS: Record<string, string> = {
  high: '高危', medium: '中危', low: '低危', destructive: '破坏性',
}
const STATUS_LABELS: Record<string, string> = {
  pending: '待审批', approved: '已批准', rejected: '已拒绝',
}

function ApprovalCenter() {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [loading, setLoading] = useState(false)

  const fetchApprovals = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/approvals')
      const data = await res.json()
      setApprovals(data)
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchApprovals()
    const t = setInterval(fetchApprovals, 5000)
    return () => clearInterval(t)
  }, [fetchApprovals])

  const decide = async (id: string, status: string) => {
    await fetch(`/api/approvals/${id}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, decided_by: 'web-user' }),
    })
    message.success(`${status === 'approved' ? '已批准' : '已拒绝'}: ${id}`)
    fetchApprovals()
  }

  const columns = [
    {
      title: '风险', dataIndex: 'risk_level', key: 'risk', width: 80,
      render: (v: string) => <Tag color={RISK_COLORS[v] || 'default'}>{RISK_LABELS[v] || v}</Tag>,
    },
    { title: '工具', dataIndex: 'tool_name', key: 'tool', width: 140 },
    {
      title: '参数', key: 'args',
      render: (_: any, r: Approval) => (
        <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{JSON.stringify(r.args)}</span>
      ),
    },
    {
      title: '预览', key: 'dry_run', width: 220,
      render: (_: any, r: Approval) => r.dry_run?.message || '-',
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => (
        <Tag color={v === 'approved' ? 'success' : v === 'rejected' ? 'error' : 'processing'}>
          {STATUS_LABELS[v] || v}
        </Tag>
      ),
    },
    {
      title: '审批人', dataIndex: 'decided_by', key: 'by', width: 120,
      render: (v: string) => v || '-',
    },
    {
      title: '操作', key: 'action', width: 160,
      render: (_: any, r: Approval) =>
        r.status === 'pending' ? (
          <Space>
            <Button type="primary" size="small" icon={<CheckOutlined />}
              onClick={() => decide(r.id, 'approved')}>批准</Button>
            <Button danger size="small" icon={<CloseOutlined />}
              onClick={() => decide(r.id, 'rejected')}>拒绝</Button>
          </Space>
        ) : null,
    },
  ]

  const pending = approvals.filter(a => a.status === 'pending')

  return (
    <div style={{ height: '100%', overflow: 'auto' }}>
      <Card
        size="small"
        title={`审批中心 (${pending.length} 待审批 / ${approvals.length} 总计)`}
        extra={<Button icon={<ReloadOutlined />} onClick={fetchApprovals} size="small">刷新</Button>}
      >
        {approvals.length === 0 && !loading ? (
          <Empty description="暂无审批记录" />
        ) : (
          <Table
            dataSource={approvals}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={{ pageSize: 20, size: 'small' }}
          />
        )}
      </Card>
    </div>
  )
}

export default ApprovalCenter
