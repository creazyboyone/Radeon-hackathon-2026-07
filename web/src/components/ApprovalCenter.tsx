import { useEffect, useState, useCallback } from 'react'
import { Table, Tag, Button, Space, Card, Empty, Popconfirm, message } from 'antd'
import { CheckOutlined, CloseOutlined, ReloadOutlined } from '@ant-design/icons'

interface Approval {
  id: string
  session_id: string
  tool_name: string
  args: any
  risk_level: string
  dry_run: any
  status: string
  decided_by: string
  ts: number
}

const RISK_COLORS: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'green',
  destructive: 'volcano',
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
    message.success(`${status === 'approved' ? 'Approved' : 'Rejected'}: ${id}`)
    fetchApprovals()
  }

  const columns = [
    {
      title: 'Risk',
      dataIndex: 'risk_level',
      key: 'risk',
      width: 80,
      render: (v: string) => <Tag color={RISK_COLORS[v] || 'default'}>{v?.toUpperCase()}</Tag>,
    },
    { title: 'Tool', dataIndex: 'tool_name', key: 'tool', width: 150 },
    {
      title: 'Args',
      key: 'args',
      render: (_: any, r: Approval) => (
        <span style={{ fontSize: 12, fontFamily: 'monospace' }}>
          {JSON.stringify(r.args)}
        </span>
      ),
    },
    {
      title: 'Dry-run',
      key: 'dry_run',
      width: 200,
      render: (_: any, r: Approval) => r.dry_run?.message || '-',
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => (
        <Tag color={v === 'approved' ? 'success' : v === 'rejected' ? 'error' : 'processing'}>
          {v}
        </Tag>
      ),
    },
    {
      title: 'By',
      dataIndex: 'decided_by',
      key: 'by',
      width: 140,
      render: (v: string) => v || '-',
    },
    {
      title: 'Action',
      key: 'action',
      width: 160,
      render: (_: any, r: Approval) =>
        r.status === 'pending' ? (
          <Space>
            <Button type="primary" size="small" icon={<CheckOutlined />}
              onClick={() => decide(r.id, 'approved')}>Approve</Button>
            <Button danger size="small" icon={<CloseOutlined />}
              onClick={() => decide(r.id, 'rejected')}>Reject</Button>
          </Space>
        ) : null,
    },
  ]

  const pending = approvals.filter(a => a.status === 'pending')

  return (
    <div style={{ maxWidth: 1200 }}>
      <Card
        size="small"
        title={`Approval Center (${pending.length} pending / ${approvals.length} total)`}
        extra={<Button icon={<ReloadOutlined />} onClick={fetchApprovals} size="small">Refresh</Button>}
      >
        {approvals.length === 0 && !loading ? (
          <Empty description="No approvals yet" />
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
