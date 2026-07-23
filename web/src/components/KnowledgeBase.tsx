import { useEffect, useState, useCallback } from 'react'
import {
  Table, Tag, Button, Space, Card, Empty, Modal, Form, Input, Select,
  Statistic, Row, Col, message, Tooltip, Typography,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined,
  BookOutlined, CheckOutlined, CloseOutlined, SearchOutlined,
} from '@ant-design/icons'

const { TextArea } = Input
const { Text } = Typography

interface Runbook {
  id: string
  title: string
  content: string
  tags: string
  source: 'manual' | 'agent_generated'
  status: 'approved' | 'pending_review' | 'rejected'
  session_id?: string
  confidence?: number
  created_at: number
  updated_at: number
  updated_by: string
}

interface Stats {
  total: number
  approved: number
  pending_review: number
  rejected: number
  manual: number
  agent_generated: number
}

const STATUS_COLORS: Record<string, string> = {
  approved: 'success',
  pending_review: 'warning',
  rejected: 'error',
}
const STATUS_LABELS: Record<string, string> = {
  approved: '已审核',
  pending_review: '待审核',
  rejected: '已拒绝',
}
const SOURCE_COLORS: Record<string, string> = {
  manual: 'blue',
  agent_generated: 'purple',
}
const SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  agent_generated: 'Agent回写',
}

