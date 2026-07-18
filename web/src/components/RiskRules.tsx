import { useEffect, useState, useCallback } from 'react'
import { Table, Tag, Button, Space, Card, Empty, Modal, Form, Input, InputNumber, Select, Switch, message } from 'antd'
import { PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'

interface RiskRule {
  id: string; tool_name: string; match_json: any; tier: string
  autonomous: boolean; enabled: boolean; priority: number
  updated_at: number; updated_by: string
}

const TIER_COLORS: Record<string, string> = {
  low: 'green', medium: 'blue', recover: 'orange',
  reversible: 'gold', irreversible: 'red',
}
const TIER_LABELS: Record<string, string> = {
  low: '低危', medium: '中危', recover: '可恢复',
  reversible: '可回撤', irreversible: '不可逆',
}

function RiskRules() {
  const [rules, setRules] = useState<RiskRule[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<RiskRule | null>(null)
  const [form] = Form.useForm()

  const fetchRules = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/risk_rules')
      const data = await res.json()
      setRules(data)
    } catch (e) {
      console.error('fetchRules failed:', e)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchRules()
  }, [fetchRules])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const rule = {
        ...values,
        id: editing?.id,
        match_json: values.match_json ? JSON.parse(values.match_json) : null,
        updated_by: 'web-user',
      }
      const url = editing ? `/api/risk_rules/${editing.id}` : '/api/risk_rules'
      const method = editing ? 'PUT' : 'POST'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rule),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      message.success(editing ? '规则已更新' : '规则已创建')
      setModalOpen(false)
      form.resetFields()
      fetchRules()
    } catch (e) {
      message.error('保存失败: ' + (e as Error).message)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`/api/risk_rules/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      message.success('规则已删除')
      fetchRules()
    } catch (e) {
      message.error('删除失败')
    }
  }

  const columns = [
    { title: '工具', dataIndex: 'tool_name', key: 'tool', width: 160 },
    {
      title: '匹配条件', key: 'match', width: 200,
      render: (_: any, r: RiskRule) => r.match_json ? JSON.stringify(r.match_json) : '— (任意)',
    },
    {
      title: '档位', dataIndex: 'tier', key: 'tier', width: 100,
      render: (v: string) => <Tag color={TIER_COLORS[v] || 'default'}>{TIER_LABELS[v] || v}</Tag>,
    },
    {
      title: '自动执行', dataIndex: 'autonomous', key: 'auto', width: 90,
      render: (v: boolean, r: RiskRule) => (
        <Switch checked={v} disabled={r.tier === 'irreversible'} size="small" />
      ),
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 70,
      render: (v: boolean) => <Tag color={v ? 'success' : 'default'}>{v ? 'ON' : 'OFF'}</Tag>,
    },
    { title: '优先级', dataIndex: 'priority', key: 'pri', width: 70 },
    {
      title: '操作', key: 'action', width: 140,
      render: (_: any, r: RiskRule) => (
        <Space>
          <Button size="small" icon={<EditOutlined />}
            onClick={() => { setEditing(r); form.setFieldsValue({ ...r, match_json: r.match_json ? JSON.stringify(r.match_json) : '' }); setModalOpen(true) }}>
            编辑
          </Button>
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => handleDelete(r.id)} />
        </Space>
      ),
    },
  ]

  return (
    <div style={{ height: '100%', overflow: 'auto' }}>
      <Card
        size="small"
        title={`风险规则 (${rules.length} 条)`}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchRules} size="small">刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} size="small"
              onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true) }}>
              新增
            </Button>
          </Space>
        }
      >
        {rules.length === 0 && !loading ? (
          <Empty description="暂无规则" />
        ) : (
          <Table
            dataSource={rules}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={{ pageSize: 20, size: 'small' }}
          />
        )}
      </Card>

      <Modal
        title={editing ? '编辑规则' : '新增规则'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        width={520}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="tool_name" label="工具名" rules={[{ required: true }]}>
            <Input placeholder="如 restart_service, 或 * 表示默认" />
          </Form.Item>
          <Form.Item name="match_json" label="匹配条件 (JSON, 可选)">
            <Input.TextArea placeholder='如 {"action":"format"} 留空表示任意' rows={2} />
          </Form.Item>
          <Form.Item name="tier" label="档位" rules={[{ required: true }]}>
            <Select options={Object.entries(TIER_LABELS).map(([k, v]) => ({ value: k, label: v }))} />
          </Form.Item>
          <Form.Item name="autonomous" label="允许自动执行" valuePropName="checked">
            <Switch disabled={Form.useWatch('tier', form) === 'irreversible'} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue={0}>
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default RiskRules
