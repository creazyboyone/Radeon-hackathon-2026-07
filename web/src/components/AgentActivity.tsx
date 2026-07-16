import { useEffect, useRef, useState, useCallback } from 'react'
import { Tree, Timeline, Card, Tag, Typography, Badge, Spin, Collapse, Empty } from 'antd'
import {
  BulbOutlined, RobotOutlined, ToolOutlined, ApiOutlined,
  CheckCircleOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const { Text, Paragraph } = Typography

interface Session {
  id: string; parent_id: string | null; type: string
  status: string; trigger: string; started_at: number; ended_at: number
}
interface AgentEvent { type: string; session_id: string; kind: string; content: any }
interface HistEvent { seq: number; kind: string; content: any; ts: number }

const KIND_CFG: Record<string, { label: string; color: string; icon: any }> = {
  reasoning:    { label: '思考', color: 'purple',  icon: <BulbOutlined /> },
  assistant:    { label: '响应', color: 'blue',    icon: <RobotOutlined /> },
  tool_call:    { label: '工具调用', color: 'orange', icon: <ToolOutlined /> },
  tool_result:  { label: '结果',   color: 'green',   icon: <ApiOutlined /> },
  user_input:   { label: '输入',   color: 'default', icon: <ThunderboltOutlined /> },
  final_answer: { label: '完成',   color: 'success', icon: <CheckCircleOutlined /> },
}

function extractCall(name: string, args: any): string {
  if (!args) return ''
  const p: string[] = []
  if (args.service) p.push(args.service)
  if (args.node) p.push(args.node)
  if (args.metric) p.push(args.metric)
  if (args.filter) p.push(`过滤=${args.filter}`)
  if (args.query) p.push(`"${args.query}"`)
  if (args.action) p.push(args.action)
  if (args.reason) p.push(`(${args.reason.slice(0, 80)})`)
  return p.join(' ')
}

function extractResult(name: string, r: any): string {
  if (!r || typeof r !== 'object') return ''
  if (r.error) return `错误: ${r.error}`
  if (r.overall_health) return `健康=${r.overall_health}, 角色=${r.role_count ?? 0}`
  if (r.count !== undefined) return `告警数=${r.count}`
  if (r.total_errors !== undefined) return `错误=${r.total_errors}, 节点=${r.nodes_checked ?? 0}`
  if (r.matches !== undefined) return `匹配=${r.matches}`
  if (r.result) return `结果=${r.result}`
  if (r.command_id) return `命令=${r.command_id}, 结果=${r.result ?? '?'}`
  if (r.output) return (r.output as string).slice(0, 100).replace(/\n/g, ' ')
  if (r.nodes) return `节点=${Object.keys(r.nodes).join(',')}`
  return JSON.stringify(r).slice(0, 100)
}

function AgentActivity() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSid, setSelectedSid] = useState('')
  const [events, setEvents] = useState<(HistEvent | AgentEvent)[]>([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions')
      const data: Session[] = await res.json()
      setSessions(data)
      if (data.length > 0 && !selectedSid) setSelectedSid(data[0].id)
    } catch {}
  }, [selectedSid])

  const fetchEvents = useCallback(async (sid: string) => {
    setLoading(true)
    try {
      const res = await fetch(`/api/sessions/${sid}/events`)
      const data: HistEvent[] = await res.json()
      setEvents(data.map(e => ({ ...e, session_id: sid, type: 'agent_event' })))
    } catch { setEvents([]) }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchSessions()
    const t = setInterval(fetchSessions, 5000)
    return () => clearInterval(t)
  }, [fetchSessions])

  useEffect(() => {
    if (selectedSid) fetchEvents(selectedSid)
  }, [selectedSid, fetchEvents])

  useEffect(() => {
    const ws = new WebSocket(`ws://${location.host}/ws`)
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type !== 'agent_event') return
        if (data.session_id === selectedSid)
          setEvents(prev => [...prev.slice(-300), data])
        if (data.kind === 'user_input') fetchSessions()
      } catch {}
    }
    return () => ws.close()
  }, [selectedSid, fetchSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const roots = sessions.filter(s => !s.parent_id)
  const buildTree = (s: Session): any => {
    const children = sessions.filter(c => c.parent_id === s.id)
    const labels: Record<string, string> = { master: '主控', auto: '巡检', fix: '修复' }
    const colors: Record<string, string> = { master: 'processing', auto: 'blue', fix: 'warning' }
    return {
      key: s.id,
      title: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tag color={colors[s.type]} style={{ margin: 0, fontSize: 11 }}>
            {labels[s.type] || s.type}
          </Tag>
          <Text code style={{ fontSize: 11 }}>{s.id.slice(0, 8)}</Text>
          {!s.ended_at && <Badge status="processing" />}
        </span>
      ),
      children: children.map(buildTree),
    }
  }
  const treeData = roots.map(buildTree)

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', overflow: 'hidden' }}>
      {/* 左侧: Session 树 */}
      <Card
        size="small"
        style={{ width: 260, flexShrink: 0, overflow: 'auto' }}
        title={<Badge status={connected ? 'success' : 'default'}
          text={connected ? '实时' : '离线'} />}
      >
        {sessions.length === 0 ? (
          <Empty description="无会话" />
        ) : (
          <Tree
            treeData={treeData}
            selectedKeys={selectedSid ? [selectedSid] : []}
            onSelect={(keys) => keys[0] && setSelectedSid(keys[0] as string)}
            defaultExpandAll
            showLine
          />
        )}
      </Card>

      {/* 右侧: 事件流 */}
      <Card
        size="small"
        style={{ flex: 1, overflow: 'auto', minWidth: 0 }}
        title={selectedSid
          ? `会话 ${selectedSid.slice(0, 8)} (${events.length} 条事件)`
          : '请选择会话'}
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : events.length === 0 ? (
          <Empty description="无事件" />
        ) : (
          <Timeline items={events.map((evt: any, i) => {
            const cfg = KIND_CFG[evt.kind] || { label: evt.kind, color: 'gray', icon: null }
            const content = evt.content || {}
            const isMD = ['reasoning', 'assistant', 'final_answer', 'user_input'].includes(evt.kind)
            const isTool = ['tool_call', 'tool_result'].includes(evt.kind)
            let summary = ''
            if (evt.kind === 'tool_call')
              summary = `${content.name}(${extractCall(content.name, content.args || {})})`
            else if (evt.kind === 'tool_result')
              summary = extractResult(content.name || '', content.result || content)
            else if (content.text) summary = content.text
            else if (content.tool_calls?.length)
              summary = content.tool_calls.map((tc: any) => tc.name).join(', ')

            return {
              key: i, color: cfg.color as any, dot: cfg.icon,
              children: (
                <div>
                  <div style={{ marginBottom: 4 }}>
                    <Tag color={cfg.color}>{cfg.label}</Tag>
                  </div>
                  {isMD ? (
                    <div className="markdown-body" style={{ fontSize: 13, lineHeight: 1.6 }}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary || ''}</ReactMarkdown>
                    </div>
                  ) : isTool ? (
                    <div>
                      <Paragraph style={{ margin: 0 }}>
                        <Text style={{ fontSize: 13 }}>{summary}</Text>
                      </Paragraph>
                      <Collapse ghost size="small" items={[{
                        key: 'json', label: 'JSON 详情',
                        children: (
                          <pre style={{ fontSize: 11, color: '#888', overflow: 'auto',
                            maxHeight: 280, background: '#0d1117',
                            padding: 10, borderRadius: 6 }}>
                            {JSON.stringify(content, null, 2)}
                          </pre>
                        )
                      }]} />
                    </div>
                  ) : (
                    <Text style={{ fontSize: 13 }}>{summary}</Text>
                  )}
                </div>
              ),
            }
          })} />
        )}
        <div ref={bottomRef} />
      </Card>
    </div>
  )
}

export default AgentActivity