function KnowledgeBase() {
  const [runbooks, setRunbooks] = useState<Runbook[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Runbook | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [filterStatus, setFilterStatus] = useState('')
  const [form] = Form.useForm()

  const fetchRunbooks = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filterStatus) params.set('status', filterStatus)
      const res = await fetch(`/api/runbooks?${params}`)
      const data = await res.json()
      setRunbooks(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error('fetchRunbooks failed:', e)
      message.error('加载知识库失败')
    }
    setLoading(false)
  }, [filterStatus])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch('/api/runbooks/stats')
      const data = await res.json()
      setStats(data)
    } catch (e) {
      console.error('fetchStats failed:', e)
    }
  }, [])

  useEffect(() => {
    fetchRunbooks()
    fetchStats()
  }, [fetchRunbooks, fetchStats])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const rb = {
        ...values,
        id: editing?.id,
        updated_by: 'web-user',
      }
      const url = editing ? `/api/runbooks/${editing.id}` : '/api/runbooks'
      const method = editing ? 'PUT' : 'POST'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rb),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      message.success(editing ? 'Runbook 已更新' : 'Runbook 已创建')
      setModalOpen(false)
      form.resetFields()
      fetchRunbooks()
      fetchStats()
    } catch (e) {
      message.error('保存失败: ' + (e as Error).message)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      const res = await fetch(`/api/runbooks/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      message.success('已删除')
      fetchRunbooks()
      fetchStats()
    } catch (e) {
      message.error('删除失败')
    }
  }

  const handleReview = async (id: string, status: 'approved' | 'rejected') => {
    try {
      const res = await fetch(`/api/runbooks/${id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, decided_by: 'web-user' }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      message.success(status === 'approved' ? '已通过审核' : '已拒绝')
      fetchRunbooks()
      fetchStats()
    } catch (e) {
      message.error('审核失败')
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      return
    }
    setSearching(true)
    try {
      const res = await fetch(`/api/runbooks/search?q=${encodeURIComponent(searchQuery)}&limit=5`)
      const data = await res.json()
      setSearchResults(data.results || [])
    } catch (e) {
      message.error('检索失败')
    }
    setSearching(false)
  }

  const columns = [
    {
      title: '标题', dataIndex: 'title', key: 'title',
      render: (v: string, r: Runbook) => (
        <Tooltip title={r.content.slice(0, 100) + '...'} placement="topLeft">
          <Text strong>{v}</Text>
        </Tooltip>
      ),
    },
    { title: '标签', dataIndex: 'tags', key: 'tags', width: 180,
      render: (v: string) => v ? v.split(',').map((t: string) =>
        <Tag key={t} color="blue" style={{ marginBottom: 2 }}>{t.trim()}</Tag>
      ) : '—',
    },
    {
      title: '来源', dataIndex: 'source', key: 'source', width: 100,
      render: (v: string) => <Tag color={SOURCE_COLORS[v]}>{SOURCE_LABELS[v] || v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string) => <Tag color={STATUS_COLORS[v]}>{STATUS_LABELS[v] || v}</Tag>,
    },
    {
      title: '置信度', dataIndex: 'confidence', key: 'conf', width: 80,
      render: (v: number) => v != null ? `${(v * 100).toFixed(0)}%` : '—',
    },
    {
      title: '更新时间', dataIndex: 'updated_at', key: 'ts', width: 130,
      render: (v: number) => v ? new Date(v * 1000).toLocaleString('zh-CN',
        { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—',
    },
    {
      title: '操作', key: 'action', width: 180,
      render: (_: any, r: Runbook) => (
        <Space size="small">
          {r.status === 'pending_review' && (
            <>
              <Button size="small" type="primary" icon={<CheckOutlined />}
                onClick={() => handleReview(r.id, 'approved')}>
                通过
              </Button>
              <Button size="small" danger icon={<CloseOutlined />}
                onClick={() => handleReview(r.id, 'rejected')}>
                拒绝
              </Button>
            </>
          )}
          <Button size="small" icon={<EditOutlined />}
            onClick={() => {
              setEditing(r)
              form.setFieldsValue(r)
              setModalOpen(true)
            }} />
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => handleDelete(r.id)} />
        </Space>
      ),
    },
  ]

  return (
    <div style={{ height: '100%', overflow: 'auto', paddingRight: 4 }}>
      {/* 统计卡片 */}
      {stats && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <Row gutter={16}>
            <Col span={4}>
              <Statistic title="总计" value={stats.total} prefix={<BookOutlined />} />
            </Col>
            <Col span={4}>
              <Statistic title="已审核" value={stats.approved} valueStyle={{ color: '#52c41a' }} />
            </Col>
            <Col span={4}>
              <Statistic title="待审核" value={stats.pending_review}
                valueStyle={{ color: stats.pending_review > 0 ? '#faad14' : undefined }} />
            </Col>
            <Col span={4}>
              <Statistic title="已拒绝" value={stats.rejected} valueStyle={{ color: '#ff4d4f' }} />
            </Col>
            <Col span={4}>
              <Statistic title="手动添加" value={stats.manual} />
            </Col>
            <Col span={4}>
              <Statistic title="Agent回写" value={stats.agent_generated}
                valueStyle={{ color: stats.agent_generated > 0 ? '#722ed1' : undefined }} />
            </Col>
          </Row>
        </Card>
      )}

      {/* 检索测试 */}
      <Card size="small" style={{ marginBottom: 12 }} title="知识库检索测试">
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="输入查询词, 测试向量+BM25混合检索..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onPressEnter={handleSearch}
            prefix={<SearchOutlined />}
          />
          <Button type="primary" onClick={handleSearch} loading={searching}>
            检索
          </Button>
        </Space.Compact>
        {searchResults.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {searchResults.map((r: any, i: number) => (
              <Card key={i} size="small" style={{ marginBottom: 6,
                background: 'rgba(255,255,255,0.02)' }}>
                <Space>
                  <Tag color={r.match_type === 'vector' ? 'green' : 'blue'}>
                    {r.match_type === 'vector' ? '向量' : 'BM25'}
                  </Tag>
                  <Tag>score: {r.score?.toFixed(3)}</Tag>
                  <Text strong>{r.title}</Text>
                </Space>
                <div style={{ marginTop: 4, fontSize: 12, opacity: 0.7 }}>
                  {r.content}
                </div>
              </Card>
            ))}
          </div>
        )}
      </Card>

      {/* Runbook 列表 */}
      <Card
        size="small"
        title={`知识库 (${runbooks.length} 条)`}
        extra={
          <Space>
            <Select
              placeholder="状态筛选"
              allowClear
              style={{ width: 120 }}
              value={filterStatus || undefined}
              onChange={(v) => setFilterStatus(v || '')}
              options={Object.entries(STATUS_LABELS).map(([k, v]) => ({ value: k, label: v }))}
            />
            <Button icon={<ReloadOutlined />} onClick={() => { fetchRunbooks(); fetchStats() }} size="small">
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} size="small"
              onClick={() => { setEditing(null); form.resetFields(); setModalOpen(true) }}>
              新增
            </Button>
          </Space>
        }
      >
        {runbooks.length === 0 && !loading ? (
          <Empty description="暂无知识库条目" />
        ) : (
          <Table
            dataSource={runbooks}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={{ pageSize: 15, size: 'small' }}
            scroll={{ x: 900 }}
          />
        )}
      </Card>

      {/* 新增/编辑 Modal */}
      <Modal
        title={editing ? '编辑 Runbook' : '新增 Runbook'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        width={680}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如: DataNode OOM 崩溃修复" />
          </Form.Item>
          <Form.Item name="content" label="内容" rules={[{ required: true, message: '请输入内容' }]}>
            <TextArea
              placeholder="结构化描述: 症状 / 排查步骤 / 根因 / 修复方法 / 验证方式"
              rows={8}
            />
          </Form.Item>
          <Form.Item name="tags" label="标签 (逗号分隔)">
            <Input placeholder="如: hdfs,datanode,oom" />
          </Form.Item>
          {!editing && (
            <Form.Item name="status" label="状态" initialValue="approved">
              <Select
                options={[
                  { value: 'approved', label: '已审核 (直接生效)' },
                  { value: 'pending_review', label: '待审核' },
                ]}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}

export default KnowledgeBase
