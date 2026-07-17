import { useEffect, useRef, useState, useCallback } from 'react'
import {
  Tree, Timeline, Card, Tag, Typography, Badge, Spin, Collapse,
  Empty, Descriptions, Statistic,
} from 'antd'
import {
  BulbOutlined, RobotOutlined, ToolOutlined, ApiOutlined,
  CheckCircleOutlined, ThunderboltOutlined, DashboardOutlined,
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
  reasoning:         { label: '思考',   color: 'purple',  icon: <BulbOutlined /> },
  stream_reasoning:  { label: '思考中', color: 'purple',  icon: <BulbOutlined /> },
  assistant:         { label: '响应',   color: 'blue',    icon: <RobotOutlined /> },
  stream_content:    { label: '响应中', color: 'blue',    icon: <RobotOutlined /> },
  tool_call:         { label: '工具调用', color: 'orange', icon: <ToolOutlined /> },
  tool_result:       { label: '结果',   color: 'green',   icon: <ApiOutlined /> },
  user_input:        { label: '输入',   color: 'default', icon: <ThunderboltOutlined /> },
  final_answer:      { label: '完成',   color: 'success', icon: <CheckCircleOutlined /> },
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
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

// 判断事件是否为 Markdown 渲染类型
const MD_KINDS = new Set(['reasoning', 'stream_reasoning', 'assistant', 'stream_content', 'final_answer', 'user_input'])
const TOOL_KINDS = new Set(['tool_call', 'tool_result'])

function AgentActivity() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSid, setSelectedSid] = useState('')
  const [selectedSession, setSelectedSession] = useState<Session | null>(null)
  const [events, setEvents] = useState<(HistEvent | AgentEvent)[]>([])
  const [clusterSnap, setClusterSnap] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const sessionsRef = useRef<Session[]>([])
  const selectedSidRef = useRef('')
  const atBottomRef = useRef(true)
  const autoScrollRef = useRef(false)

  sessionsRef.current = sessions
  selectedSidRef.current = selectedSid

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions')
      const data: Session[] = await res.json()
      setSessions(data)
      if (data.length > 0 && !selectedSidRef.current) setSelectedSid(data[0].id)
    } catch (e) {
      console.error('fetchSessions failed:', e)
    }
  }, [])

  const fetchEvents = useCallback(async (sid: string) => {
    setLoading(true)
    try {
      const res = await fetch(`/api/sessions/${sid}/events`)
      const data: HistEvent[] = await res.json()
      setEvents(data.map(e => ({ ...e, session_id: sid, type: 'agent_event' })))
    } catch (e) {
      console.error('fetchEvents failed:', e)
      setEvents([])
    }
    setLoading(false)
  }, [])

  const fetchClusterSnap = useCallback(async () => {
    try {
      const res = await fetch('/api/cluster/snapshot')
      const data = await res.json()
      setClusterSnap(data)
    } catch (e) {
      console.error('fetchClusterSnap failed:', e)
    }
  }, [])

  useEffect(() => {
    fetchSessions()
    const t = setInterval(fetchSessions, 5000)
    return () => clearInterval(t)
  }, [fetchSessions])

  useEffect(() => {
    if (!selectedSid) return
    const s = sessionsRef.current.find(x => x.id === selectedSid)
    if (s?.type === 'master') { fetchClusterSnap(); setEvents([]) }
    else { fetchEvents(selectedSid) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSid])

  useEffect(() => {
    if (selectedSid) {
      const s = sessions.find(x => x.id === selectedSid)
      setSelectedSession(s || null)
    }
  }, [sessions, selectedSid])

  // WebSocket
  useEffect(() => {
    const ws = new WebSocket(`ws://${location.host}/ws`)
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type !== 'agent_event') return
        if (data.session_id !== selectedSid) {
          if (data.kind === 'user_input') fetchSessions()
          return
        }

        // 流式事件: 追加到最后一个同类流式事件
        if (data.kind === 'stream_reasoning' || data.kind === 'stream_content') {
          autoScrollRef.current = true
          setEvents(prev => {
            const last = prev[prev.length - 1] as any
            if (last && last.kind === data.kind) {
              return [...prev.slice(0, -1), {
                ...last,
                content: { text: (last.content?.text || '') + data.content.text }
              }]
            }
            return [...prev, data]
          })
          return
        }

        // 完整事件: 替换最后一个流式事件
        if (data.kind === 'reasoning' || data.kind === 'assistant') {
          const streamKind = data.kind === 'reasoning' ? 'stream_reasoning' : 'stream_content'
          setEvents(prev => {
            const last = prev[prev.length - 1] as any
            if (last && last.kind === streamKind) {
              return [...prev.slice(0, -1), data]
            }
            return [...prev, data]
          })
          autoScrollRef.current = true
          return
        }

        // 其他事件直接追加
        autoScrollRef.current = true
        setEvents(prev => [...prev.slice(-300), data])
      } catch (e) {
        console.error('ws message parse error:', e)
      }
    }
    return () => ws.close()
  }, [selectedSid, fetchSessions])

  // 只在用户在底部时自动滚动
  useEffect(() => {
    if (autoScrollRef.current && atBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
    autoScrollRef.current = false
  }, [events])

  const handleScroll = () => {
    if (scrollRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
      atBottomRef.current = scrollHeight - scrollTop - clientHeight < 80
    }
  }

  // Tree
  const roots = sessions.filter(s => !s.parent_id)
  const buildTree = (s: Session): any => {
    const children = sessions.filter(c => c.parent_id === s.id)
    const labels: Record<string, string> = { master: '主控', auto: '巡检', fix: '修复' }
    const colors: Record<string, string> = { master: 'processing', auto: 'blue', fix: 'warning' }
    return {
      key: s.id,
      title: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tag color={colors[s.type]} style={{ margin: 0, fontSize: 11 }}>{labels[s.type] || s.type}</Tag>
          <Text style={{ fontSize: 11, color: '#94a3b8' }}>{fmtTime(s.started_at)}</Text>
          {!s.ended_at && <Badge status="processing" />}
        </span>
      ),
      children: children.map(buildTree),
    }
  }
  const treeData = roots.map(buildTree)

  const renderClusterSnap = () => {
    if (!clusterSnap) return <Empty description="无集群状态" />
    const services = clusterSnap.services || {}
    return (
      <div>
        <Card size="small" style={{ marginBottom: 16 }}>
          <Statistic
            title="集群整体状态"
            value={clusterSnap.overall_health || 'UNKNOWN'}
            prefix={<DashboardOutlined />}
            valueStyle={{ color: clusterSnap.overall_health === 'GOOD' ? '#22c55e' : '#ef4444' }}
          />
        </Card>
        <Card size="small" title="服务状态">
          {Object.entries(services).map(([name, info]: [string, any]) => (
            <Descriptions key={name} size="small" column={3} bordered style={{ marginBottom: 8 }}
              items={[
                { key: 'name', label: '服务', children: name },
                { key: 'health', label: '健康', children: (
                  <Tag color={info.health === 'GOOD' ? 'success' : 'error'}>{info.health || 'UNKNOWN'}</Tag>
                )},
                { key: 'roles', label: '角色数', children: info.role_count || 0 },
              ]}
            />
          ))}
        </Card>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%', overflow: 'hidden', padding: 4 }}>
      {/* 左侧: Session 树 */}
      <Card
        size="small"
        style={{ width: 260, flexShrink: 0, height: '100%', display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'hidden', minHeight: 0, padding: '8px 12px' } }}
        title={<Badge status={connected ? 'success' : 'default'} text={connected ? '实时' : '离线'} />}
      >
        <div style={{ height: '100%', overflow: 'auto' }}>
          {sessions.length === 0 ? <Empty description="无会话" /> : (
            <Tree
              treeData={treeData}
              selectedKeys={selectedSid ? [selectedSid] : []}
              onSelect={(keys) => keys[0] && setSelectedSid(keys[0] as string)}
              defaultExpandAll
              showLine
            />
          )}
        </div>
      </Card>

      {/* 右侧: 事件流 */}
      <Card
        size="small"
        style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflow: 'hidden', minHeight: 0, padding: 0 } }}
        title={selectedSession
          ? `${selectedSession.type === 'master' ? '主控' : selectedSession.type === 'auto' ? '巡检' : '修复'} ${fmtTime(selectedSession.started_at)}`
          : '请选择会话'}
      >
        <div ref={scrollRef} onScroll={handleScroll} style={{ height: '100%', overflow: 'auto', padding: '8px 16px' }}>
          {selectedSession?.type === 'master' ? (
            renderClusterSnap()
          ) : loading ? (
            <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
          ) : events.length === 0 ? (
            <Empty description="无事件" />
          ) : (
            <Timeline items={events.map((evt: any, i) => {
              const cfg = KIND_CFG[evt.kind] || { label: evt.kind, color: 'gray', icon: null }
              const content = evt.content || {}
              const isMD = MD_KINDS.has(evt.kind)
              const isTool = TOOL_KINDS.has(evt.kind)
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
        </div>
      </Card>
    </div>
  )
}

export default AgentActivity
