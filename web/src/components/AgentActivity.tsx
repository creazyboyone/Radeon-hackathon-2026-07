import { useEffect, useRef, useState, useCallback } from 'react'
import { Tree, Timeline, Card, Tag, Typography, Badge, Spin, Button, Collapse } from 'antd'
import {
  ApiOutlined, BulbOutlined, ToolOutlined, CheckCircleOutlined,
  RobotOutlined, ThunderboltOutlined
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const { Text, Paragraph } = Typography

interface Session {
  id: string
  parent_id: string | null
  type: string
  status: string
  trigger: string
  started_at: number
  ended_at: number
}

interface AgentEvent {
  type: string
  session_id: string
  kind: string
  content: any
}

interface HistEvent {
  seq: number
  kind: string
  content: any
  ts: number
}

const KIND_CONFIG: Record<string, { label: string; color: string; icon: any }> = {
  reasoning:    { label: 'THINKING',  color: 'purple',  icon: <BulbOutlined /> },
  assistant:    { label: 'RESPONSE',  color: 'blue',    icon: <RobotOutlined /> },
  tool_call:    { label: 'TOOL CALL',  color: 'orange',  icon: <ToolOutlined /> },
  tool_result:  { label: 'RESULT',     color: 'green',   icon: <ApiOutlined /> },
  user_input:   { label: 'INPUT',      color: 'default', icon: <ThunderboltOutlined /> },
  final_answer: { label: 'DONE',       color: 'success', icon: <CheckCircleOutlined /> },
}

function extractCallInfo(name: string, args: any): string {
  if (!args) return ''
  const parts: string[] = []
  if (args.service) parts.push(args.service)
  if (args.node) parts.push(args.node)
  if (args.metric) parts.push(args.metric)
  if (args.filter) parts.push(`filter=${args.filter}`)
  if (args.query) parts.push(`"${args.query}"`)
  if (args.action) parts.push(args.action)
  if (args.reason) parts.push(`(${args.reason.slice(0, 80)})`)
  return parts.join(' ')
}

function extractResultSummary(name: string, result: any): string {
  if (!result || typeof result !== 'object') return ''
  if (result.error) return `Error: ${result.error}`
  if (result.overall_health) return `health=${result.overall_health}, roles=${result.role_count ?? 0}`
  if (result.count !== undefined) return `alerts=${result.count}`
  if (result.total_errors !== undefined) return `errors=${result.total_errors}, nodes=${result.nodes_checked ?? 0}`
  if (result.matches !== undefined) return `matches=${result.matches}`
  if (result.result) return `result=${result.result}`
  if (result.command_id) return `cmd=${result.command_id}, result=${result.result ?? '?'}`
  if (result.output) return (result.output as string).slice(0, 100).replace(/\n/g, ' ')
  if (result.nodes) return `nodes=${Object.keys(result.nodes).join(',')}`
  return JSON.stringify(result).slice(0, 100)
}

function AgentActivity() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [selectedSid, setSelectedSid] = useState<string>('')
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
        if (data.session_id === selectedSid) {
          setEvents(prev => [...prev.slice(-300), data])
        }
        if (data.kind === 'user_input') fetchSessions()
      } catch {}
    }
    return () => ws.close()
  }, [selectedSid, fetchSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  // 构建 Tree 数据
  const roots = sessions.filter(s => !s.parent_id)
  const buildTreeData = (s: Session): any => {
    const children = sessions.filter(c => c.parent_id === s.id)
    const typeLabel = s.type === 'master' ? 'Master' : s.type === 'auto' ? 'Auto' : 'Fix'
    const typeColor = s.type === 'master' ? 'processing' : s.type === 'auto' ? 'blue' : 'warning'
    return {
      key: s.id,
      title: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tag color={typeColor} style={{ margin: 0, fontSize: 11 }}>{typeLabel}</Tag>
          <Text code style={{ fontSize: 11 }}>{s.id.slice(0, 8)}</Text>
          {!s.ended_at && <Badge status="processing" />}
        </span>
      ),
      children: children.map(buildTreeData),
    }
  }
  const treeData = roots.map(buildTreeData)

  return (
    <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 140px)' }}>
      {/* 左侧: Session 树 */}
      <Card
        size="small"
        style={{ width: 280, flexShrink: 0, overflow: 'auto' }}
        title={
          <span>
            <Badge status={connected ? 'success' : 'default'} text={connected ? 'Live' : 'Offline'} />
          </span>
        }
      >
        <Tree
          treeData={treeData}
          selectedKeys={selectedSid ? [selectedSid] : []}
          onSelect={(keys) => keys[0] && setSelectedSid(keys[0] as string)}
          defaultExpandAll
          showLine
        />
      </Card>

      {/* 右侧: 事件 Timeline */}
      <Card
        size="small"
        style={{ flex: 1, overflow: 'auto' }}
        title={
          <span>
            {selectedSid
              ? `Session ${selectedSid.slice(0, 8)} (${events.length} events)`
              : 'Select a session'}
          </span>
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : events.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#888' }}>No events</div>
        ) : (
          <Timeline
            items={events.map((evt: any, i) => {
              const cfg = KIND_CONFIG[evt.kind] || { label: evt.kind, color: 'gray', icon: null }
              const content = evt.content || {}
              const isMarkdown = ['reasoning', 'assistant', 'final_answer', 'user_input'].includes(evt.kind)
              const isTool = ['tool_call', 'tool_result'].includes(evt.kind)

              let summary = ''
              if (evt.kind === 'tool_call') {
                summary = `${content.name}(${extractCallInfo(content.name, content.args || {})})`
              } else if (evt.kind === 'tool_result') {
                summary = extractResultSummary(content.name || '', content.result || content)
              } else if (content.text) {
                summary = content.text
              } else if (content.tool_calls?.length) {
                summary = content.tool_calls.map((tc: any) => tc.name).join(', ')
              }

              return {
                key: i,
                color: cfg.color as any,
                dot: cfg.icon,
                children: (
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <Tag color={cfg.color}>{cfg.label}</Tag>
                    </div>
                    {isMarkdown ? (
                      <div className="markdown-body" style={{ fontSize: 13, lineHeight: 1.6 }}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary || ''}</ReactMarkdown>
                      </div>
                    ) : isTool ? (
                      <div>
                        <Paragraph style={{ margin: 0 }}>
                          <Text style={{ fontSize: 13 }}>{summary}</Text>
                        </Paragraph>
                        <Collapse
                          ghost
                          size="small"
                          items={[{
                            key: 'json',
                            label: 'JSON',
                            children: (
                              <pre style={{ fontSize: 11, color: '#888', overflow: 'auto', maxHeight: 300, background: '#0d1117', padding: 10, borderRadius: 6 }}>
                                {JSON.stringify(content, null, 2)}
                              </pre>
                            )
                          }]}
                        />
                      </div>
                    ) : (
                      <Text style={{ fontSize: 13 }}>{summary}</Text>
                    )}
                  </div>
                ),
              }
            })}
          />
        )}
        <div ref={bottomRef} />
      </Card>
    </div>
  )
}

export default AgentActivity
